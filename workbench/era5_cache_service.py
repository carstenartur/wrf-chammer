#!/usr/bin/env python3
"""Safe global management of content-addressed ERA5 cache entries."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench.era5_download_manager import Era5DownloadManager
from workbench.era5_service import Era5DataService, Era5DataServiceError

_PLAN_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE_DOWNLOAD_STATUSES = {"QUEUED", "RUNNING", "CANCELLING"}


class Era5CacheServiceError(RuntimeError):
    """A safe, user-facing cache-management error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CacheCoordinatedEra5DownloadManager(Era5DownloadManager):
    """Serialize download creation with cache deletion for the local application."""

    def __init__(self, *args: Any, **kwargs: Any):
        self.cache_operation_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def _enqueue(self, plan_key: str, retry_of: str | None) -> dict[str, Any]:
        with self.cache_operation_lock:
            return super()._enqueue(plan_key, retry_of)


class Era5CacheService:
    """Inspect and delete managed ERA5 plans without exposing host paths."""

    def __init__(
        self,
        data_service: Era5DataService,
        download_manager: CacheCoordinatedEra5DownloadManager,
    ):
        self.data_service = data_service
        self.download_manager = download_manager
        self.cache_root = data_service.cache_root
        self._audit_lock = threading.Lock()

    def list_entries(self) -> list[dict[str, Any]]:
        if not self.cache_root.is_dir():
            return []
        entries: list[dict[str, Any]] = []
        downloads_snapshot = self.download_manager.list()
        try:
            candidates = sorted(self.cache_root.iterdir(), key=lambda path: path.name)
        except OSError:
            return []
        for candidate in candidates:
            if not _PLAN_KEY_RE.fullmatch(candidate.name):
                continue
            try:
                entries.append(
                    self.detail(
                        candidate.name,
                        downloads_snapshot=downloads_snapshot,
                    )
                )
            except Era5CacheServiceError as exc:
                entries.append(self._invalid_entry(candidate.name, exc.message))
        entries.sort(key=lambda entry: str(entry.get("last_used_at") or ""), reverse=True)
        return entries

    def detail(
        self,
        plan_key: str,
        *,
        downloads_snapshot: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        plan_directory = self._canonical_plan_directory(plan_key)
        try:
            plan = self.data_service.load_prepared_plan(plan_key)
        except Era5DataServiceError as exc:
            raise Era5CacheServiceError("cache_entry_invalid", exc.message) from exc

        all_downloads = (
            downloads_snapshot
            if downloads_snapshot is not None
            else self.download_manager.list()
        )
        downloads = [
            self._download_dependency(job)
            for job in all_downloads
            if job.get("plan_key") == plan_key
        ]
        downloads.sort(key=lambda job: str(job.get("created_at") or ""), reverse=True)
        active = [job for job in downloads if job.get("status") in _ACTIVE_DOWNLOAD_STATUSES]
        metrics = self._directory_metrics(plan_directory)
        last_used = self._last_used_at(metrics["modified_at"], downloads)
        period = plan.get("period") if isinstance(plan.get("period"), dict) else {}
        domain = plan.get("domain") if isinstance(plan.get("domain"), dict) else {}
        cache = plan.get("cache") if isinstance(plan.get("cache"), dict) else {}
        provenance = plan.get("provenance") if isinstance(plan.get("provenance"), dict) else {}
        dependency_ids = sorted(
            job["id"] for job in downloads if isinstance(job.get("id"), str)
        )

        return {
            "plan_key": plan_key,
            "status": cache.get("status", "unknown"),
            "period": {
                "start": period.get("start"),
                "end": period.get("end"),
                "time_points": period.get("time_points"),
            },
            "domain": {
                "bounds": domain.get("bounds"),
                "margin_degrees": domain.get("margin_degrees"),
            },
            "coverage": {
                "hits": int(cache.get("hits") or 0),
                "partial_entries": int(cache.get("partial_entries") or 0),
                "total": int(cache.get("total") or 0),
                "percent": float(cache.get("coverage_percent") or 0),
            },
            "storage": {
                "size_bytes": metrics["size_bytes"],
                "file_count": metrics["file_count"],
            },
            "created_at": metrics["created_at"],
            "modified_at": metrics["modified_at"],
            "last_used_at": last_used,
            "age_days": self._age_days(last_used),
            "provenance": {
                "source": provenance.get("source"),
                "datasets": provenance.get("datasets", []),
                "artificial_weather_data": bool(
                    provenance.get("artificial_weather_data", False)
                ),
                "checksums_available": (plan_directory / "checksums.json").is_file(),
                "provenance_file_available": (plan_directory / "provenance.json").is_file(),
            },
            "dependencies": {
                "download_job_count": len(downloads),
                "active_download_job_count": len(active),
                "download_jobs": downloads,
            },
            "deletion": {
                "allowed": not active,
                "blocked_reason": (
                    None
                    if not active
                    else "One or more ERA5 download jobs are still active."
                ),
                "confirmation": {
                    "plan_key": plan_key,
                    "dependent_job_ids": dependency_ids,
                },
            },
        }

    def delete(self, plan_key: str, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise Era5CacheServiceError(
                "cache_delete_confirmation_invalid",
                "Cache deletion confirmation must be a JSON object.",
            )
        with self.download_manager.cache_operation_lock:
            entry = self.detail(plan_key)
            confirmation = entry["deletion"]["confirmation"]
            if request.get("confirm_plan_key") != plan_key:
                raise Era5CacheServiceError(
                    "cache_delete_confirmation_invalid",
                    "Confirm the exact ERA5 plan key before deleting the cache entry.",
                )
            supplied_ids = request.get("dependent_job_ids")
            if not isinstance(supplied_ids, list) or any(
                not isinstance(job_id, str) for job_id in supplied_ids
            ):
                raise Era5CacheServiceError(
                    "cache_delete_confirmation_invalid",
                    "Confirm the dependent ERA5 download job IDs before deletion.",
                )
            expected_ids = confirmation["dependent_job_ids"]
            if sorted(set(supplied_ids)) != expected_ids:
                raise Era5CacheServiceError(
                    "cache_dependency_snapshot_changed",
                    "The ERA5 cache dependencies changed. Refresh the cache entry before deleting it.",
                )
            if not entry["deletion"]["allowed"]:
                raise Era5CacheServiceError(
                    "cache_entry_in_use",
                    entry["deletion"]["blocked_reason"],
                )

            plan_directory = self._canonical_plan_directory(plan_key)
            tombstone = self.cache_root / f".deleting-{plan_key}-{uuid.uuid4().hex[:12]}"
            deleted_bytes = int(entry["storage"]["size_bytes"])
            try:
                os.replace(plan_directory, tombstone)
                shutil.rmtree(tombstone)
            except OSError as exc:
                if tombstone.exists() and not plan_directory.exists():
                    try:
                        os.replace(tombstone, plan_directory)
                    except OSError:
                        pass
                raise Era5CacheServiceError(
                    "cache_delete_failed",
                    "The ERA5 cache entry could not be deleted safely.",
                ) from exc

            deleted_at = self._utc_now()
            self._append_audit({
                "event": "era5_cache_deleted",
                "timestamp": deleted_at,
                "plan_key": plan_key,
                "released_bytes": deleted_bytes,
                "dependent_job_ids": expected_ids,
            })
            return {
                "ok": True,
                "deleted": {
                    "plan_key": plan_key,
                    "deleted_at": deleted_at,
                    "released_bytes": deleted_bytes,
                    "dependent_job_ids": expected_ids,
                },
            }

    def _canonical_plan_directory(self, plan_key: str) -> Path:
        if not isinstance(plan_key, str) or not _PLAN_KEY_RE.fullmatch(plan_key):
            raise Era5CacheServiceError("cache_entry_not_found", "ERA5 cache entry not found.")
        root = self.cache_root.resolve()
        candidate = root / plan_key
        if candidate.is_symlink():
            raise Era5CacheServiceError(
                "cache_entry_invalid", "Symlinked ERA5 cache entries are not managed."
            )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise Era5CacheServiceError(
                "cache_entry_not_found", "ERA5 cache entry not found."
            ) from exc
        if resolved.parent != root or not resolved.is_dir():
            raise Era5CacheServiceError(
                "cache_entry_invalid", "ERA5 cache entry is outside the managed cache."
            )
        return resolved

    def _directory_metrics(self, root: Path) -> dict[str, Any]:
        size_bytes = 0
        file_count = 0
        timestamps: list[float] = []
        for current_root, directory_names, file_names in os.walk(root, followlinks=False):
            current = Path(current_root)
            directory_names[:] = [
                name for name in directory_names if not (current / name).is_symlink()
            ]
            for file_name in file_names:
                path = current / file_name
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    stat = path.stat()
                except OSError:
                    continue
                size_bytes += stat.st_size
                file_count += 1
                timestamps.append(stat.st_mtime)
        if not timestamps:
            try:
                timestamps.append(root.stat().st_mtime)
            except OSError:
                timestamps.append(datetime.now(timezone.utc).timestamp())
        return {
            "size_bytes": size_bytes,
            "file_count": file_count,
            "created_at": self._timestamp(min(timestamps)),
            "modified_at": self._timestamp(max(timestamps)),
        }

    @staticmethod
    def _download_dependency(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job.get("id"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "retry_of": job.get("retry_of"),
        }

    def _last_used_at(self, modified_at: str, downloads: list[dict[str, Any]]) -> str:
        timestamps = [modified_at]
        for download in downloads:
            for field in ("finished_at", "started_at", "created_at"):
                value = download.get(field)
                if isinstance(value, str):
                    timestamps.append(value)
                    break
        return max(timestamps, key=self._parse_timestamp)

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return datetime.fromtimestamp(0, timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _age_days(self, value: str) -> float:
        age = datetime.now(timezone.utc) - self._parse_timestamp(value)
        return round(max(0.0, age.total_seconds() / 86400), 2)

    @staticmethod
    def _timestamp(value: float) -> str:
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _append_audit(self, event: dict[str, Any]) -> None:
        audit_directory = self.cache_root / ".audit"
        audit_directory.mkdir(parents=True, exist_ok=True)
        audit_path = audit_directory / "cache-events.jsonl"
        with self._audit_lock:
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    @staticmethod
    def _invalid_entry(plan_key: str, message: str) -> dict[str, Any]:
        return {
            "plan_key": plan_key,
            "status": "invalid",
            "period": {},
            "domain": {},
            "coverage": {"hits": 0, "partial_entries": 0, "total": 0, "percent": 0},
            "storage": {"size_bytes": 0, "file_count": 0},
            "created_at": None,
            "modified_at": None,
            "last_used_at": None,
            "age_days": None,
            "provenance": {"artificial_weather_data": False},
            "dependencies": {
                "download_job_count": 0,
                "active_download_job_count": 0,
                "download_jobs": [],
            },
            "deletion": {
                "allowed": False,
                "blocked_reason": message,
                "confirmation": {
                    "plan_key": plan_key,
                    "dependent_job_ids": [],
                },
            },
        }
