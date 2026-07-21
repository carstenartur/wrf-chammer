#!/usr/bin/env python3
"""Validated public facade for persistent simulation-job state.

The internal SQLite adapter lives in ``_simulation_store_sqlite``. This module
validates immutable specification boundaries and cross-platform paths before
delegating to that adapter. It also exposes narrowly scoped queue/event methods
used by reconnectable event delivery and resource-aware worker admission.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from workbench._simulation_store_sqlite import SCHEMA_VERSION, SimulationStoreError
from workbench._simulation_store_sqlite import SimulationStore as _SQLiteSimulationStore

_SPEC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_PLAN_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXPECTED_STEP_IDS = (
    "input-data",
    "geogrid",
    "ungrib",
    "metgrid",
    "real",
    "wrf",
    "postprocessing",
    "result-indexing",
)
_REQUIRED_RUNTIMES = ("wps", "wrf", "postprocessing")
_ACTIVE_JOB_STATUSES = (
    "PREPROCESSING",
    "INITIALIZING",
    "SIMULATING",
    "POSTPROCESSING",
    "CANCELLING",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SimulationStore(_SQLiteSimulationStore):
    """SQLite store with a strictly validated immutable specification boundary."""

    def _load_specification(self, specification_key: str) -> dict[str, Any]:
        specification = super()._load_specification(specification_key)
        self._validate_specification(specification_key, specification)
        return specification

    def events_after(
        self, job_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Return a bounded, ordered event page for SSE replay/reconnect."""

        self._validate_job_id(job_id)
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int):
            raise SimulationStoreError(
                "invalid_event_cursor", "Event cursor must be a non-negative integer."
            )
        if after_sequence < 0:
            raise SimulationStoreError(
                "invalid_event_cursor", "Event cursor must be a non-negative integer."
            )
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise SimulationStoreError(
                "invalid_event_cursor", "Event page limit must be an integer."
            )
        page_limit = max(1, min(500, limit))
        with self._connect() as connection:
            self._job_row(connection, job_id)
            rows = connection.execute(
                """
                SELECT sequence, timestamp, event_type, status, step_id,
                       message, details_json
                FROM job_event
                WHERE job_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (job_id, after_sequence, page_limit),
            ).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "timestamp": row["timestamp"],
                "type": row["event_type"],
                "status": row["status"],
                "step_id": row["step_id"],
                "message": row["message"],
                "details": self._decode_event_details(row["details_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _decode_event_details(value: str | None) -> dict[str, Any]:
        import json

        if not value:
            return {}
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def next_queued_job(self) -> dict[str, Any] | None:
        """Return the oldest queued simulation without changing its state."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM simulation_job
                WHERE status = 'QUEUED'
                ORDER BY queued_at ASC, created_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            return self._hydrate_job(connection, row["id"], include_events=False)

    def active_job_count(self) -> int:
        placeholders = ",".join("?" for _ in _ACTIVE_JOB_STATUSES)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM simulation_job WHERE status IN ({placeholders})",
                _ACTIVE_JOB_STATUSES,
            ).fetchone()
        return int(row["count"])

    def claim_job(
        self, job_id: str, worker_id: str, *, max_active_jobs: int = 1
    ) -> dict[str, Any] | None:
        """Atomically claim one known queued job if the concurrency slot is free."""

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

    def reject_queued_job(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fail a queued job before any step starts when admission is impossible."""

        self._validate_job_id(job_id)
        if not isinstance(code, str) or not code.strip():
            raise SimulationStoreError("invalid_error", "Failure code must be non-empty.")
        if not isinstance(message, str) or not message.strip():
            raise SimulationStoreError("invalid_error", "Failure message must be non-empty.")
        now = _utc_now()
        with self._transaction() as connection:
            row = self._job_row(connection, job_id)
            if row["status"] != "QUEUED":
                raise SimulationStoreError(
                    "job_state_invalid", "Only a queued simulation can fail admission."
                )
            connection.execute(
                """
                UPDATE simulation_job
                SET status = 'FAILED', finished_at = ?, worker_id = NULL,
                    current_step_id = NULL, error_code = ?, error_message = ?
                WHERE id = ?
                """,
                (now, code.strip(), message.strip(), job_id),
            )
            self._append_event(
                connection,
                job_id,
                event_type="resource_preflight_failed",
                status="FAILED",
                message=message.strip(),
                details={"code": code.strip(), **(details or {})},
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
        """Append one safe structured system event and return it."""

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
        return self.events_after(job_id, after_sequence=0, limit=500)[-1]

    @staticmethod
    def _validate_specification(expected_key: str, specification: Any) -> None:
        if not isinstance(specification, dict):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification is not a JSON object.",
            )
        if not _SPEC_KEY_RE.fullmatch(expected_key) or specification.get(
            "specification_key"
        ) != expected_key:
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification key does not match its content.",
            )
        if specification.get("immutable") is not True or specification.get(
            "execution_started"
        ) is not False:
            raise SimulationStoreError(
                "specification_integrity_error",
                "Simulation jobs require an immutable specification that has not started execution.",
            )

        identity = specification.get("identity")
        if not isinstance(identity, dict):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification has no valid identity object.",
            )

        steps = identity.get("steps")
        if not isinstance(steps, list):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification has no valid step contracts.",
            )
        step_ids: list[str] = []
        for contract in steps:
            if not isinstance(contract, dict):
                raise SimulationStoreError(
                    "specification_integrity_error",
                    "Every immutable pipeline step contract must be an object.",
                )
            step_id = contract.get("id")
            if not isinstance(step_id, str) or step_id not in _EXPECTED_STEP_IDS:
                raise SimulationStoreError(
                    "specification_integrity_error",
                    "The immutable pipeline specification contains an unknown step.",
                )
            step_ids.append(step_id)
        if tuple(step_ids) != _EXPECTED_STEP_IDS:
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification must contain all pipeline steps once and in order.",
            )

        era5_input = identity.get("era5_input")
        if not isinstance(era5_input, dict):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification has no ERA5 input dataset.",
            )
        plan_key = era5_input.get("plan_key")
        if not isinstance(plan_key, str) or not _PLAN_KEY_RE.fullmatch(plan_key):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification contains an invalid ERA5 plan key.",
            )
        provenance = era5_input.get("provenance")
        files = era5_input.get("files")
        if (
            not isinstance(provenance, dict)
            or provenance.get("artificial_weather_data") is not False
            or not isinstance(files, list)
            or not files
        ):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification must reference verified real ERA5 files.",
            )

        runtime = identity.get("runtime")
        if not isinstance(runtime, dict):
            raise SimulationStoreError(
                "specification_integrity_error",
                "The immutable pipeline specification has no runtime snapshots.",
            )
        for runtime_name in _REQUIRED_RUNTIMES:
            value = runtime.get(runtime_name)
            if not isinstance(value, dict):
                raise SimulationStoreError(
                    "specification_integrity_error",
                    f"The immutable pipeline specification has no {runtime_name} runtime snapshot.",
                )
            reference = value.get("reference")
            identity_value = value.get("identity")
            if not isinstance(reference, str) or not reference.strip():
                raise SimulationStoreError(
                    "specification_integrity_error",
                    f"The {runtime_name} runtime reference is invalid.",
                )
            if not isinstance(identity_value, str) or not _RUNTIME_ID_RE.fullmatch(
                identity_value
            ):
                raise SimulationStoreError(
                    "specification_integrity_error",
                    f"The {runtime_name} runtime identity must be a pinned SHA-256 digest.",
                )

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SimulationStoreError(
                "invalid_artifact", "Artifact path must be non-empty."
            )
        text = value.strip()
        if "\x00" in text or "\\" in text:
            raise SimulationStoreError(
                "invalid_artifact",
                "Artifact path must use an unambiguous relative POSIX path.",
            )
        raw_parts = text.split("/")
        posix_path = PurePosixPath(text)
        windows_path = PureWindowsPath(text)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or any(part in {"", ".", ".."} for part in raw_parts)
        ):
            raise SimulationStoreError(
                "invalid_artifact",
                "Artifact path must stay inside the simulation directory.",
            )
        return "/".join(raw_parts)


__all__ = ["SCHEMA_VERSION", "SimulationStore", "SimulationStoreError"]
