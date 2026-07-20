#!/usr/bin/env python3
"""Persistent SQLite state for immutable-specification-backed simulations."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from workbench.pipeline_specification_service import (
    PipelineSpecificationService,
    PipelineSpecificationServiceError,
)

SCHEMA_VERSION = 1
_JOB_ID_RE = re.compile(r"^sim-[0-9a-f]{12}-[0-9a-f]{12}$")
_SPEC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_JOB_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
_ACTIVE_JOB_STATUSES = {
    "PREPROCESSING",
    "INITIALIZING",
    "SIMULATING",
    "POSTPROCESSING",
    "CANCELLING",
}
_STEP_STAGE = {
    "input-data": "PREPROCESSING",
    "geogrid": "PREPROCESSING",
    "ungrib": "PREPROCESSING",
    "metgrid": "PREPROCESSING",
    "real": "INITIALIZING",
    "wrf": "SIMULATING",
    "postprocessing": "POSTPROCESSING",
    "result-indexing": "POSTPROCESSING",
}


class SimulationStoreError(RuntimeError):
    """A classified persistent simulation-state error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _decode_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _finite_nonnegative(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SimulationStoreError("invalid_measurement", f"{field} must be a non-negative number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SimulationStoreError(
            "invalid_measurement", f"{field} must be a non-negative number"
        ) from exc
    if not math.isfinite(number) or number < 0:
        raise SimulationStoreError("invalid_measurement", f"{field} must be a non-negative number")
    return number


class SimulationStore:
    """Versioned local persistence and state-machine operations for real runs."""

    def __init__(
        self,
        repo_root: Path,
        specification_service: PipelineSpecificationService,
        *,
        database_path: Path | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.specification_service = specification_service
        configured = os.environ.get("WRF_CHAMMER_SIMULATION_DATABASE")
        if database_path is None and configured:
            configured_path = Path(configured).expanduser()
            database_path = (
                configured_path
                if configured_path.is_absolute()
                else self.repo_root / configured_path
            )
        self.database_path = (
            database_path
            or self.repo_root / "workbench-runs" / "state" / "workbench.sqlite3"
        ).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_lock = threading.Lock()
        self._migrate()

    def schema_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migration"
            ).fetchone()
            return int(row["version"])

    def create_job(self, specification_key: str, *, retry_of: str | None = None) -> dict[str, Any]:
        specification = self._load_specification(specification_key)
        if retry_of is not None:
            self._validate_job_id(retry_of)
        job_id = f"sim-{specification_key[:12]}-{uuid.uuid4().hex[:12]}"
        identity = specification["identity"]
        now = _utc_now()
        with self._transaction() as connection:
            if retry_of is not None:
                existing = connection.execute(
                    "SELECT specification_key, status FROM simulation_job WHERE id = ?",
                    (retry_of,),
                ).fetchone()
                if existing is None:
                    raise SimulationStoreError("job_not_found", "Simulation job not found.")
                if existing["status"] not in {"FAILED", "CANCELLED"}:
                    raise SimulationStoreError(
                        "job_not_retryable",
                        "Only failed or cancelled simulations can be retried.",
                    )
                if existing["specification_key"] != specification_key:
                    raise SimulationStoreError(
                        "retry_specification_mismatch",
                        "A retry must use the same immutable specification.",
                    )
            connection.execute(
                """
                INSERT INTO simulation_job (
                    id, specification_key, retry_of, status, created_at,
                    queued_at, started_at, finished_at, worker_id,
                    current_step_id, error_code, error_message
                ) VALUES (?, ?, ?, 'READY', ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
                """,
                (job_id, specification_key, retry_of, now),
            )
            for position, contract in enumerate(identity.get("steps", [])):
                step_id = contract.get("id")
                label = contract.get("label")
                if not isinstance(step_id, str) or not step_id:
                    raise SimulationStoreError(
                        "specification_integrity_error",
                        "The immutable specification contains an invalid step.",
                    )
                connection.execute(
                    """
                    INSERT INTO job_step (
                        job_id, step_id, position, label, status, attempt,
                        started_at, finished_at, progress_json, contract_json,
                        error_code, error_message
                    ) VALUES (?, ?, ?, ?, 'PENDING', 0, NULL, NULL, '{}', ?, NULL, NULL)
                    """,
                    (job_id, step_id, position, str(label or step_id), _json(contract)),
                )
            era5_input = identity.get("era5_input", {})
            connection.execute(
                """
                INSERT INTO input_dataset (
                    id, job_id, plan_key, provenance_json, files_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"input-{uuid.uuid4().hex}",
                    job_id,
                    era5_input.get("plan_key"),
                    _json(era5_input.get("provenance", {})),
                    _json(era5_input.get("files", [])),
                    now,
                ),
            )
            runtime = identity.get("runtime", {})
            for runtime_name in sorted(runtime):
                runtime_value = runtime[runtime_name]
                connection.execute(
                    """
                    INSERT INTO runtime_snapshot (
                        id, job_id, runtime_name, reference, identity, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"runtime-{uuid.uuid4().hex}",
                        job_id,
                        runtime_name,
                        runtime_value.get("reference"),
                        runtime_value.get("identity"),
                        now,
                    ),
                )
            self._append_event(
                connection,
                job_id,
                event_type="job_created",
                status="READY",
                message="Simulation job created from an immutable specification.",
                details={"specification_key": specification_key, "retry_of": retry_of},
            )
        return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        return self.create_job(job["specification_key"], retry_of=job_id)

    def enqueue_job(self, job_id: str) -> dict[str, Any]:
        self._validate_job_id(job_id)
        now = _utc_now()
        with self._transaction() as connection:
            row = self._job_row(connection, job_id)
            if row["status"] != "READY":
                raise SimulationStoreError(
                    "job_not_queueable", "Only a ready simulation can be queued."
                )
            connection.execute(
                "UPDATE simulation_job SET status = 'QUEUED', queued_at = ? WHERE id = ?",
                (now, job_id),
            )
            self._append_event(
                connection,
                job_id,
                event_type="job_queued",
                status="QUEUED",
                message="Simulation job is waiting for a worker.",
            )
        return self.get_job(job_id)

    def claim_next_job(self, worker_id: str) -> dict[str, Any] | None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise SimulationStoreError("invalid_worker_id", "Worker ID must be non-empty.")
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM simulation_job
                WHERE status = 'QUEUED'
                ORDER BY queued_at ASC, created_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            step = connection.execute(
                """
                SELECT * FROM job_step
                WHERE job_id = ? AND status = 'PENDING'
                ORDER BY position ASC LIMIT 1
                """,
                (row["id"],),
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
                (stage, now, worker_id.strip(), step["step_id"], row["id"]),
            )
            connection.execute(
                """
                UPDATE job_step
                SET status = 'RUNNING', attempt = attempt + 1,
                    started_at = ?, finished_at = NULL,
                    error_code = NULL, error_message = NULL
                WHERE job_id = ? AND step_id = ?
                """,
                (now, row["id"], step["step_id"]),
            )
            self._append_event(
                connection,
                row["id"],
                event_type="step_started",
                status=stage,
                step_id=step["step_id"],
                message=f"Started step {step['label']}.",
                details={"worker_id": worker_id.strip()},
            )
            claimed_id = row["id"]
        return self.get_job(claimed_id)

    def update_step_progress(
        self, job_id: str, step_id: str, progress: dict[str, Any]
    ) -> dict[str, Any]:
        self._validate_job_id(job_id)
        if not isinstance(progress, dict):
            raise SimulationStoreError("invalid_progress", "Step progress must be an object.")
        with self._transaction() as connection:
            step = self._step_row(connection, job_id, step_id)
            if step["status"] != "RUNNING":
                raise SimulationStoreError(
                    "step_not_running", "Progress can be recorded only for a running step."
                )
            connection.execute(
                "UPDATE job_step SET progress_json = ? WHERE job_id = ? AND step_id = ?",
                (_json(progress), job_id, step_id),
            )
            self._append_event(
                connection,
                job_id,
                event_type="step_progress",
                status=self._job_row(connection, job_id)["status"],
                step_id=step_id,
                message=f"Progress updated for step {step_id}.",
                details={"progress": progress},
            )
        return self.get_job(job_id)

    def complete_step(self, job_id: str, step_id: str) -> dict[str, Any]:
        self._validate_job_id(job_id)
        now = _utc_now()
        with self._transaction() as connection:
            job = self._job_row(connection, job_id)
            step = self._step_row(connection, job_id, step_id)
            if job["current_step_id"] != step_id or step["status"] != "RUNNING":
                raise SimulationStoreError(
                    "step_not_current", "Only the current running step can be completed."
                )
            connection.execute(
                """
                UPDATE job_step SET status = 'SUCCEEDED', finished_at = ?
                WHERE job_id = ? AND step_id = ?
                """,
                (now, job_id, step_id),
            )
            next_step = connection.execute(
                """
                SELECT * FROM job_step
                WHERE job_id = ? AND status = 'PENDING'
                ORDER BY position ASC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if next_step is None:
                connection.execute(
                    """
                    UPDATE simulation_job
                    SET status = 'SUCCEEDED', current_step_id = NULL,
                        finished_at = ?, worker_id = NULL
                    WHERE id = ?
                    """,
                    (now, job_id),
                )
                self._append_event(
                    connection,
                    job_id,
                    event_type="job_succeeded",
                    status="SUCCEEDED",
                    step_id=step_id,
                    message="All real pipeline steps completed successfully.",
                )
            else:
                next_stage = self._stage_for_step(next_step["step_id"])
                connection.execute(
                    """
                    UPDATE job_step
                    SET status = 'RUNNING', attempt = attempt + 1,
                        started_at = ?, finished_at = NULL,
                        error_code = NULL, error_message = NULL
                    WHERE job_id = ? AND step_id = ?
                    """,
                    (now, job_id, next_step["step_id"]),
                )
                connection.execute(
                    """
                    UPDATE simulation_job
                    SET status = ?, current_step_id = ?
                    WHERE id = ?
                    """,
                    (next_stage, next_step["step_id"], job_id),
                )
                self._append_event(
                    connection,
                    job_id,
                    event_type="step_started",
                    status=next_stage,
                    step_id=next_step["step_id"],
                    message=f"Completed {step['label']} and started {next_step['label']}.",
                )
        return self.get_job(job_id)

    def fail_step(
        self, job_id: str, step_id: str, *, code: str, message: str
    ) -> dict[str, Any]:
        self._validate_job_id(job_id)
        if not isinstance(code, str) or not code.strip():
            raise SimulationStoreError("invalid_error", "Failure code must be non-empty.")
        if not isinstance(message, str) or not message.strip():
            raise SimulationStoreError("invalid_error", "Failure message must be non-empty.")
        now = _utc_now()
        with self._transaction() as connection:
            job = self._job_row(connection, job_id)
            step = self._step_row(connection, job_id, step_id)
            if job["current_step_id"] != step_id or step["status"] != "RUNNING":
                raise SimulationStoreError(
                    "step_not_current", "Only the current running step can fail."
                )
            connection.execute(
                """
                UPDATE job_step
                SET status = 'FAILED', finished_at = ?, error_code = ?, error_message = ?
                WHERE job_id = ? AND step_id = ?
                """,
                (now, code.strip(), message.strip(), job_id, step_id),
            )
            connection.execute(
                """
                UPDATE simulation_job
                SET status = 'FAILED', finished_at = ?, worker_id = NULL,
                    error_code = ?, error_message = ?
                WHERE id = ?
                """,
                (now, code.strip(), message.strip(), job_id),
            )
            self._append_event(
                connection,
                job_id,
                event_type="step_failed",
                status="FAILED",
                step_id=step_id,
                message=message.strip(),
                details={"code": code.strip()},
            )
        return self.get_job(job_id)

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        self._validate_job_id(job_id)
        now = _utc_now()
        with self._transaction() as connection:
            job = self._job_row(connection, job_id)
            status = job["status"]
            if status in _TERMINAL_JOB_STATUSES:
                return self._hydrate_job(connection, job_id)
            if status in {"READY", "QUEUED"}:
                connection.execute(
                    """
                    UPDATE simulation_job
                    SET status = 'CANCELLED', finished_at = ?, worker_id = NULL,
                        current_step_id = NULL
                    WHERE id = ?
                    """,
                    (now, job_id),
                )
                connection.execute(
                    """
                    UPDATE job_step SET status = 'CANCELLED', finished_at = ?
                    WHERE job_id = ? AND status = 'PENDING'
                    """,
                    (now, job_id),
                )
                event_status = "CANCELLED"
                message = "Simulation cancelled before worker execution."
            else:
                connection.execute(
                    "UPDATE simulation_job SET status = 'CANCELLING' WHERE id = ?",
                    (job_id,),
                )
                event_status = "CANCELLING"
                message = "Cancellation requested for the active simulation."
            self._append_event(
                connection,
                job_id,
                event_type="cancel_requested",
                status=event_status,
                step_id=job["current_step_id"],
                message=message,
            )
        return self.get_job(job_id)

    def finalize_cancel(self, job_id: str) -> dict[str, Any]:
        self._validate_job_id(job_id)
        now = _utc_now()
        with self._transaction() as connection:
            job = self._job_row(connection, job_id)
            if job["status"] != "CANCELLING":
                raise SimulationStoreError(
                    "job_not_cancelling", "Simulation is not waiting for cancellation."
                )
            connection.execute(
                """
                UPDATE job_step SET status = 'CANCELLED', finished_at = ?
                WHERE job_id = ? AND status IN ('PENDING', 'RUNNING')
                """,
                (now, job_id),
            )
            connection.execute(
                """
                UPDATE simulation_job
                SET status = 'CANCELLED', finished_at = ?, worker_id = NULL,
                    current_step_id = NULL
                WHERE id = ?
                """,
                (now, job_id),
            )
            self._append_event(
                connection,
                job_id,
                event_type="job_cancelled",
                status="CANCELLED",
                message="Active simulation cancellation completed.",
            )
        return self.get_job(job_id)

    def recover_interrupted_jobs(self) -> list[str]:
        recovered: list[str] = []
        now = _utc_now()
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, current_step_id FROM simulation_job
                WHERE status IN ('PREPROCESSING', 'INITIALIZING', 'SIMULATING', 'POSTPROCESSING', 'CANCELLING')
                ORDER BY created_at ASC
                """
            ).fetchall()
            for row in rows:
                if row["current_step_id"]:
                    connection.execute(
                        """
                        UPDATE job_step
                        SET status = 'FAILED', finished_at = ?,
                            error_code = 'worker_interrupted',
                            error_message = 'Worker stopped before the step reached a checkpoint.'
                        WHERE job_id = ? AND step_id = ? AND status = 'RUNNING'
                        """,
                        (now, row["id"], row["current_step_id"]),
                    )
                connection.execute(
                    """
                    UPDATE simulation_job
                    SET status = 'FAILED', finished_at = ?, worker_id = NULL,
                        error_code = 'worker_interrupted',
                        error_message = 'Worker stopped before the simulation reached a checkpoint.'
                    WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                self._append_event(
                    connection,
                    row["id"],
                    event_type="job_recovered",
                    status="FAILED",
                    step_id=row["current_step_id"],
                    message="Interrupted simulation marked failed for safe retry.",
                    details={"code": "worker_interrupted"},
                )
                recovered.append(row["id"])
        return recovered

    def add_artifact(
        self,
        job_id: str,
        *,
        step_id: str | None,
        kind: str,
        relative_path: str,
        sha256: str | None = None,
        size_bytes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_job_id(job_id)
        if not isinstance(kind, str) or not kind.strip():
            raise SimulationStoreError("invalid_artifact", "Artifact kind must be non-empty.")
        normalized_path = self._safe_relative_path(relative_path)
        if sha256 is not None and (
            not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256)
        ):
            raise SimulationStoreError("invalid_artifact", "Artifact SHA-256 is invalid.")
        if size_bytes is not None and (
            isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0
        ):
            raise SimulationStoreError("invalid_artifact", "Artifact size must be non-negative.")
        artifact_id = f"artifact-{uuid.uuid4().hex}"
        with self._transaction() as connection:
            self._job_row(connection, job_id)
            if step_id is not None:
                self._step_row(connection, job_id, step_id)
            connection.execute(
                """
                INSERT INTO artifact (
                    id, job_id, step_id, kind, relative_path, sha256,
                    size_bytes, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    job_id,
                    step_id,
                    kind.strip(),
                    normalized_path,
                    sha256,
                    size_bytes,
                    _utc_now(),
                    _json(metadata or {}),
                ),
            )
        return next(
            artifact for artifact in self.get_job(job_id)["artifacts"] if artifact["id"] == artifact_id
        )

    def add_resource_measurement(
        self,
        job_id: str,
        *,
        step_id: str | None,
        cpu_seconds: Any = None,
        max_rss_bytes: Any = None,
        disk_bytes: Any = None,
        wall_seconds: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_job_id(job_id)
        values = {
            "cpu_seconds": _finite_nonnegative(cpu_seconds, "cpu_seconds"),
            "max_rss_bytes": _finite_nonnegative(max_rss_bytes, "max_rss_bytes"),
            "disk_bytes": _finite_nonnegative(disk_bytes, "disk_bytes"),
            "wall_seconds": _finite_nonnegative(wall_seconds, "wall_seconds"),
        }
        measurement_id = f"measurement-{uuid.uuid4().hex}"
        timestamp = _utc_now()
        with self._transaction() as connection:
            self._job_row(connection, job_id)
            if step_id is not None:
                self._step_row(connection, job_id, step_id)
            connection.execute(
                """
                INSERT INTO resource_measurement (
                    id, job_id, step_id, timestamp, cpu_seconds,
                    max_rss_bytes, disk_bytes, wall_seconds, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    measurement_id,
                    job_id,
                    step_id,
                    timestamp,
                    values["cpu_seconds"],
                    values["max_rss_bytes"],
                    values["disk_bytes"],
                    values["wall_seconds"],
                    _json(metadata or {}),
                ),
            )
        return {
            "id": measurement_id,
            "job_id": job_id,
            "step_id": step_id,
            "timestamp": timestamp,
            **values,
            "metadata": metadata or {},
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        self._validate_job_id(job_id)
        with self._connect() as connection:
            self._job_row(connection, job_id)
            return self._hydrate_job(connection, job_id)

    def list_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM simulation_job
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._hydrate_job(connection, row["id"], include_events=False) for row in rows]

    def _hydrate_job(
        self, connection: sqlite3.Connection, job_id: str, *, include_events: bool = True
    ) -> dict[str, Any]:
        job = self._job_row(connection, job_id)
        steps = [
            {
                "id": row["step_id"],
                "position": row["position"],
                "label": row["label"],
                "status": row["status"],
                "attempt": row["attempt"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "progress": _decode_json(row["progress_json"], {}),
                "contract": _decode_json(row["contract_json"], {}),
                "error": (
                    {"code": row["error_code"], "message": row["error_message"]}
                    if row["error_code"]
                    else None
                ),
            }
            for row in connection.execute(
                "SELECT * FROM job_step WHERE job_id = ? ORDER BY position ASC",
                (job_id,),
            ).fetchall()
        ]
        input_rows = connection.execute(
            "SELECT * FROM input_dataset WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        ).fetchall()
        runtimes = connection.execute(
            "SELECT * FROM runtime_snapshot WHERE job_id = ? ORDER BY runtime_name ASC",
            (job_id,),
        ).fetchall()
        artifacts = connection.execute(
            "SELECT * FROM artifact WHERE job_id = ? ORDER BY created_at ASC, id ASC",
            (job_id,),
        ).fetchall()
        measurements = connection.execute(
            "SELECT * FROM resource_measurement WHERE job_id = ? ORDER BY timestamp ASC, id ASC",
            (job_id,),
        ).fetchall()
        events = []
        if include_events:
            events = [
                {
                    "sequence": row["sequence"],
                    "timestamp": row["timestamp"],
                    "type": row["event_type"],
                    "status": row["status"],
                    "step_id": row["step_id"],
                    "message": row["message"],
                    "details": _decode_json(row["details_json"], {}),
                }
                for row in connection.execute(
                    "SELECT * FROM job_event WHERE job_id = ? ORDER BY sequence ASC",
                    (job_id,),
                ).fetchall()
            ]
        return {
            "id": job["id"],
            "specification_key": job["specification_key"],
            "retry_of": job["retry_of"],
            "status": job["status"],
            "created_at": job["created_at"],
            "queued_at": job["queued_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "worker_id": job["worker_id"],
            "current_step_id": job["current_step_id"],
            "error": (
                {"code": job["error_code"], "message": job["error_message"]}
                if job["error_code"]
                else None
            ),
            "cancellable": job["status"] not in _TERMINAL_JOB_STATUSES,
            "retryable": job["status"] in {"FAILED", "CANCELLED"},
            "steps": steps,
            "input_datasets": [
                {
                    "id": row["id"],
                    "plan_key": row["plan_key"],
                    "provenance": _decode_json(row["provenance_json"], {}),
                    "files": _decode_json(row["files_json"], []),
                    "created_at": row["created_at"],
                }
                for row in input_rows
            ],
            "runtime_snapshots": [
                {
                    "id": row["id"],
                    "name": row["runtime_name"],
                    "reference": row["reference"],
                    "identity": row["identity"],
                    "created_at": row["created_at"],
                }
                for row in runtimes
            ],
            "artifacts": [
                {
                    "id": row["id"],
                    "step_id": row["step_id"],
                    "kind": row["kind"],
                    "relative_path": row["relative_path"],
                    "sha256": row["sha256"],
                    "size_bytes": row["size_bytes"],
                    "created_at": row["created_at"],
                    "metadata": _decode_json(row["metadata_json"], {}),
                }
                for row in artifacts
            ],
            "resource_measurements": [
                {
                    "id": row["id"],
                    "step_id": row["step_id"],
                    "timestamp": row["timestamp"],
                    "cpu_seconds": row["cpu_seconds"],
                    "max_rss_bytes": row["max_rss_bytes"],
                    "disk_bytes": row["disk_bytes"],
                    "wall_seconds": row["wall_seconds"],
                    "metadata": _decode_json(row["metadata_json"], {}),
                }
                for row in measurements
            ],
            "events": events,
        }

    def _append_event(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        *,
        event_type: str,
        status: str,
        message: str,
        step_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM job_event WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO job_event (
                job_id, sequence, timestamp, event_type, status,
                step_id, message, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                int(row["sequence"]),
                _utc_now(),
                event_type,
                status,
                step_id,
                message,
                _json(details or {}),
            ),
        )

    def _load_specification(self, specification_key: str) -> dict[str, Any]:
        if not isinstance(specification_key, str) or not _SPEC_KEY_RE.fullmatch(
            specification_key
        ):
            raise SimulationStoreError(
                "specification_not_found", "Immutable pipeline specification not found."
            )
        try:
            return self.specification_service.get(specification_key)
        except PipelineSpecificationServiceError as exc:
            raise SimulationStoreError(exc.code, exc.message) from exc

    @staticmethod
    def _stage_for_step(step_id: str) -> str:
        stage = _STEP_STAGE.get(step_id)
        if stage is None:
            raise SimulationStoreError(
                "specification_integrity_error", f"Unknown pipeline step: {step_id}"
            )
        return stage

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SimulationStoreError("invalid_artifact", "Artifact path must be non-empty.")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise SimulationStoreError(
                "invalid_artifact", "Artifact path must stay inside the simulation directory."
            )
        return path.as_posix()

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
            raise SimulationStoreError("job_not_found", "Simulation job not found.")

    @staticmethod
    def _job_row(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM simulation_job WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise SimulationStoreError("job_not_found", "Simulation job not found.")
        return row

    @staticmethod
    def _step_row(
        connection: sqlite3.Connection, job_id: str, step_id: str
    ) -> sqlite3.Row:
        if not isinstance(step_id, str) or not step_id:
            raise SimulationStoreError("step_not_found", "Simulation step not found.")
        row = connection.execute(
            "SELECT * FROM job_step WHERE job_id = ? AND step_id = ?",
            (job_id, step_id),
        ).fetchone()
        if row is None:
            raise SimulationStoreError("step_not_found", "Simulation step not found.")
        return row

    def _migrate(self) -> None:
        with self._migration_lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migration (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                current = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migration"
                ).fetchone()["version"]
                if int(current) < 1:
                    self._apply_migration_1(connection)
                    connection.execute(
                        "INSERT INTO schema_migration(version, applied_at) VALUES (1, ?)",
                        (_utc_now(),),
                    )
                if int(current) > SCHEMA_VERSION:
                    raise SimulationStoreError(
                        "database_version_unsupported",
                        "The simulation database was created by a newer Workbench version.",
                    )

    @staticmethod
    def _apply_migration_1(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE simulation_job (
                id TEXT PRIMARY KEY,
                specification_key TEXT NOT NULL,
                retry_of TEXT REFERENCES simulation_job(id),
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                queued_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                worker_id TEXT,
                current_step_id TEXT,
                error_code TEXT,
                error_message TEXT
            );

            CREATE TABLE job_step (
                job_id TEXT NOT NULL REFERENCES simulation_job(id) ON DELETE CASCADE,
                step_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                progress_json TEXT NOT NULL,
                contract_json TEXT NOT NULL,
                error_code TEXT,
                error_message TEXT,
                PRIMARY KEY(job_id, step_id),
                UNIQUE(job_id, position)
            );

            CREATE TABLE job_event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES simulation_job(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                step_id TEXT,
                message TEXT NOT NULL,
                details_json TEXT NOT NULL,
                UNIQUE(job_id, sequence)
            );

            CREATE TABLE artifact (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES simulation_job(id) ON DELETE CASCADE,
                step_id TEXT,
                kind TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                sha256 TEXT,
                size_bytes INTEGER,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                FOREIGN KEY(job_id, step_id) REFERENCES job_step(job_id, step_id)
            );

            CREATE TABLE input_dataset (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES simulation_job(id) ON DELETE CASCADE,
                plan_key TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                files_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE runtime_snapshot (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES simulation_job(id) ON DELETE CASCADE,
                runtime_name TEXT NOT NULL,
                reference TEXT NOT NULL,
                identity TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, runtime_name)
            );

            CREATE TABLE resource_measurement (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES simulation_job(id) ON DELETE CASCADE,
                step_id TEXT,
                timestamp TEXT NOT NULL,
                cpu_seconds REAL,
                max_rss_bytes REAL,
                disk_bytes REAL,
                wall_seconds REAL,
                metadata_json TEXT NOT NULL,
                FOREIGN KEY(job_id, step_id) REFERENCES job_step(job_id, step_id)
            );

            CREATE INDEX simulation_job_status_queue
                ON simulation_job(status, queued_at, created_at);
            CREATE INDEX job_event_job_sequence
                ON job_event(job_id, sequence);
            CREATE INDEX artifact_job_step
                ON artifact(job_id, step_id);
            CREATE INDEX resource_measurement_job_step
                ON resource_measurement(job_id, step_id, timestamp);
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection
