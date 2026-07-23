#!/usr/bin/env python3
"""Workbench application with simulation-aware cache and run manifests."""

from __future__ import annotations

import signal
import threading
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote, urlparse

import workbench.server._application_result_base as _core
from workbench.era5_cache_service import (
    CacheCoordinatedEra5DownloadManager,
    Era5CacheService,
    Era5CacheServiceError,
)
from workbench.era5_credential_service import Era5CredentialValidationService
from workbench.era5_download_manager import Era5DownloadManager
from workbench.server._application_result_base import *  # noqa: F401,F403
from workbench.server.server import ApiError, WorkbenchApiServer
from workbench.simulation_run_manifest import (
    SimulationRunManifestError,
    SimulationRunManifestService,
)
from workbench.simulation_store import SimulationStoreError


class WorkbenchApplicationHandler(_core.WorkbenchApplicationHandler):
    """Use persistent simulations for cache dependencies and reproducible manifests."""

    def _era5_cache_service(self) -> Era5CacheService:
        service = getattr(self.server, "era5_cache_service", None)
        if not isinstance(service, Era5CacheService):
            manager = self._era5_download_manager()
            if not isinstance(manager, CacheCoordinatedEra5DownloadManager):
                raise Era5CacheServiceError(
                    "cache_manager_unavailable",
                    "The ERA5 cache manager is not available.",
                )
            service = Era5CacheService(
                self._era5_service(),
                manager,
                self._simulation_store(),
            )
            self.server.era5_cache_service = service
        return service

    def _simulation_run_manifest_service(self) -> SimulationRunManifestService:
        service = getattr(self.server, "simulation_run_manifest_service", None)
        if not isinstance(service, SimulationRunManifestService):
            service = SimulationRunManifestService(
                self.server.repo_root,
                self._simulation_store(),
            )
            self.server.simulation_run_manifest_service = service
        return service

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path.startswith("/api/simulations/") and path.endswith("/run-manifest"):
            self._handle_run_manifest(path)
            return
        super().do_GET()

    def _handle_run_manifest(self, path: str) -> None:
        try:
            self._require_local_client()
            suffix = path[len("/api/simulations/") : -len("/run-manifest")]
            job_id = unquote(suffix.rstrip("/"))
            if not job_id or "/" in job_id:
                raise SimulationRunManifestError(
                    "run_manifest_not_found", "Simulation run manifest was not found."
                )
            manifest = self._simulation_run_manifest_service().manifest(job_id)
            self._send_json(HTTPStatus.OK, {"ok": True, "manifest": manifest})
        except SimulationRunManifestError as exc:
            self._send_run_manifest_error(exc)
        except SimulationStoreError as exc:
            self._send_simulation_error(exc)
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "run_manifest_error",
                f"Simulation run manifest failed ({type(exc).__name__}).",
            )

    def _send_run_manifest_error(self, exc: SimulationRunManifestError) -> None:
        if exc.code == "run_manifest_not_found":
            status = HTTPStatus.NOT_FOUND
        elif exc.code == "run_manifest_integrity_error":
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        elif exc.code == "run_manifest_unavailable":
            status = HTTPStatus.SERVICE_UNAVAILABLE
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_error(status, exc.code, exc.message)

    def _send_simulation_error(self, exc: SimulationStoreError) -> None:
        if exc.code == "input_dataset_unavailable":
            self._send_error(HTTPStatus.CONFLICT, exc.code, exc.message)
            return
        super()._send_simulation_error(exc)


_core.WorkbenchApplicationHandler = WorkbenchApplicationHandler


def main(argv: list[str] | None = None) -> int:
    args = _core.build_arg_parser().parse_args(argv)
    server = WorkbenchApiServer(
        (args.host, args.port), WorkbenchApplicationHandler, args.repo_root
    )

    def request_shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(
            target=server.shutdown,
            name="workbench-shutdown",
            daemon=True,
        ).start()

    for signal_name in ("SIGTERM", "SIGINT"):
        candidate = getattr(signal, signal_name, None)
        if candidate is not None:
            signal.signal(candidate, request_shutdown)

    print(
        f"WRF Workbench available at http://{args.host}:{args.port}/", flush=True
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        credential_service = getattr(
            server, "era5_credential_validation_service", None
        )
        if isinstance(credential_service, Era5CredentialValidationService):
            credential_service.close()
        manager = getattr(server, "era5_download_manager", None)
        if isinstance(manager, Era5DownloadManager):
            manager.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
