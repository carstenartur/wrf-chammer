#!/usr/bin/env python3
"""Primary local WRF Workbench application server."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

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
from workbench.era5_planner import Era5PlanningError  # noqa: E402
from workbench.era5_service import Era5DataService, Era5DataServiceError  # noqa: E402
from workbench.persistent_jobs import (  # noqa: E402
    JobConflictError,
    JobNotFoundError,
    PersistentJobService,
)
from workbench.readiness import collect_readiness  # noqa: E402
from workbench.server.server import (  # noqa: E402
    JOB_ID_RE,
    ApiError,
    WorkbenchApiHandler,
    WorkbenchApiServer,
)


class WorkbenchApplicationHandler(WorkbenchApiHandler):
    def _era5_service(self) -> Era5DataService:
        service = getattr(self.server, "era5_data_service", None)
        if not isinstance(service, Era5DataService):
            service = Era5DataService(self.server.repo_root)
            self.server.era5_data_service = service
        return service

    def _persistent_jobs(self) -> PersistentJobService:
        service = getattr(self.server, "persistent_job_service", None)
        if not isinstance(service, PersistentJobService):
            service = PersistentJobService(self.server.repo_root)
            self.server.persistent_job_service = service
        return service

    def _latest_wizard_preview(self) -> dict[str, Any] | None:
        preview = getattr(self.server, "latest_wizard_preview", None)
        return preview if isinstance(preview, dict) else None

    @staticmethod
    def _query_int(
        query: dict[str, list[str]],
        name: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        raw = query.get(name, [str(default)])[0]
        try:
            value = int(raw)
        except ValueError as exc:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_query",
                f"{name} must be an integer",
            ) from exc
        return max(minimum, min(value, maximum))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/api/jobs":
            try:
                self._require_local_client()
                limit = self._query_int(query, "limit", 100, 1, 500)
                jobs = self._persistent_jobs().list(limit=limit)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "execution": "persistent",
                        "count": len(jobs),
                        "jobs": jobs,
                    },
                )
            except ApiError as exc:
                self._send_error(exc.status, exc.code, exc.message, exc.details)
            except Exception as exc:  # pragma: no cover
                self._send_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "application_error",
                    str(exc),
                )
            return

        if path.startswith("/api/jobs/"):
            parts = [unquote(part) for part in path.split("/") if part]
            job_id = self._persistent_job_id(path)
            persistent_endpoint = (
                len(parts) == 3
                or (len(parts) == 4 and parts[3] in {"events", "artifacts"})
            )
            if job_id and (
                persistent_endpoint or self._persistent_jobs().exists(job_id)
            ):
                self._handle_persistent_get(path, query, job_id)
                return

        supported = {
            "/api/readiness",
            "/api/domain/profiles",
            "/api/wizard/latest",
            "/api/data/era5/status",
        }
        if path not in supported:
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
                payload = {
                    "ok": True,
                    "available": latest is not None,
                    "preview": latest,
                }
            else:
                payload = self._era5_service().status(
                    self._latest_wizard_preview()
                )
            self._send_json(HTTPStatus.OK, payload)
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "application_error",
                str(exc),
            )

    def _handle_persistent_get(
        self,
        path: str,
        query: dict[str, list[str]],
        job_id: str,
    ) -> None:
        try:
            self._require_local_client()
            parts = [unquote(part) for part in path.split("/") if part]
            service = self._persistent_jobs()
            if len(parts) == 3:
                payload = {"ok": True, "job": service.get(job_id)}
            elif len(parts) == 4 and parts[3] == "events":
                after_id = self._query_int(
                    query,
                    "after_id",
                    0,
                    0,
                    2_147_483_647,
                )
                limit = self._query_int(query, "limit", 200, 1, 1000)
                payload = {
                    "ok": True,
                    "job_id": job_id,
                    "events": service.events(
                        job_id,
                        after_id=after_id,
                        limit=limit,
                    ),
                }
            elif len(parts) == 4 and parts[3] == "artifacts":
                payload = {
                    "ok": True,
                    "job_id": job_id,
                    "artifacts": service.artifacts(job_id),
                }
            else:
                self._send_error(
                    HTTPStatus.NOT_FOUND,
                    "not_found",
                    f"Unknown endpoint: {path}",
                )
                return
            self._send_json(HTTPStatus.OK, payload)
        except JobNotFoundError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, "job_not_found", str(exc))
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "application_error",
                str(exc),
            )

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/api/jobs":
            self._handle_application_create_job()
            return

        if path.startswith("/api/jobs/") and path.endswith("/retry"):
            job_id = self._persistent_job_id(path)
            if job_id:
                self._handle_persistent_action("retry", job_id)
                return

        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            job_id = self._persistent_job_id(path)
            if job_id:
                self._handle_persistent_action("cancel", job_id)
                return

        supported = {
            "/api/domain/plan",
            "/api/wizard/preview",
            "/api/data/era5/plan",
            "/api/data/era5/prepare",
        }
        if path not in supported:
            super().do_POST()
            return
        try:
            self._require_local_client()
            request = self._read_json()
            if path == "/api/domain/plan":
                payload = plan_domain(request)
            elif path == "/api/wizard/preview":
                payload = self._wizard_preview(request)
            elif path == "/api/data/era5/plan":
                payload = self._era5_service().plan(
                    request,
                    self._latest_wizard_preview(),
                )
            else:
                payload = self._era5_service().prepare(
                    request,
                    self._latest_wizard_preview(),
                )
            self._send_json(HTTPStatus.OK, payload)
        except (DomainPlanningError, Era5PlanningError) as exc:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "valid": False, "errors": exc.errors},
            )
        except Era5DataServiceError as exc:
            self._send_error(HTTPStatus.CONFLICT, exc.code, exc.message)
        except (CatalogueError, EventNotFoundError) as exc:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "valid": False, "errors": [str(exc)]},
            )
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "application_error",
                str(exc),
            )

    def _handle_application_create_job(self) -> None:
        try:
            self._require_local_client()
            request = self._read_json()
            execution = request.get("execution", "synchronous")
            if request.get("async") is True:
                execution = "queued"
            if execution in {"queued", "asynchronous", "persistent"}:
                config = self._extract_config(request)
                errors = self._validate_config(config)
                if errors:
                    self._send_json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"ok": False, "valid": False, "errors": errors},
                    )
                    return
                priority = request.get("priority", 0)
                if isinstance(priority, bool) or not isinstance(priority, int):
                    raise ApiError(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_priority",
                        "priority must be an integer",
                    )
                job = self._persistent_jobs().create(
                    config,
                    start=bool(request.get("start", True)),
                    priority=priority,
                )
                self._send_json(
                    HTTPStatus.ACCEPTED
                    if job["state"] == "QUEUED"
                    else HTTPStatus.CREATED,
                    {
                        "ok": True,
                        "execution": "persistent",
                        "job": job,
                    },
                )
                return
            if execution not in {"synchronous", "legacy"}:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_execution",
                    "execution must be 'synchronous' or 'queued'",
                )
            self._legacy_create_from_request(request)
        except JobConflictError as exc:
            self._send_error(HTTPStatus.CONFLICT, "job_conflict", str(exc))
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "application_error",
                str(exc),
            )

    def _legacy_create_from_request(self, request: dict[str, Any]) -> None:
        submitted_config = self._extract_config(request)
        errors = self._validate_config(submitted_config)
        if errors:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "valid": False, "errors": errors},
            )
            return
        job_id = submitted_config["id"]
        if not JOB_ID_RE.match(job_id):
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_job_id",
                f"Invalid job id: {job_id!r}",
            )
        run_dir = self._create_run_dir()
        config = self._server_managed_config(submitted_config, run_dir)
        sanitized_errors = self._validate_config(config)
        if sanitized_errors:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "valid": False, "errors": sanitized_errors},
            )
            return
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix="config-",
            suffix=".json",
            dir=str(run_dir),
            delete=False,
        ) as config_file:
            json.dump(config, config_file, indent=2)
            config_file.write("\n")
            config_path = Path(config_file.name).resolve()
        if not self._is_path_under(config_path, run_dir):
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "bad_config_path",
                "Server-created config file is outside the API run directory",
            )
        self._write_run_metadata(run_dir, job_id, "created")
        self._write_index_record(job_id, run_dir.name, "created")
        if not bool(request.get("start", True)):
            self._send_json(
                HTTPStatus.CREATED,
                {"ok": True, "job": self._job_summary(job_id)},
            )
            return
        result = self._run_workbench(config_path, run_dir)
        self._write_run_metadata(run_dir, job_id, result["status"])
        self._write_index_record(job_id, run_dir.name, result["status"])
        self._send_json(
            HTTPStatus.CREATED
            if result["exit_code"] == 0
            else HTTPStatus.INTERNAL_SERVER_ERROR,
            {
                "ok": result["exit_code"] == 0,
                "job": self._job_summary(job_id),
                "run": result,
            },
        )

    def _handle_persistent_action(self, action: str, job_id: str) -> None:
        try:
            self._require_local_client()
            service = self._persistent_jobs()
            job = (
                service.cancel(job_id)
                if action == "cancel"
                else service.retry(job_id)
            )
            self._send_json(
                HTTPStatus.ACCEPTED,
                {
                    "ok": True,
                    "execution": "persistent",
                    "job": job,
                },
            )
        except JobNotFoundError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, "job_not_found", str(exc))
        except JobConflictError as exc:
            self._send_error(HTTPStatus.CONFLICT, "job_conflict", str(exc))
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "application_error",
                str(exc),
            )

    @staticmethod
    def _persistent_job_id(path: str) -> str | None:
        parts = [unquote(part) for part in path.split("/") if part]
        return (
            parts[2]
            if len(parts) >= 3 and parts[:2] == ["api", "jobs"]
            else None
        )

    def _wizard_preview(self, request: dict[str, Any]) -> dict[str, Any]:
        event_ref = request.get("event") or "xaver"
        if not isinstance(event_ref, str) or not event_ref.strip():
            raise DomainPlanningError(["event must be a non-empty string"])

        planning_request = request.get("planning")
        if not isinstance(planning_request, dict):
            planning_request = request
        plan = plan_domain(planning_request)

        mode = str(request.get("mode") or "dry-run")
        job_id = str(
            request.get("job_id") or f"{event_ref.lower()}-map-preview"
        )
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
    parser = argparse.ArgumentParser(
        description="Run the local WRF Workbench application"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Bind port (default: 8080)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root (normally detected automatically)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    server = WorkbenchApiServer(
        (args.host, args.port),
        WorkbenchApplicationHandler,
        args.repo_root,
    )
    print(
        f"WRF Workbench available at http://{args.host}:{args.port}/",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
