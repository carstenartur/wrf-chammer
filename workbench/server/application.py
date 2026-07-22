#!/usr/bin/env python3
"""Workbench application with simulation-aware ERA5 cache management."""

from __future__ import annotations

import signal
import threading
from http import HTTPStatus
from typing import Any

import workbench.server._application_result_base as _core
from workbench.era5_cache_service import (
    CacheCoordinatedEra5DownloadManager,
    Era5CacheService,
    Era5CacheServiceError,
)
from workbench.era5_credential_service import Era5CredentialValidationService
from workbench.era5_download_manager import Era5DownloadManager
from workbench.server._application_result_base import *  # noqa: F401,F403
from workbench.server.server import WorkbenchApiServer
from workbench.simulation_store import SimulationStoreError


class WorkbenchApplicationHandler(_core.WorkbenchApplicationHandler):
    """Use the persistent simulation store for cache dependency warnings."""

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
