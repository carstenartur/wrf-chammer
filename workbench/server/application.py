#!/usr/bin/env python3
"""Primary local WRF Workbench application server."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workbench.core.catalogue import (  # noqa: E402
    CatalogueError,
    EventNotFoundError,
    build_job_config,
)
from workbench.domain_planner import (  # noqa: E402
    DomainPlanningError,
    available_profiles,
    plan_domain,
)
from workbench.era5_download_manager import (  # noqa: E402
    Era5DownloadManager,
    Era5DownloadManagerError,
)
from workbench.era5_planner import Era5PlanningError  # noqa: E402
from workbench.era5_service import Era5DataService, Era5DataServiceError  # noqa: E402
from workbench.readiness import collect_readiness  # noqa: E402
from workbench.server.server import ApiError, WorkbenchApiHandler, WorkbenchApiServer  # noqa: E402

_ERA5_DOWNLOADS_PATH = "/api/data/era5/downloads"


class WorkbenchApplicationHandler(WorkbenchApiHandler):
    def _handle_static_path(self, path: str) -> bool:
        if path == "/web/era5-download-control.js":
            self._send_static_file(
                self.server.web_dir / "era5-download-control.js",
                "application/javascript; charset=utf-8",
            )
            return True
        return super()._handle_static_path(path)

    def _era5_service(self) -> Era5DataService:
        service = getattr(self.server, "era5_data_service", None)
        if not isinstance(service, Era5DataService):
            service = Era5DataService(self.server.repo_root)
            self.server.era5_data_service = service
        return service

    def _era5_download_manager(self) -> Era5DownloadManager:
        manager = getattr(self.server, "era5_download_manager", None)
        if not isinstance(manager, Era5DownloadManager):
            manager = Era5DownloadManager(self.server.repo_root, self._era5_service())
            self.server.era5_download_manager = manager
        return manager

    def _latest_wizard_preview(self) -> dict[str, Any] | None:
        preview = getattr(self.server, "latest_wizard_preview", None)
        return preview if isinstance(preview, dict) else None

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        supported = {
            "/api/readiness",
            "/api/domain/profiles",
            "/api/wizard/latest",
            "/api/data/era5/status",
            _ERA5_DOWNLOADS_PATH,
        }
        if path not in supported and not path.startswith(f"{_ERA5_DOWNLOADS_PATH}/"):
            super().do_GET()
            return
        try:
            self._require_local_client()
            if path == "/api/readiness":
                payload = collect_readiness(self.server.repo_root)
            elif path == "/api/domain/profiles":
                payload = {"ok": True, "profiles": available_profiles()}
            elif path == "/api/wizard/latest":
                latest = self._latest_wizard_preview()
                payload = {"ok": True, "available": latest is not None, "preview": latest}
            elif path == "/api/data/era5/status":
                payload = self._era5_service().status(self._latest_wizard_preview())
            elif path == _ERA5_DOWNLOADS_PATH:
                downloads = self._era5_download_manager().list()
                payload = {"ok": True, "count": len(downloads), "downloads": downloads}
            else:
                payload = self._handle_get_era5_download(path)
            self._send_json(HTTPStatus.OK, payload)
        except Era5DownloadManagerError as exc:
            self._send_download_error(exc)
        except Era5DataServiceError as exc:
            self._send_era5_service_error(exc)
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "application_error", str(exc))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        supported = {
            "/api/domain/plan",
            "/api/wizard/preview",
            "/api/data/era5/plan",
            "/api/data/era5/prepare",
            _ERA5_DOWNLOADS_PATH,
        }
        is_download_action = path.startswith(f"{_ERA5_DOWNLOADS_PATH}/") and (
            path.endswith("/cancel") or path.endswith("/retry")
        )
        if path not in supported and not is_download_action:
            super().do_POST()
            return
        try:
            self._require_local_client()
            request = self._read_json()
            if path == "/api/domain/plan":
                payload = plan_domain(request)
                status = HTTPStatus.OK
            elif path == "/api/wizard/preview":
                payload = self._wizard_preview(request)
                status = HTTPStatus.OK
            elif path == "/api/data/era5/plan":
                payload = self._era5_service().plan(request, self._latest_wizard_preview())
                status = HTTPStatus.OK
            elif path == "/api/data/era5/prepare":
                payload = self._era5_service().prepare(request, self._latest_wizard_preview())
                status = HTTPStatus.OK
            elif path == _ERA5_DOWNLOADS_PATH:
                download = self._era5_download_manager().start(
                    request, self._latest_wizard_preview()
                )
                payload = {"ok": True, "download": download}
                status = HTTPStatus.ACCEPTED
            else:
                payload = self._handle_post_era5_download(path)
                status = HTTPStatus.ACCEPTED
            self._send_json(status, payload)
        except (DomainPlanningError, Era5PlanningError) as exc:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "valid": False, "errors": exc.errors},
            )
        except Era5DownloadManagerError as exc:
            self._send_download_error(exc)
        except Era5DataServiceError as exc:
            self._send_era5_service_error(exc)
        except (CatalogueError, EventNotFoundError) as exc:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "valid": False, "errors": [str(exc)]},
            )
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "application_error", str(exc))

    def _handle_get_era5_download(self, path: str) -> dict[str, Any]:
        suffix = path[len(f"{_ERA5_DOWNLOADS_PATH}/"):]
        if suffix.endswith("/events"):
            job_id = unquote(suffix[:-len("/events")])
            events = self._era5_download_manager().events(job_id)
            return {"ok": True, "count": len(events), "events": events}
        job_id = unquote(suffix)
        return {"ok": True, "download": self._era5_download_manager().get(job_id)}

    def _handle_post_era5_download(self, path: str) -> dict[str, Any]:
        suffix = path[len(f"{_ERA5_DOWNLOADS_PATH}/"):]
        if suffix.endswith("/cancel"):
            job_id = unquote(suffix[:-len("/cancel")])
            download = self._era5_download_manager().cancel(job_id)
        elif suffix.endswith("/retry"):
            job_id = unquote(suffix[:-len("/retry")])
            download = self._era5_download_manager().retry(job_id)
        else:  # guarded by do_POST
            raise Era5DownloadManagerError("download_not_found", "ERA5 download job not found.")
        return {"ok": True, "download": download}

    def _send_download_error(self, exc: Era5DownloadManagerError) -> None:
        status = (
            HTTPStatus.NOT_FOUND
            if exc.code == "download_not_found"
            else HTTPStatus.CONFLICT
        )
        self._send_error(status, exc.code, exc.message)

    def _send_era5_service_error(self, exc: Era5DataServiceError) -> None:
        status = (
            HTTPStatus.NOT_FOUND
            if exc.code in {"plan_not_found", "plan_not_prepared"}
            else HTTPStatus.CONFLICT
        )
        self._send_error(status, exc.code, exc.message)

    def _wizard_preview(self, request: dict[str, Any]) -> dict[str, Any]:
        event_ref = request.get("event") or "xaver"
        if not isinstance(event_ref, str) or not event_ref.strip():
            raise DomainPlanningError(["event must be a non-empty string"])

        planning_request = request.get("planning")
        if not isinstance(planning_request, dict):
            planning_request = request
        plan = plan_domain(planning_request)

        mode = str(request.get("mode") or "dry-run")
        job_id = str(request.get("job_id") or f"{event_ref.lower()}-map-preview")
        config = build_job_config(
            event_ref,
            mode=mode,
            job_id=job_id,
            catalogue=self._catalogue(),
        )
        config["period"] = {
            "start": plan["period"]["start"],
            "end": plan["period"]["end"],
        }
        planned_domain = plan["domain"]
        config["domain"] = {
            "label": planned_domain["label"],
            "center_lat": planned_domain["center_lat"],
            "center_lon": planned_domain["center_lon"],
            "dx_km": planned_domain["dx_km"],
            "dy_km": planned_domain["dy_km"],
            "e_we": planned_domain["e_we"],
            "e_sn": planned_domain["e_sn"],
        }
        metadata = config.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["domain_source"] = "map-bounds"
            metadata["domain_bounds"] = planned_domain["bounds"]
            metadata["quality_profile"] = plan["quality_profile"]["id"]
            metadata["resource_estimate"] = plan["resources"]

        errors = self._validate_config(config)
        preview = {
            "ok": not errors,
            "valid": not errors,
            "errors": errors,
            "warnings": plan["warnings"],
            "plan": plan,
            "config": config,
        }
        if not errors:
            self.server.latest_wizard_preview = preview
        return preview


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local WRF Workbench application")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root (normally detected automatically)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    server = WorkbenchApiServer((args.host, args.port), WorkbenchApplicationHandler, args.repo_root)

    def request_shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, name="workbench-shutdown", daemon=True).start()

    for signal_name in ("SIGTERM", "SIGINT"):
        candidate = getattr(signal, signal_name, None)
        if candidate is not None:
            signal.signal(candidate, request_shutdown)

    print(f"WRF Workbench available at http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager = getattr(server, "era5_download_manager", None)
        if isinstance(manager, Era5DownloadManager):
            manager.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
