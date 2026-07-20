#!/usr/bin/env python3
"""Versioned resource telemetry and admission checks for persistent jobs."""

from __future__ import annotations

import json
import math
import os
import resource
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

RESOURCE_SCHEMA_VERSION = 2
GIB = 1024**3


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _decode(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def memory_snapshot() -> dict[str, int | None]:
    total: int | None = None
    available: int | None = None
    path = Path("/proc/meminfo")
    if path.is_file():
        try:
            values: dict[str, int] = {}
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts and parts[0].isdigit():
                    values[key] = int(parts[0]) * 1024
            total = values.get("MemTotal")
            available = values.get("MemAvailable") or values.get("MemFree")
        except OSError:
            pass
    if total is None:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int):
                total = pages * page_size
        except (AttributeError, OSError, ValueError):
            pass
    return {"total_bytes": total, "available_bytes": available}


def child_usage() -> dict[str, float]:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    maximum_rss = float(usage.ru_maxrss)
    # Linux reports KiB; macOS reports bytes.
    if maximum_rss > 0 and maximum_rss < 10_000_000:
        maximum_rss *= 1024
    return {
        "user_cpu_seconds": float(usage.ru_utime),
        "system_cpu_seconds": float(usage.ru_stime),
        "maximum_resident_bytes": maximum_rss,
    }


