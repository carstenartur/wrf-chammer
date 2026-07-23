#!/usr/bin/env python3
"""Public simulation store with ERA5 dependency coordination."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

import workbench._simulation_store_dependency_core as _core
from workbench._simulation_store_dependency_core import *  # noqa: F401,F403
from workbench.era5_dependency_lock import ERA5_DEPENDENCY_LOCK

_PLAN_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_JOB_ID_RE = re.compile(r"^sim-[0-9a-f]{12}-[0-9a-f]{12}$")
_REPRODUCTION_EVENT = "job_reproduced"


class SimulationStore(_core.SimulationStore):
    """Coordinate simulation creation with dependencies and exact reproduction."""

    def _require_input_dataset_available(self, specification: dict[str, Any]) -> None:
        identity = specification.get("identity")
        era5_input = identity.get("era5_input") if isinstance(identity, dict) else None
        plan_key = era5_input.get("plan_key") if isinstance(era5_input, dict) else None
        files = era5_input.get("files") if isinstance(era5_input, dict) else None
        data_service = getattr(self.specification_service, "data_service", None)
        plan_directory_getter = getattr(data_service, "plan_directory", None)
        # The production PipelineSpecificationService always exposes its
        # Era5DataService. Isolated persistence adapters used by unit tests may
        # intentionally provide only immutable specification objects; their
        # executor tests retain the independent input-byte verification.
        if not callable(plan_directory_getter):
            return
        if (
            not isinstance(plan_key, str)
            or not _PLAN_KEY_RE.fullmatch(plan_key)
            or not isinstance(files, list)
            or not files
        ):
            raise SimulationStoreError(
                "input_dataset_unavailable",
                "The immutable specification does not reference an available ERA5 input dataset.",
            )
        raw_directory = plan_directory_getter(plan_key)
        try:
            if raw_directory.is_symlink() or not raw_directory.is_dir():
                raise OSError("ERA5 plan directory is unavailable")
            plan_directory = raw_directory.resolve(strict=True)
        except OSError as exc:
            raise SimulationStoreError(
                "input_dataset_unavailable",
                "The verified ERA5 cache entry is no longer available.",
            ) from exc
        for sidecar in ("checksums.json", "provenance.json"):
            path = plan_directory / sidecar
            try:
                unavailable = path.is_symlink() or not path.is_file()
            except OSError:
                unavailable = True
            if unavailable:
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "The verified ERA5 cache metadata is incomplete.",
                )
        for entry in files:
            if not isinstance(entry, dict):
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "The immutable ERA5 input file list is invalid.",
                )
            value = entry.get("path")
            if not isinstance(value, str) or "\\" in value or "\x00" in value:
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "The immutable ERA5 input file path is invalid.",
                )
            relative = PurePosixPath(value)
            if relative.is_absolute() or any(
                part in {"", ".", ".."} for part in value.split("/")
            ):
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "The immutable ERA5 input file path is invalid.",
                )
            expected_size = entry.get("size_bytes")
            try:
                target = (plan_directory / relative.as_posix()).resolve(strict=True)
                unavailable = (
                    plan_directory not in target.parents
                    or target.is_symlink()
                    or not target.is_file()
                )
                if (
                    not unavailable
                    and isinstance(expected_size, int)
                    and not isinstance(expected_size, bool)
                ):
                    unavailable = target.stat().st_size != expected_size
            except OSError:
                unavailable = True
            if unavailable:
                raise SimulationStoreError(
                    "input_dataset_unavailable",
                    "One or more verified ERA5 input files are no longer available.",
                )

    def create_job(
        self, specification_key: str, *, retry_of: str | None = None
    ) -> dict[str, Any]:
        with ERA5_DEPENDENCY_LOCK:
            try:
                specification = self.specification_service.get(specification_key)
            except Exception as exc:
                raise SimulationStoreError(
                    "specification_not_found",
                    "Immutable pipeline specification not found.",
                ) from exc
            self._require_input_dataset_available(specification)
            return super().create_job(specification_key, retry_of=retry_of)

    def reproduce_job(self, job_id: str) -> dict[str, Any]:
        """Create a new READY job from exactly the same immutable specification."""

        source = super().get_job(job_id)
        reproduced = self.create_job(source["specification_key"])
        self.record_event(
            reproduced["id"],
            event_type=_REPRODUCTION_EVENT,
            status="READY",
            message=(
                "Simulation job reproduced from the exact immutable specification; "
                "no worker has been queued or started."
            ),
            details={
                "source_job_id": source["id"],
                "specification_key": source["specification_key"],
                "mode": "exact-immutable-specification",
            },
        )
        return self.get_job(reproduced["id"])

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._decorate_reproduction_lineage([super().get_job(job_id)])[0]

    def list_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self._decorate_reproduction_lineage(super().list_jobs(limit=limit))

    def _decorate_reproduction_lineage(
        self, jobs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not jobs:
            return jobs
        requested_ids = {
            job.get("id")
            for job in jobs
            if isinstance(job.get("id"), str) and _JOB_ID_RE.fullmatch(job["id"])
        }
        lineage = {
            job_id: {"reproduced_from": None, "reproductions": []}
            for job_id in requested_ids
        }
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event.job_id, event.details_json, job.created_at
                FROM job_event AS event
                INNER JOIN simulation_job AS job ON job.id = event.job_id
                WHERE event.event_type = ?
                ORDER BY job.created_at ASC, event.job_id ASC, event.sequence ASC
                """,
                (_REPRODUCTION_EVENT,),
            ).fetchall()
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(details, dict):
                continue
            source_id = details.get("source_job_id")
            child_id = row["job_id"]
            if (
                not isinstance(source_id, str)
                or not _JOB_ID_RE.fullmatch(source_id)
                or not isinstance(child_id, str)
                or not _JOB_ID_RE.fullmatch(child_id)
            ):
                continue
            if child_id in lineage:
                existing = lineage[child_id]["reproduced_from"]
                if existing not in (None, source_id):
                    raise SimulationStoreError(
                        "reproduction_lineage_invalid",
                        "Simulation reproduction lineage is inconsistent.",
                    )
                lineage[child_id]["reproduced_from"] = source_id
            if source_id in lineage and child_id not in lineage[source_id]["reproductions"]:
                lineage[source_id]["reproductions"].append(child_id)

        decorated: list[dict[str, Any]] = []
        for job in jobs:
            job_id = job.get("id")
            values = lineage.get(
                job_id,
                {"reproduced_from": None, "reproductions": []},
            )
            decorated.append(
                {
                    **job,
                    "reproduced_from": values["reproduced_from"],
                    "reproductions": list(values["reproductions"]),
                }
            )
        return decorated


__all__ = list(getattr(_core, "__all__", ()))
if "SimulationStore" not in __all__:
    __all__.append("SimulationStore")
