#!/usr/bin/env python3
"""Persistent SQLite job state for local WRF Workbench workers."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

JOB_STATES = {
    "DRAFT",
    "VALIDATING",
    "WAITING_FOR_DATA",
    "DOWNLOADING_DATA",
    "READY",
    "QUEUED",
    "PREPROCESSING",
    "INITIALIZING",
    "SIMULATING",
    "POSTPROCESSING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLING",
    "CANCELLED",
}
ACTIVE_STATES = {
    "VALIDATING",
    "DOWNLOADING_DATA",
    "PREPROCESSING",
    "INITIALIZING",
    "SIMULATING",
    "POSTPROCESSING",
    "CANCELLING",
}
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
RETRYABLE_STATES = {"FAILED", "CANCELLED"}
TRANSITIONS = {
    "DRAFT": {"VALIDATING", "QUEUED", "CANCELLED"},
    "VALIDATING": {"WAITING_FOR_DATA", "READY", "QUEUED", "FAILED", "CANCELLING"},
    "WAITING_FOR_DATA": {"DOWNLOADING_DATA", "READY", "QUEUED", "CANCELLED"},
    "DOWNLOADING_DATA": {"READY", "FAILED", "CANCELLING"},
    "READY": {"QUEUED", "CANCELLED"},
    "QUEUED": {"PREPROCESSING", "INITIALIZING", "SIMULATING", "CANCELLED"},
    "PREPROCESSING": {"INITIALIZING", "FAILED", "CANCELLING"},
    "INITIALIZING": {"SIMULATING", "FAILED", "CANCELLING"},
    "SIMULATING": {"POSTPROCESSING", "SUCCEEDED", "FAILED", "CANCELLING"},
    "POSTPROCESSING": {"SUCCEEDED", "FAILED", "CANCELLING"},
    "CANCELLING": {"CANCELLED", "FAILED"},
    "SUCCEEDED": set(),
    "FAILED": {"QUEUED"},
    "CANCELLED": {"QUEUED"},
}


class JobStoreError(RuntimeError):
    """Base class for persistent job-store failures."""


class JobNotFoundError(JobStoreError):
    pass


class JobConflictError(JobStoreError):
    pass


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _decode(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class JobStore:
    """Connection-per-operation SQLite repository shared by API and workers."""

    def __init__(self, database_path: Path):
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def migrate(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 1 in applied:
                return
            connection.executescript(
                """
                CREATE TABLE simulation_jobs (
                    job_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    run_root TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    CHECK (cancel_requested IN (0, 1))
                );

                CREATE TABLE job_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES simulation_jobs(job_id) ON DELETE CASCADE,
                    attempt INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    progress REAL,
                    started_at TEXT,
                    ended_at TEXT,
                    exit_code INTEGER,
                    log_path TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    UNIQUE(job_id, attempt, name)
                );

                CREATE TABLE job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES simulation_jobs(job_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    state TEXT,
                    step_name TEXT,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES simulation_jobs(job_id) ON DELETE CASCADE,
                    attempt INTEGER NOT NULL,
                    artifact_type TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, attempt, relative_path)
                );

                CREATE TABLE workers (
                    worker_id TEXT PRIMARY KEY,
                    process_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at REAL NOT NULL
                );

                CREATE INDEX simulation_jobs_queue_idx
                    ON simulation_jobs(state, priority DESC, created_at);
                CREATE INDEX job_events_job_idx ON job_events(job_id, id);
                CREATE INDEX job_steps_job_idx ON job_steps(job_id, attempt, id);
                CREATE INDEX artifacts_job_idx ON artifacts(job_id, attempt, id);
                """
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )

    def _event(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        state: str | None,
        message: str,
        *,
        step_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO job_events(
                job_id, created_at, event_type, state, step_name, message, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, utc_now(), event_type, state, step_name, message, _json(details or {})),
        )

    def _job_row(self, connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM simulation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise JobNotFoundError(f"Unknown job: {job_id}")
        return row

    def create_job(
        self,
        job_id: str,
        config: dict[str, Any],
        run_root: str,
        *,
        enqueue: bool = True,
        priority: int = 0,
    ) -> dict[str, Any]:
        state = "QUEUED" if enqueue else "DRAFT"
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO simulation_jobs(
                        job_id, state, config_json, run_root, priority, attempt,
                        cancel_requested, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?)
                    """,
                    (job_id, state, _json(config), run_root, int(priority), now, now),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise JobConflictError(f"Job {job_id!r} already exists") from exc
            connection.execute(
                "INSERT INTO job_steps(job_id, attempt, name, state) VALUES (?, 1, 'workbench-run', ?)",
                (job_id, state),
            )
            self._event(
                connection,
                job_id,
                "job-created",
                state,
                "Job was created and queued." if enqueue else "Job draft was created.",
            )
            connection.commit()
        return self.get_job(job_id)

    def exists(self, job_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM simulation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone() is not None

    def enqueue(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._job_row(connection, job_id)
            current = str(row["state"])
            if current not in {"DRAFT", "READY"}:
                connection.rollback()
                raise JobConflictError(f"Job {job_id} cannot be queued from {current}")
            now = utc_now()
            connection.execute(
                "UPDATE simulation_jobs SET state = 'QUEUED', cancel_requested = 0, updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )
            connection.execute(
                "UPDATE job_steps SET state = 'QUEUED' WHERE job_id = ? AND attempt = ? AND name = 'workbench-run'",
                (job_id, int(row["attempt"])),
            )
            self._event(connection, job_id, "job-queued", "QUEUED", "Job entered the worker queue.")
            connection.commit()
        return self.get_job(job_id)

    def claim_next(self, worker_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM simulation_jobs
                WHERE state = 'QUEUED' AND cancel_requested = 0
                ORDER BY priority DESC, created_at, job_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            job_id = str(row["job_id"])
            now = utc_now()
            updated = connection.execute(
                """
                UPDATE simulation_jobs
                SET state = 'SIMULATING', worker_id = ?, updated_at = ?,
                    started_at = COALESCE(started_at, ?)
                WHERE job_id = ? AND state = 'QUEUED' AND cancel_requested = 0
                """,
                (worker_id, now, now, job_id),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.execute(
                """
                UPDATE job_steps
                SET state = 'SIMULATING', started_at = COALESCE(started_at, ?)
                WHERE job_id = ? AND attempt = ? AND name = 'workbench-run'
                """,
                (now, job_id, int(row["attempt"])),
            )
            self._event(
                connection,
                job_id,
                "job-claimed",
                "SIMULATING",
                f"Worker {worker_id} claimed the job.",
                step_name="workbench-run",
            )
            connection.commit()
        return self.get_job(job_id)

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._job_row(connection, job_id)
            state = str(row["state"])
            now = utc_now()
            if state in TERMINAL_STATES:
                connection.rollback()
                raise JobConflictError(f"Job {job_id} is already {state}")
            if state in {"DRAFT", "WAITING_FOR_DATA", "READY", "QUEUED"}:
                new_state, ended_at = "CANCELLED", now
            else:
                new_state, ended_at = "CANCELLING", None
            connection.execute(
                """
                UPDATE simulation_jobs
                SET state = ?, cancel_requested = 1, updated_at = ?, ended_at = ?
                WHERE job_id = ?
                """,
                (new_state, now, ended_at, job_id),
            )
            connection.execute(
                """
                UPDATE job_steps
                SET state = ?, ended_at = CASE WHEN ? = 'CANCELLED' THEN ? ELSE ended_at END
                WHERE job_id = ? AND attempt = ? AND name = 'workbench-run'
                """,
                (new_state, new_state, now, job_id, int(row["attempt"])),
            )
            self._event(
                connection,
                job_id,
                "cancel-requested",
                new_state,
                "Job cancellation was requested.",
                step_name="workbench-run",
            )
            connection.commit()
        return self.get_job(job_id)

    def cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            return bool(self._job_row(connection, job_id)["cancel_requested"])

    def complete(
        self,
        job_id: str,
        *,
        state: str,
        exit_code: int | None,
        log_path: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if state not in TERMINAL_STATES:
            raise JobConflictError(f"Completion state must be terminal, got {state}")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._job_row(connection, job_id)
            current = str(row["state"])
            if current in TERMINAL_STATES:
                connection.rollback()
                return self.get_job(job_id)
            if state == "CANCELLED" and current not in ACTIVE_STATES | {"CANCELLING"}:
                connection.rollback()
                raise JobConflictError(f"Job {job_id} cannot be cancelled from {current}")
            now = utc_now()
            connection.execute(
                """
                UPDATE simulation_jobs
                SET state = ?, worker_id = NULL, cancel_requested = 0,
                    updated_at = ?, ended_at = ?, error_code = ?, error_message = ?
                WHERE job_id = ?
                """,
                (state, now, now, error_code, error_message, job_id),
            )
            connection.execute(
                """
                UPDATE job_steps
                SET state = ?, ended_at = ?, exit_code = ?, log_path = ?,
                    error_code = ?, error_message = ?
                WHERE job_id = ? AND attempt = ? AND name = 'workbench-run'
                """,
                (
                    state,
                    now,
                    exit_code,
                    log_path,
                    error_code,
                    error_message,
                    job_id,
                    int(row["attempt"]),
                ),
            )
            self._event(
                connection,
                job_id,
                "job-finished",
                state,
                "Job completed successfully."
                if state == "SUCCEEDED"
                else "Job was cancelled."
                if state == "CANCELLED"
                else "Job failed.",
                step_name="workbench-run",
                details={"exit_code": exit_code, "error_code": error_code},
            )
            connection.commit()
        return self.get_job(job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._job_row(connection, job_id)
            state = str(row["state"])
            if state not in RETRYABLE_STATES:
                connection.rollback()
                raise JobConflictError(f"Job {job_id} cannot be retried from {state}")
            attempt = int(row["attempt"]) + 1
            now = utc_now()
            connection.execute(
                """
                UPDATE simulation_jobs
                SET state = 'QUEUED', attempt = ?, cancel_requested = 0,
                    worker_id = NULL, updated_at = ?, started_at = NULL,
                    ended_at = NULL, error_code = NULL, error_message = NULL
                WHERE job_id = ?
                """,
                (attempt, now, job_id),
            )
            connection.execute(
                "INSERT INTO job_steps(job_id, attempt, name, state) VALUES (?, ?, 'workbench-run', 'QUEUED')",
                (job_id, attempt),
            )
            self._event(
                connection,
                job_id,
                "job-retried",
                "QUEUED",
                f"Job retry attempt {attempt} entered the queue.",
                step_name="workbench-run",
                details={"attempt": attempt},
            )
            connection.commit()
        return self.get_job(job_id)

    def register_worker(self, worker_id: str, process_id: int) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workers(worker_id, process_id, started_at, heartbeat_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    process_id = excluded.process_id,
                    started_at = excluded.started_at,
                    heartbeat_at = excluded.heartbeat_at
                """,
                (worker_id, int(process_id), now, time.time()),
            )

    def heartbeat(self, worker_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE workers SET heartbeat_at = ? WHERE worker_id = ?",
                (time.time(), worker_id),
            )

    def live_worker_ids(self, *, max_age_seconds: float = 30.0) -> set[str]:
        threshold = time.time() - max(1.0, float(max_age_seconds))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT worker_id FROM workers WHERE heartbeat_at >= ?", (threshold,)
            ).fetchall()
            return {str(row["worker_id"]) for row in rows}

    def unregister_worker(self, worker_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM workers WHERE worker_id = ?", (worker_id,))

    def recover_orphaned_jobs(self, active_worker_ids: Iterable[str]) -> list[str]:
        active = set(active_worker_ids)
        recovered: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM simulation_jobs
                WHERE state IN ('VALIDATING','DOWNLOADING_DATA','PREPROCESSING',
                                'INITIALIZING','SIMULATING','POSTPROCESSING','CANCELLING')
                """
            ).fetchall()
            now = utc_now()
            for row in rows:
                worker_id = row["worker_id"]
                if worker_id and worker_id in active:
                    continue
                job_id = str(row["job_id"])
                message = "The previous worker disappeared before completing the job."
                connection.execute(
                    """
                    UPDATE simulation_jobs
                    SET state = 'FAILED', worker_id = NULL, cancel_requested = 0,
                        updated_at = ?, ended_at = ?, error_code = 'PROCESS_CRASH',
                        error_message = ? WHERE job_id = ?
                    """,
                    (now, now, message, job_id),
                )
                connection.execute(
                    """
                    UPDATE job_steps
                    SET state = 'FAILED', ended_at = ?, error_code = 'PROCESS_CRASH',
                        error_message = ?
                    WHERE job_id = ? AND attempt = ? AND name = 'workbench-run'
                    """,
                    (now, message, job_id, int(row["attempt"])),
                )
                self._event(
                    connection,
                    job_id,
                    "job-recovered",
                    "FAILED",
                    "An orphaned job was marked failed during worker recovery.",
                    step_name="workbench-run",
                    details={"error_code": "PROCESS_CRASH", "previous_worker": worker_id},
                )
                recovered.append(job_id)
            connection.commit()
        return recovered

    def add_artifact(
        self,
        job_id: str,
        *,
        artifact_type: str,
        relative_path: str,
        size_bytes: int,
        sha256: str | None,
    ) -> None:
        with self._connect() as connection:
            row = self._job_row(connection, job_id)
            connection.execute(
                """
                INSERT INTO artifacts(
                    job_id, attempt, artifact_type, relative_path,
                    size_bytes, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, attempt, relative_path) DO UPDATE SET
                    artifact_type = excluded.artifact_type,
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256,
                    created_at = excluded.created_at
                """,
                (
                    job_id,
                    int(row["attempt"]),
                    artifact_type,
                    relative_path,
                    int(size_bytes),
                    sha256,
                    utc_now(),
                ),
            )

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._job_row(connection, job_id)
            steps = connection.execute(
                "SELECT * FROM job_steps WHERE job_id = ? ORDER BY attempt, id", (job_id,)
            ).fetchall()
            artifacts = connection.execute(
                """
                SELECT id, attempt, artifact_type, relative_path, size_bytes, sha256, created_at
                FROM artifacts WHERE job_id = ? ORDER BY attempt, id
                """,
                (job_id,),
            ).fetchall()
            return self._render_job(row, steps=steps, artifacts=artifacts)

    def list_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM simulation_jobs ORDER BY created_at DESC, job_id LIMIT ?",
                (safe_limit,),
            ).fetchall()
            return [self._render_job(row) for row in rows]

    def events(self, job_id: str, *, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._connect() as connection:
            self._job_row(connection, job_id)
            rows = connection.execute(
                """
                SELECT * FROM job_events
                WHERE job_id = ? AND id > ? ORDER BY id LIMIT ?
                """,
                (job_id, int(after_id), safe_limit),
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "job_id": str(row["job_id"]),
                    "created_at": row["created_at"],
                    "event_type": row["event_type"],
                    "state": row["state"],
                    "step_name": row["step_name"],
                    "message": row["message"],
                    "details": _decode(row["details_json"], {}),
                }
                for row in rows
            ]

    def artifacts(self, job_id: str) -> list[dict[str, Any]]:
        return self.get_job(job_id)["artifacts"]

    def _render_job(
        self,
        row: sqlite3.Row,
        *,
        steps: list[sqlite3.Row] | None = None,
        artifacts: list[sqlite3.Row] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": str(row["job_id"]),
            "state": str(row["state"]),
            "config": _decode(row["config_json"], {}),
            "run_root": str(row["run_root"]),
            "priority": int(row["priority"]),
            "attempt": int(row["attempt"]),
            "cancel_requested": bool(row["cancel_requested"]),
            "worker_id": row["worker_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "error": {
                "code": row["error_code"], "message": row["error_message"]
            } if row["error_code"] or row["error_message"] else None,
        }
        if steps is not None:
            payload["steps"] = [
                {
                    "id": int(step["id"]),
                    "attempt": int(step["attempt"]),
                    "name": step["name"],
                    "state": step["state"],
                    "progress": step["progress"],
                    "started_at": step["started_at"],
                    "ended_at": step["ended_at"],
                    "exit_code": step["exit_code"],
                    "log_path": step["log_path"],
                    "error": {
                        "code": step["error_code"], "message": step["error_message"]
                    } if step["error_code"] or step["error_message"] else None,
                }
                for step in steps
            ]
        if artifacts is not None:
            payload["artifacts"] = [
                {
                    "id": int(artifact["id"]),
                    "attempt": int(artifact["attempt"]),
                    "artifact_type": artifact["artifact_type"],
                    "relative_path": artifact["relative_path"],
                    "size_bytes": int(artifact["size_bytes"]),
                    "sha256": artifact["sha256"],
                    "created_at": artifact["created_at"],
                }
                for artifact in artifacts
            ]
        return payload