class JobResourceStore:
    """Telemetry tables sharing the persistent job SQLite database."""

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
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?",
                (RESOURCE_SCHEMA_VERSION,),
            ).fetchone()
            if applied:
                return
            connection.execute("BEGIN IMMEDIATE")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES simulation_jobs(job_id) ON DELETE CASCADE,
                    attempt INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    worker_id TEXT,
                    logical_cores INTEGER,
                    total_memory_bytes INTEGER,
                    available_memory_bytes INTEGER,
                    free_disk_bytes INTEGER,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS resource_measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES simulation_jobs(job_id) ON DELETE CASCADE,
                    attempt INTEGER NOT NULL,
                    metric TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS runtime_snapshots_job_idx
                    ON runtime_snapshots(job_id, attempt, id);
                CREATE INDEX IF NOT EXISTS resource_measurements_job_idx
                    ON resource_measurements(job_id, attempt, id);
                """
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (RESOURCE_SCHEMA_VERSION, _utc_now()),
            )
            connection.commit()

    def capture_snapshot(
        self,
        job_id: str,
        attempt: int,
        phase: str,
        repo_root: Path,
        *,
        worker_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        memory = memory_snapshot()
        disk = shutil.disk_usage(repo_root / "workbench-runs")
        payload = {
            "phase": phase,
            "captured_at": _utc_now(),
            "worker_id": worker_id,
            "logical_cores": os.cpu_count() or 1,
            "total_memory_bytes": memory["total_bytes"],
            "available_memory_bytes": memory["available_bytes"],
            "free_disk_bytes": int(disk.free),
            "details": details or {},
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_snapshots(
                    job_id, attempt, phase, captured_at, worker_id, logical_cores,
                    total_memory_bytes, available_memory_bytes, free_disk_bytes,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    int(attempt),
                    phase,
                    payload["captured_at"],
                    worker_id,
                    payload["logical_cores"],
                    payload["total_memory_bytes"],
                    payload["available_memory_bytes"],
                    payload["free_disk_bytes"],
                    _json(payload["details"]),
                ),
            )
        return payload

    def add_measurement(
        self,
        job_id: str,
        attempt: int,
        metric: str,
        value: float,
        unit: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not math.isfinite(float(value)):
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO resource_measurements(
                    job_id, attempt, metric, value, unit, captured_at, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    int(attempt),
                    metric,
                    float(value),
                    unit,
                    _utc_now(),
                    _json(details or {}),
                ),
            )

    def record_usage_delta(
        self,
        job_id: str,
        attempt: int,
        before: dict[str, float],
        after: dict[str, float],
        elapsed_seconds: float,
        *,
        exit_code: int | None,
    ) -> None:
        for metric in ("user_cpu_seconds", "system_cpu_seconds"):
            self.add_measurement(
                job_id,
                attempt,
                metric,
                max(0.0, after[metric] - before[metric]),
                "seconds",
            )
        self.add_measurement(
            job_id,
            attempt,
            "maximum_resident_bytes",
            max(before["maximum_resident_bytes"], after["maximum_resident_bytes"]),
            "bytes",
        )
        self.add_measurement(job_id, attempt, "wall_clock_seconds", elapsed_seconds, "seconds")
        if exit_code is not None:
            self.add_measurement(job_id, attempt, "exit_code", float(exit_code), "code")

    def resources(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            snapshots = connection.execute(
                "SELECT * FROM runtime_snapshots WHERE job_id = ? ORDER BY attempt, id",
                (job_id,),
            ).fetchall()
            measurements = connection.execute(
                "SELECT * FROM resource_measurements WHERE job_id = ? ORDER BY attempt, id",
                (job_id,),
            ).fetchall()
        return {
            "snapshots": [
                {
                    "id": int(row["id"]),
                    "attempt": int(row["attempt"]),
                    "phase": row["phase"],
                    "captured_at": row["captured_at"],
                    "worker_id": row["worker_id"],
                    "logical_cores": row["logical_cores"],
                    "total_memory_bytes": row["total_memory_bytes"],
                    "available_memory_bytes": row["available_memory_bytes"],
                    "free_disk_bytes": row["free_disk_bytes"],
                    "details": _decode(row["details_json"]),
                }
                for row in snapshots
            ],
            "measurements": [
                {
                    "id": int(row["id"]),
                    "attempt": int(row["attempt"]),
                    "metric": row["metric"],
                    "value": float(row["value"]),
                    "unit": row["unit"],
                    "captured_at": row["captured_at"],
                    "details": _decode(row["details_json"]),
                }
                for row in measurements
            ],
        }


def resource_requirements(config: dict[str, Any]) -> dict[str, int | None]:
    metadata = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
    estimate = metadata.get("resource_estimate") if isinstance(metadata.get("resource_estimate"), dict) else {}
    ram = estimate.get("estimated_ram_gb") if isinstance(estimate.get("estimated_ram_gb"), dict) else {}
    storage = estimate.get("estimated_storage_gb") if isinstance(estimate.get("estimated_storage_gb"), dict) else {}
    try:
        ram_gb = float(ram.get("minimum"))
    except (TypeError, ValueError):
        ram_gb = 0.0
    try:
        disk_gb = float(storage.get("working_total"))
    except (TypeError, ValueError):
        disk_gb = 0.0
    return {
        "minimum_memory_bytes": int(ram_gb * 1_000_000_000) if ram_gb > 0 else None,
        "working_disk_bytes": int(disk_gb * 1_000_000_000) if disk_gb > 0 else None,
    }


def evaluate_admission(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    requirements = resource_requirements(config)
    memory = memory_snapshot()
    disk = shutil.disk_usage(repo_root / "workbench-runs")
    reserve_gb = float(os.environ.get("WRF_CHAMMER_DISK_RESERVE_GB", "2"))
    reserve_bytes = max(0, int(reserve_gb * GIB))
    available_memory = memory["available_bytes"] or memory["total_bytes"]
    memory_required = requirements["minimum_memory_bytes"]
    disk_required = requirements["working_disk_bytes"]

    reasons: list[dict[str, Any]] = []
    if memory_required is not None and available_memory is not None and memory_required > available_memory:
        reasons.append({
            "resource": "memory",
            "required_bytes": memory_required,
            "available_bytes": available_memory,
        })
    usable_disk = max(0, int(disk.free) - reserve_bytes)
    if disk_required is not None and disk_required > usable_disk:
        reasons.append({
            "resource": "disk",
            "required_bytes": disk_required,
            "available_bytes": usable_disk,
            "reserved_bytes": reserve_bytes,
        })
    return {
        "admitted": not reasons,
        "requirements": requirements,
        "available": {
            "memory_bytes": available_memory,
            "disk_bytes": usable_disk,
            "logical_cores": os.cpu_count() or 1,
        },
        "reasons": reasons,
        "estimate_available": memory_required is not None or disk_required is not None,
    }
