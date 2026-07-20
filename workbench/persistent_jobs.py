#!/usr/bin/env python3
"""API-facing service for persistent local Workbench jobs."""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

from workbench.job_store import JobConflictError, JobNotFoundError, JobStore

JOB_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _managed_path(repo_root: Path, value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    resolved = path.resolve()
    managed_root = (repo_root / "workbench-runs").resolve()
    try:
        if os.path.commonpath([str(managed_root), str(resolved)]) != str(managed_root):
            raise JobConflictError(f"{label} must remain under workbench-runs")
    except OSError as exc:
        raise JobConflictError(f"Could not resolve {label}") from exc
    return resolved


class PersistentJobService:
    """Validate paths and expose persistent jobs without HTTP dependencies."""

    def __init__(self, repo_root: Path, database_path: Path | None = None):
        self.repo_root = repo_root.resolve()
        configured_root = os.environ.get(
            "WRF_CHAMMER_PERSISTENT_ROOT", "workbench-runs/persistent"
        )
        self.persistent_root = _managed_path(
            self.repo_root, configured_root, "persistent job root"
        )
        self.persistent_root.mkdir(parents=True, exist_ok=True)
        configured_database: str | Path = database_path or os.environ.get(
            "WRF_CHAMMER_JOB_DATABASE", "workbench-runs/jobs.sqlite3"
        )
        self.database_path = _managed_path(
            self.repo_root, configured_database, "persistent job database"
        )
        self.store = JobStore(self.database_path)

    def exists(self, job_id: str) -> bool:
        return self.store.exists(job_id)

    def create(
        self,
        config: dict[str, Any],
        *,
        start: bool = True,
        priority: int = 0,
    ) -> dict[str, Any]:
        job_id = config.get("id")
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            raise JobConflictError(f"Invalid persistent job id: {job_id!r}")
        run_root = self.persistent_root / job_id
        return self.store.create_job(
            job_id,
            copy.deepcopy(config),
            str(run_root.relative_to(self.repo_root)),
            enqueue=start,
            priority=priority,
        )

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_jobs(limit=limit)

    def get(self, job_id: str) -> dict[str, Any]:
        return self.store.get_job(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.store.request_cancel(job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        return self.store.retry(job_id)

    def events(self, job_id: str, *, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.events(job_id, after_id=after_id, limit=limit)

    def artifacts(self, job_id: str) -> list[dict[str, Any]]:
        return self.store.artifacts(job_id)


__all__ = ["JobConflictError", "JobNotFoundError", "PersistentJobService"]
