#!/usr/bin/env python3
"""Primary local WRF Workbench application server."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
from workbench.era5_planner import (  # noqa: E402
    Era5PlanningError,
    build_era5_plan,
    build_era5_plan_from_job,
)
from workbench.readiness import collect_readiness  # noqa: E402
from workbench.server.server import ApiError, WorkbenchApiHandler, WorkbenchApiServer  # noqa: E402

_CACHE_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _directory_size(root: Path) -> int:
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if not path.is_symlink() and path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


class WorkbenchApplicationHandler(WorkbenchApiHandler):
    def _era5_cache_root(self) -> Path:
        configured = os.environ.get("WRF_CHAMMER_ERA5_CACHE_ROOT")
        if configured:
            path = Path(configured).expanduser()
            if not path.is_absolute():
                path = self.server.repo_root / path
            return path.resolve()
        return (self.server.repo_root / ".era5-cache").resolve()

    def _cache_display_path(self, cache_root: Path) -> str:
        try:
            return str(cache_root.relative_to(self.server.repo_root)) or "."
        except ValueError:
            return "configured-external-cache"

    def _latest_preview(self) -> dict[str, Any]:
        preview = getattr(self.server, "latest_wizard_preview", None)
        if not isinstance(preview, dict) or not preview.get("valid"):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "wizard_preview_required",
                "Create a valid guided simulation preview before planning ERA5 data.",
            )
        return preview

    def _credential_status(self) -> dict[str, Any]:
        readiness = collect_readiness(self.server.repo_root, include_images=False)
        check = next(
            (entry for entry in readiness["checks"] if entry.get("id") == "era5-credentials"),
            {"status": "warning", "summary": "ERA5/CDS credential status is unavailable."},
        )
        return {
            "configured": check.get("status") == "ready",
            "status": check.get("status", "warning"),
            "summary": check.get("summary", "ERA5/CDS credential status is unavailable."),
            "remediation": check.get("remediation"),
        }

    def _era5_status(self) -> dict[str, Any]:
        cache_root = self._era5_cache_root()
        plans = []
        if cache_root.is_dir():
            plans = [path for path in cache_root.iterdir() if path.is_dir() and _CACHE_KEY_RE.fullmatch(path.name)]
        latest = getattr(self.server, "latest_wizard_preview", None)
        return {
            "ok": True,
            "credentials": self._credential_status(),
            "cache": {
                "path": self._cache_display_path(cache_root),
                "exists": cache_root.is_dir(),
                "writable": os.access(cache_root if cache_root.exists() else cache_root.parent, os.W_OK),
                "plan_count": len(plans),
                "size_bytes": _directory_size(cache_root),
            },
            "wizard_preview": {
                "available": isinstance(latest, dict) and bool(latest.get("valid")),
                "job_id": latest.get("config", {}).get("id") if isinstance(latest, dict) else None,
            },
        }

    def _era5_plan_from_request(self, request: dict[str, Any]) -> dict[str, Any]:
        interval = request.get("interval_hours", 1)
        if isinstance(interval, bool) or not isinstance(interval, int):
            raise Era5PlanningError(["interval_hours must be an integer between 1 and 24"])
        margin = request.get("margin_degrees", 1.0)
        if isinstance(margin, bool) or not isinstance(margin, (int, float)):
            raise Era5PlanningError(["margin_degrees must be a number"])

        cache_root = self._era5_cache_root()
        source = request.get("source")
        job = request.get("job")
        if source == "latest-wizard-preview":
            job = self._latest_preview()["config"]

        if isinstance(job, dict):
            plan = build_era5_plan_from_job(
                job,
                cache_root=cache_root,
                interval_hours=interval,
                margin_degrees=float(margin),
            )
        elif isinstance(request.get("period"), dict) and request.get("bounds") is not None:
            plan = build_era5_plan(
                period=request["period"],
                bounds=request["bounds"],
                cache_root=cache_root,
                interval_hours=interval,
                margin_degrees=float(margin),
            )
        else:
            raise Era5PlanningError([
                "Provide a job, period plus bounds, or source='latest-wizard-preview'."
            ])

        plan["cache"]["root"] = self._cache_display_path(cache_root)
        plan["cache"]["plan_directory"] = f"{self._cache_display_path(cache_root)}/{plan['plan_key']}"
        return plan

    def _prepare_era5_plan(self, request: dict[str, Any]) -> dict[str, Any]:
        plan = self._era5_plan_from_request(request)
        cache_root = self._era5_cache_root()
        plan_directory = cache_root / plan["plan_key"]
        _atomic_json(plan_directory / "era5-plan.json", plan)
        _atomic_json(plan_directory / "era5-download-config.json", plan["download_config"])
        display_root = self._cache_display_path(cache_root)
        return {
            "ok": True,
            "plan": plan,
            "prepared": {
                "plan": f"{display_root}/{plan['plan_key']}/era5-plan.json",
                "download_config": f"{display_root}/{plan['plan_key']}/era5-download-config.json",
                "download_started": False,
            },
        }

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
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
                self._send_json(HTTPStatus.OK, collect_readiness(self.server.repo_root))
            elif path == "/api/domain/profiles":
                self._send_json(HTTPStatus.OK, {"ok": True, "profiles": available_profiles()})
            elif path == "/api/wizard/latest":
                latest = getattr(self.server, "latest_wizard_preview", None)
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "available": isinstance(latest, dict), "preview": latest},
                )
            else:
                self._send_json(HTTPStatus.OK, self._era5_status())
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
                payload = self._era5_plan_from_request(request)
            else:
                payload = self._prepare_era5_plan(request)
            self._send_json(HTTPStatus.OK, payload)
        except (DomainPlanningError, Era5PlanningError) as exc:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "valid": False, "errors": exc.errors},
            )
        except (CatalogueError, EventNotFoundError) as exc:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "valid": False, "errors": [str(exc)]},
            )
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "application_error", str(exc))

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
    print(f"WRF Workbench available at http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
