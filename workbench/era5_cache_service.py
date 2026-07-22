#!/usr/bin/env python3
"""ERA5 cache management with persistent simulation dependencies."""

from __future__ import annotations

import threading
from typing import Any

import workbench._era5_cache_service_core as _core
from workbench._era5_cache_service_core import *  # noqa: F401,F403
from workbench.era5_dependency_lock import ERA5_DEPENDENCY_LOCK

CacheCoordinatedEra5DownloadManager = _core.CacheCoordinatedEra5DownloadManager
Era5CacheServiceError = _core.Era5CacheServiceError


class Era5CacheService(_core.Era5CacheService):
    """Include immutable simulation jobs in cache dependency snapshots."""

    def __init__(
        self,
        data_service: Any,
        download_manager: CacheCoordinatedEra5DownloadManager,
        simulation_store: Any | None = None,
    ):
        super().__init__(data_service, download_manager)
        self.simulation_store = simulation_store
        self._delete_context = threading.local()

    def _simulation_dependencies(self, plan_key: str) -> list[dict[str, Any]]:
        if self.simulation_store is None:
            return []
        getter = getattr(self.simulation_store, "dependencies_for_plan", None)
        if not callable(getter):
            raise Era5CacheServiceError(
                "cache_dependency_unavailable",
                "Simulation dependencies cannot be inspected safely.",
            )
        try:
            dependencies = getter(plan_key)
        except Exception as exc:
            raise Era5CacheServiceError(
                "cache_dependency_unavailable",
                "Simulation dependencies cannot be inspected safely.",
            ) from exc
        if not isinstance(dependencies, list) or any(
            not isinstance(dependency, dict) for dependency in dependencies
        ):
            raise Era5CacheServiceError(
                "cache_dependency_unavailable",
                "Simulation dependencies are invalid.",
            )
        return dependencies

    def detail(
        self,
        plan_key: str,
        *,
        downloads_snapshot: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        entry = super().detail(plan_key, downloads_snapshot=downloads_snapshot)
        simulations = self._simulation_dependencies(plan_key)
        simulation_ids = sorted(
            dependency["id"]
            for dependency in simulations
            if isinstance(dependency.get("id"), str)
        )
        expected = getattr(self._delete_context, "expected", None)
        if isinstance(expected, dict) and expected.get("plan_key") == plan_key:
            if simulation_ids != expected.get("simulation_ids"):
                raise Era5CacheServiceError(
                    "cache_dependency_snapshot_changed",
                    "The ERA5 cache dependencies changed. Refresh the cache entry before deleting it.",
                )

        blocking_simulations = [
            dependency for dependency in simulations if dependency.get("blocking") is True
        ]
        dependencies = dict(entry.get("dependencies") or {})
        dependencies.update(
            {
                "simulation_job_count": len(simulations),
                "blocking_simulation_job_count": len(blocking_simulations),
                "simulation_jobs": simulations,
            }
        )
        entry["dependencies"] = dependencies

        deletion = dict(entry.get("deletion") or {})
        download_blocked_reason = deletion.get("blocked_reason")
        reasons = [download_blocked_reason] if download_blocked_reason else []
        if blocking_simulations:
            reasons.append(
                "One or more simulations still require this ERA5 input dataset."
            )
        deletion["allowed"] = bool(deletion.get("allowed")) and not blocking_simulations
        deletion["blocked_reason"] = " ".join(reasons) or None
        confirmation = dict(deletion.get("confirmation") or {})
        confirmation["dependent_simulation_ids"] = simulation_ids
        deletion["confirmation"] = confirmation
        entry["deletion"] = deletion

        timestamps = [entry.get("last_used_at")]
        for dependency in simulations:
            for field in ("finished_at", "started_at", "queued_at", "created_at"):
                value = dependency.get(field)
                if isinstance(value, str):
                    timestamps.append(value)
                    break
        timestamps = [value for value in timestamps if isinstance(value, str)]
        if timestamps:
            last_used = max(timestamps, key=self._parse_timestamp)
            entry["last_used_at"] = last_used
            entry["age_days"] = self._age_days(last_used)
        return entry

    def delete(self, plan_key: str, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise Era5CacheServiceError(
                "cache_delete_confirmation_invalid",
                "Cache deletion confirmation must be a JSON object.",
            )
        with ERA5_DEPENDENCY_LOCK:
            entry = self.detail(plan_key)
            expected_simulation_ids = entry["deletion"]["confirmation"][
                "dependent_simulation_ids"
            ]
            supplied_simulation_ids = request.get("dependent_simulation_ids")
            if not isinstance(supplied_simulation_ids, list) or any(
                not isinstance(job_id, str) for job_id in supplied_simulation_ids
            ):
                raise Era5CacheServiceError(
                    "cache_delete_confirmation_invalid",
                    "Confirm the dependent simulation job IDs before deletion.",
                )
            if sorted(set(supplied_simulation_ids)) != expected_simulation_ids:
                raise Era5CacheServiceError(
                    "cache_dependency_snapshot_changed",
                    "The ERA5 cache dependencies changed. Refresh the cache entry before deleting it.",
                )
            self._delete_context.expected = {
                "plan_key": plan_key,
                "simulation_ids": expected_simulation_ids,
            }
            try:
                result = super().delete(plan_key, request)
            finally:
                self._delete_context.expected = None
            result["deleted"]["dependent_simulation_ids"] = expected_simulation_ids
            return result

    def _append_audit(self, event: dict[str, Any]) -> None:
        enriched = dict(event)
        context = getattr(self._delete_context, "expected", None)
        if enriched.get("event") == "era5_cache_deleted" and isinstance(context, dict):
            enriched["dependent_simulation_ids"] = list(
                context.get("simulation_ids") or []
            )
        super()._append_audit(enriched)


__all__ = [
    "CacheCoordinatedEra5DownloadManager",
    "Era5CacheService",
    "Era5CacheServiceError",
]
