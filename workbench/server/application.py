#!/usr/bin/env python3
"""Primary local WRF Workbench application with persistent simulation APIs."""

from __future__ import annotations

import argparse
import signal
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from workbench.era5_credential_service import Era5CredentialValidationService
from workbench.era5_download_manager import Era5DownloadManager
from workbench.server._application_base import (
    WorkbenchApplicationHandler as _BaseWorkbenchApplicationHandler,
    build_arg_parser,
)
from workbench.server.server import ApiError, WorkbenchApiServer
from workbench.simulation_store import SimulationStore, SimulationStoreError

_SIMULATIONS_PATH = "/api/simulations"


class WorkbenchApplicationHandler(_BaseWorkbenchApplicationHandler):
    """Extend the established Workbench API with persistent simulation records."""

    def _wizard_preview(self, request: dict[str, Any]) -> dict[str, Any]:
        """Normalize the UI label to the validated ERA5/WRF product mode."""

        normalized = dict(request)
        if normalized.get("mode") == "real-data":
            normalized["mode"] = "era5-wrf"
        return super()._wizard_preview(normalized)

    def _handle_static_path(self, path: str) -> bool:
        if path == "/web/simulation-job-queue.js":
            self._send_static_file(
                self.server.web_dir / "simulation-job-queue.js",
                "application/javascript; charset=utf-8",
            )
            return True
        return super()._handle_static_path(path)

    def _simulation_store(self) -> SimulationStore:
        store = getattr(self.server, "simulation_store", None)
        if not isinstance(store, SimulationStore):
            store = SimulationStore(
                self.server.repo_root,
                self._pipeline_specification_service(),
            )
            self.server.simulation_store = store
        return store

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != _SIMULATIONS_PATH and not path.startswith(
            f"{_SIMULATIONS_PATH}/"
        ):
            super().do_GET()
            return
        try:
            self._require_local_client()
            if path == _SIMULATIONS_PATH:
                jobs = self._simulation_store().list_jobs()
                payload = {"ok": True, "count": len(jobs), "simulations": jobs}
            else:
                suffix = path[len(f"{_SIMULATIONS_PATH}/") :]
                if suffix.endswith("/events"):
                    job_id = unquote(suffix[: -len("/events")])
                    job = self._simulation_store().get_job(job_id)
                    payload = {
                        "ok": True,
                        "count": len(job["events"]),
                        "events": job["events"],
                    }
                elif suffix.endswith("/artifacts"):
                    job_id = unquote(suffix[: -len("/artifacts")])
                    job = self._simulation_store().get_job(job_id)
                    payload = {
                        "ok": True,
                        "count": len(job["artifacts"]),
                        "artifacts": job["artifacts"],
                    }
                else:
                    job_id = unquote(suffix)
                    payload = {
                        "ok": True,
                        "simulation": self._simulation_store().get_job(job_id),
                    }
            self._send_json(HTTPStatus.OK, payload)
        except SimulationStoreError as exc:
            self._send_simulation_error(exc)
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR, "application_error", str(exc)
            )

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != _SIMULATIONS_PATH and not path.startswith(
            f"{_SIMULATIONS_PATH}/"
        ):
            super().do_POST()
            return
        try:
            self._require_local_client()
            if path == _SIMULATIONS_PATH:
                request = self._read_json()
                specification_key = request.get("specification_key")
                if not isinstance(specification_key, str):
                    raise SimulationStoreError(
                        "invalid_simulation_request",
                        "A valid immutable specification key is required.",
                    )
                simulation = self._simulation_store().create_job(specification_key)
                status = HTTPStatus.CREATED
            else:
                suffix = path[len(f"{_SIMULATIONS_PATH}/") :]
                parts = suffix.split("/")
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    raise SimulationStoreError(
                        "job_not_found", "Simulation job not found."
                    )
                job_id = unquote(parts[0])
                action = parts[1]
                if action == "enqueue":
                    simulation = self._simulation_store().enqueue_job(job_id)
                    status = HTTPStatus.ACCEPTED
                elif action == "cancel":
                    simulation = self._simulation_store().request_cancel(job_id)
                    status = HTTPStatus.ACCEPTED
                elif action == "retry":
                    simulation = self._simulation_store().retry_job(job_id)
                    status = HTTPStatus.CREATED
                else:
                    raise SimulationStoreError(
                        "job_not_found", "Simulation job not found."
                    )
            self._send_json(status, {"ok": True, "simulation": simulation})
        except SimulationStoreError as exc:
            self._send_simulation_error(exc)
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR, "application_error", str(exc)
            )

    def _send_simulation_error(self, exc: SimulationStoreError) -> None:
        if exc.code in {"job_not_found", "specification_not_found"}:
            status = HTTPStatus.NOT_FOUND
        elif exc.code in {
            "invalid_simulation_request",
            "specification_integrity_error",
            "invalid_artifact",
            "invalid_measurement",
            "invalid_progress",
            "invalid_error",
        }:
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        elif exc.code in {
            "job_not_queueable",
            "job_not_retryable",
            "retry_specification_mismatch",
            "job_not_cancelling",
            "job_state_invalid",
            "step_not_running",
            "step_not_current",
            "step_not_found",
        }:
            status = HTTPStatus.CONFLICT
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_error(status, exc.code, exc.message)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
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
