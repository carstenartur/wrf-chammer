#!/usr/bin/env python3
"""Workbench application with integrated checksum-indexed result viewer."""

from __future__ import annotations

import signal
import threading
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote, urlparse

from workbench.era5_credential_service import Era5CredentialValidationService
from workbench.era5_download_manager import Era5DownloadManager
from workbench.server._application_event_base import (
    WorkbenchApplicationHandler as _BaseWorkbenchApplicationHandler,
    build_arg_parser,
)
from workbench.server.server import ApiError, WorkbenchApiServer
from workbench.simulation_result_service import (
    SimulationResultError,
    SimulationResultService,
)
from workbench.simulation_store import SimulationStoreError


class WorkbenchApplicationHandler(_BaseWorkbenchApplicationHandler):
    """Add an integrated viewer for products indexed by a successful job."""

    def _handle_static_path(self, path: str) -> bool:
        scripts = {
            "/web/simulation-result-entry.js": "simulation-result-entry.js",
            "/web/result-viewer-tools.js": "result-viewer-tools.js",
        }
        filename = scripts.get(path)
        if filename:
            self._send_static_file(
                self.server.web_dir / filename,
                "application/javascript; charset=utf-8",
            )
            return True
        return super()._handle_static_path(path)

    def _simulation_result_service(self) -> SimulationResultService:
        service = getattr(self.server, "simulation_result_service", None)
        if not isinstance(service, SimulationResultService):
            service = SimulationResultService(
                self.server.repo_root,
                self._simulation_store(),
            )
            self.server.simulation_result_service = service
        return service

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path.startswith("/api/simulations/") and path.endswith("/results"):
            self._handle_result_manifest(path)
            return
        if path.startswith("/jobs/") and (
            path.endswith("/results") or "/results/" in path
        ):
            self._handle_result_view(path)
            return
        super().do_GET()

    def _handle_result_manifest(self, path: str) -> None:
        try:
            self._require_local_client()
            suffix = path[len("/api/simulations/") : -len("/results")]
            job_id = unquote(suffix.rstrip("/"))
            if not job_id or "/" in job_id:
                raise SimulationResultError(
                    "result_not_found", "Simulation results were not found."
                )
            manifest = self._simulation_result_service().manifest(job_id)
            self._send_json(HTTPStatus.OK, {"ok": True, "results": manifest})
        except SimulationResultError as exc:
            self._send_result_error(exc)
        except SimulationStoreError as exc:
            self._send_simulation_error(exc)
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "result_viewer_error",
                f"Integrated result viewer failed ({type(exc).__name__}).",
            )

    def _handle_result_view(self, path: str) -> None:
        try:
            self._require_local_client()
            suffix = path[len("/jobs/") :]
            parts = suffix.split("/")
            if len(parts) < 2 or parts[1] != "results" or not parts[0]:
                raise SimulationResultError(
                    "result_not_found", "Simulation results were not found."
                )
            job_id = unquote(parts[0])
            if len(parts) == 2:
                self._send_viewer_html(
                    self._simulation_result_service().viewer_html(job_id)
                )
                return
            request_path = unquote("/".join(parts[2:]))
            product, body = self._simulation_result_service().read_product(
                job_id, request_path
            )
            self._send_bytes(HTTPStatus.OK, body, product.content_type)
        except SimulationResultError as exc:
            self._send_result_error(exc)
        except SimulationStoreError as exc:
            self._send_simulation_error(exc)
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "result_viewer_error",
                f"Integrated result viewer failed ({type(exc).__name__}).",
            )

    def _send_viewer_html(self, body: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_result_error(self, exc: SimulationResultError) -> None:
        if exc.code == "result_not_found":
            status = HTTPStatus.NOT_FOUND
        elif exc.code == "results_not_ready":
            status = HTTPStatus.CONFLICT
        elif exc.code == "result_integrity_error":
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        elif exc.code == "viewer_unavailable":
            status = HTTPStatus.SERVICE_UNAVAILABLE
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
