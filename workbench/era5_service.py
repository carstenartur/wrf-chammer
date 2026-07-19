#!/usr/bin/env python3
"""Credential-safe ERA5 planning and managed-cache service.

The service plans and materializes request files only. It never starts a CDS
network request and never returns credential values or undisclosed absolute
cache paths to API consumers.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from workbench.era5_planner import (
    Era5PlanningError,
    build_era5_plan,
    build_era5_plan_from_job,
)

_CACHE_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class Era5DataServiceError(RuntimeError):
    """A safe, user-facing ERA5 service error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


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


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


class Era5DataService:
    """Plan real ERA5 input and manage content-addressed request files."""

    def __init__(self, repo_root: Path, cache_root: Path | None = None):
        self.repo_root = repo_root.resolve()
        self.cache_root = (cache_root or self._configured_cache_root()).resolve()

    def _configured_cache_root(self) -> Path:
        configured = os.environ.get("WRF_CHAMMER_ERA5_CACHE_ROOT")
        if not configured:
            return self.repo_root / ".era5-cache"
        path = Path(configured).expanduser()
        return path if path.is_absolute() else self.repo_root / path

    def display_cache_root(self) -> str:
        try:
            return str(self.cache_root.relative_to(self.repo_root)) or "."
        except ValueError:
            return "configured-external-cache"

    @staticmethod
    def credential_status() -> dict[str, Any]:
        configured = bool(os.environ.get("CDSAPI_KEY")) or (Path.home() / ".cdsapirc").is_file()
        return {
            "configured": configured,
            "status": "ready" if configured else "warning",
            "summary": (
                "ERA5/CDS credentials are configured."
                if configured
                else "ERA5/CDS credentials were not found."
            ),
            "remediation": (
                None
                if configured
                else "Create ~/.cdsapirc or configure CDSAPI_KEY before downloading real ERA5 data."
            ),
        }

    def status(self, latest_preview: dict[str, Any] | None) -> dict[str, Any]:
        plans: list[Path] = []
        if self.cache_root.is_dir():
            try:
                plans = [
                    path
                    for path in self.cache_root.iterdir()
                    if path.is_dir() and _CACHE_KEY_RE.fullmatch(path.name)
                ]
            except OSError:
                plans = []
        writable_base = _nearest_existing_parent(self.cache_root)
        valid_preview = isinstance(latest_preview, dict) and bool(latest_preview.get("valid"))
        return {
            "ok": True,
            "credentials": self.credential_status(),
            "cache": {
                "path": self.display_cache_root(),
                "exists": self.cache_root.is_dir(),
                "writable": os.access(writable_base, os.W_OK),
                "plan_count": len(plans),
                "size_bytes": _directory_size(self.cache_root),
            },
            "wizard_preview": {
                "available": valid_preview,
                "job_id": (
                    latest_preview.get("config", {}).get("id")
                    if valid_preview
                    else None
                ),
            },
        }

    @staticmethod
    def require_preview(latest_preview: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(latest_preview, dict) or not latest_preview.get("valid"):
            raise Era5DataServiceError(
                "wizard_preview_required",
                "Create a valid guided simulation preview before planning ERA5 data.",
            )
        config = latest_preview.get("config")
        if not isinstance(config, dict):
            raise Era5DataServiceError(
                "wizard_preview_invalid",
                "The latest guided simulation preview does not contain a valid job configuration.",
            )
        return config

    @staticmethod
    def _planning_options(request: dict[str, Any]) -> tuple[int, float]:
        interval = request.get("interval_hours", 1)
        if isinstance(interval, bool) or not isinstance(interval, int):
            raise Era5PlanningError(["interval_hours must be an integer between 1 and 24"])
        margin = request.get("margin_degrees", 1.0)
        if isinstance(margin, bool) or not isinstance(margin, (int, float)):
            raise Era5PlanningError(["margin_degrees must be a number"])
        return interval, float(margin)

    def plan(
        self,
        request: dict[str, Any],
        latest_preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise Era5PlanningError(["request must be a JSON object"])
        interval, margin = self._planning_options(request)

        source = request.get("source")
        job = request.get("job")
        if source == "latest-wizard-preview":
            job = self.require_preview(latest_preview)

        if isinstance(job, dict):
            plan = build_era5_plan_from_job(
                job,
                cache_root=self.cache_root,
                interval_hours=interval,
                margin_degrees=margin,
            )
        elif isinstance(request.get("period"), dict) and request.get("bounds") is not None:
            plan = build_era5_plan(
                period=request["period"],
                bounds=request["bounds"],
                cache_root=self.cache_root,
                interval_hours=interval,
                margin_degrees=margin,
            )
        else:
            raise Era5PlanningError([
                "Provide a job, period plus bounds, or source='latest-wizard-preview'."
            ])

        display_root = self.display_cache_root()
        plan["cache"]["root"] = display_root
        plan["cache"]["plan_directory"] = f"{display_root}/{plan['plan_key']}"
        return plan

    def prepare(
        self,
        request: dict[str, Any],
        latest_preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plan = self.plan(request, latest_preview)
        plan_directory = self.cache_root / plan["plan_key"]
        _atomic_json(plan_directory / "era5-plan.json", plan)
        _atomic_json(plan_directory / "era5-download-config.json", plan["download_config"])

        display_root = self.display_cache_root()
        return {
            "ok": True,
            "plan": plan,
            "prepared": {
                "plan": f"{display_root}/{plan['plan_key']}/era5-plan.json",
                "download_config": f"{display_root}/{plan['plan_key']}/era5-download-config.json",
                "download_started": False,
            },
        }
