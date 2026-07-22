#!/usr/bin/env python3
"""Public simulation-store facade with atomic preflight event ordering."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from workbench._simulation_store_facade_core import (
    SCHEMA_VERSION,
    SimulationStore as _FacadeCoreSimulationStore,
    SimulationStoreError,
)

_ACTIVE_JOB_STATUSES = (
    "PREPROCESSING",
    "INITIALIZING",
    "SIMULATING",
    "POSTPROCESSING",
    "CANCELLING",
)
_CACHE_BLOCKING_JOB_STATUSES = {
    "READY",
    "QUEUED",
    *_ACTIVE_JOB_STATUSES,
}
_PLAN_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SimulationStore(_FacadeCoreSimulationStore):
    """Validated store with atomic admission and exact appended-event results."""

    def dependencies_for_plan(self, plan_key: str) -> list[dict[str, Any]]:
        """Return path-free simulation records that reference one ERA5 plan."""

        if not isinstance(plan_key, str) or not _PLAN_KEY_RE.fullmatch(plan_key):
            raise SimulationStoreError(
                "invalid_plan_key", "ERA5 plan key must be a 64-character SHA-256 value."
            )
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT
                    job.id, job.specification_key, job.retry_of, job.status,
                    job.created_at, job.queued_at, job.started_at,
                    job.finished_at, job.current_step_id
                FROM simulation_job AS job
                INNER JOIN input_dataset AS input ON input.job_id = job.id
                WHERE input.plan_key = ?
                ORDER BY job.created_at DESC, job.id DESC
                """,
                (plan_key,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "specification_key": row["specification_key"],
                "retry_of": row["retry_of"],
                "status": row["status"],
                "created_at": row["created_at"],
                "queued_at": row["queued_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "current_step_id": row["current_step_id"],
                "blocking": row["status"] in _CACHE_BLOCKING_JOB_STATUSES,
                "retryable": row["status"] in {"FAILED", "CANCELLED"},
            }
            for row in rows
        ]

    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        max_active_jobs: int = 1,
        preflight: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self._validate_job_id(job_id)
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise SimulationStoreError("invalid_worker_id", "Worker ID must be non-empty.")
        if (
            isinstance(max_active_jobs, bool)
            or not isinstance(max_active_jobs, int)
            or max_active_jobs < 1
        ):
            raise SimulationStoreError(
                "invalid_concurrency_limit",
                "Maximum active simulations must be a positive integer.",
            )
        if preflight is not None and not isinstance(preflight, dict):
            raise SimulationStoreError(
                "invalid_measurement", "Resource preflight must be an object."
            )

        now = _utc_now()
        with self._transaction() as connection:
            placeholders = ",".join("?" for _ in _ACTIVE_JOB_STATUSES)
            active = connection.execute(
                f"SELECT COUNT(*) AS count FROM simulation_job WHERE status IN ({placeholders})",
                _ACTIVE_JOB_STATUSES,
            ).fetchone()
            if int(active["count"]) >= max_active_jobs:
                return None

            row = connection.execute(
                "SELECT * FROM simulation_job WHERE id = ? AND status = 'QUEUED'",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            step = connection.execute(
                """
                SELECT * FROM job_step
                WHERE job_id = ? AND status = 'PENDING'
                ORDER BY position ASC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if step is None:
                raise SimulationStoreError(
                    "job_state_invalid", "Queued simulation has no pending step."
                )

            stage = self._stage_for_step(step["step_id"])
            connection.execute(
                """
                UPDATE simulation_job
                SET status = ?, started_at = ?, worker_id = ?, current_step_id = ?
                WHERE id = ? AND status = 'QUEUED'
                """,
                (stage, now, worker_id.strip(), step["step_id"], job_id),
            )
            connection.execute(
                """
                UPDATE job_step
                SET status = 'RUNNING', attempt = attempt + 1,
                    started_at = ?, finished_at = NULL,
                    error_code = NULL, error_message = NULL
                WHERE job_id = ? AND step_id = ?
                """,
                (now, job_id, step["step_id"]),
            )
            if preflight is not None:
                self._append_event(
                    connection,
                    job_id,
                    event_type="resource_preflight_passed",
                    status=stage,
                    step_id=step["step_id"],
                    message=(
                        "Resource preflight passed; the simulation worker claimed "
                        "the queued job."
                    ),
                    details={
                        "worker_id": worker_id.strip(),
                        "assessment": preflight,
                    },
                )
            self._append_event(
                connection,
                job_id,
                event_type="step_started",
                status=stage,
                step_id=step["step_id"],
                message=f"Started step {step['label']}.",
                details={"worker_id": worker_id.strip()},
            )
        return self.get_job(job_id)

    def record_event(
        self,
        job_id: str,
        *,
        event_type: str,
        status: str,
        message: str,
        step_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append and return exactly the newly persisted event."""

        self._validate_job_id(job_id)
        for value, label in (
            (event_type, "Event type"),
            (status, "Event status"),
            (message, "Event message"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise SimulationStoreError(
                    "invalid_event", f"{label} must be non-empty."
                )
        if details is not None and not isinstance(details, dict):
            raise SimulationStoreError(
                "invalid_event", "Event details must be an object."
            )

        with self._transaction() as connection:
            self._job_row(connection, job_id)
            if step_id is not None:
                self._step_row(connection, job_id, step_id)
            self._append_event(
                connection,
                job_id,
                event_type=event_type.strip(),
                status=status.strip(),
                step_id=step_id,
                message=message.strip(),
                details=details or {},
            )
            row = connection.execute(
                """
                SELECT sequence, timestamp, event_type, status, step_id,
                       message, details_json
                FROM job_event
                WHERE job_id = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        return {
            "sequence": int(row["sequence"]),
            "timestamp": row["timestamp"],
            "type": row["event_type"],
            "status": row["status"],
            "step_id": row["step_id"],
            "message": row["message"],
            "details": self._decode_event_details(row["details_json"]),
        }


__all__ = ["SCHEMA_VERSION", "SimulationStore", "SimulationStoreError"]
