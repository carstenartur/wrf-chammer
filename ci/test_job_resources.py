#!/usr/bin/env python3
"""Offline tests for resource migration, admission and measurements."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from workbench.job_resources import (  # noqa: E402
    JobResourceStore,
    evaluate_admission,
)
from workbench.job_store import JobStore  # noqa: E402
from workbench.job_worker import JobWorker  # noqa: E402


def config(job_id: str, *, ram_gb: float | None = None, disk_gb: float | None = None) -> dict:
    metadata = {}
    if ram_gb is not None or disk_gb is not None:
        metadata["resource_estimate"] = {
            "estimated_ram_gb": {"minimum": ram_gb or 0, "recommended": ram_gb or 0},
            "estimated_storage_gb": {"working_total": disk_gb or 0},
        }
    return {
        "id": job_id,
        "mode": "dry-run",
        "name": job_id,
        "period": {
            "start": "2013-12-05T12:00:00Z",
            "end": "2013-12-05T13:00:00Z",
        },
        "domain": {
            "label": "test",
            "center_lat": 54,
            "center_lon": 9,
            "dx_km": 27,
            "dy_km": 27,
            "e_we": 10,
            "e_sn": 10,
        },
        "inputs": {"source": "none"},
        "outputs": {"directory": "ignored"},
        "metadata": metadata,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="wrf-job-resources-") as temporary:
        root = Path(temporary)
        repo = root / "repo"
        (repo / "workbench").mkdir(parents=True)
        (repo / "workbench-runs").mkdir(parents=True)
        script = repo / "workbench" / "run.sh"
        script.write_text("#!/bin/sh\nset -eu\nsleep 0.05\n", encoding="utf-8")
        script.chmod(0o755)

        database = repo / "workbench-runs" / "jobs.sqlite3"
        store = JobStore(database)
        resources = JobResourceStore(database)
        with sqlite3.connect(database) as connection:
            versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            assert versions == {1, 2}
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        assert "runtime_snapshots" in tables
        assert "resource_measurements" in tables

        no_estimate = evaluate_admission(config("no-estimate"), repo)
        assert no_estimate["admitted"] is True
        assert no_estimate["estimate_available"] is False

        impossible = evaluate_admission(
            config("impossible", ram_gb=1_000_000, disk_gb=1_000_000), repo
        )
        assert impossible["admitted"] is False
        assert {reason["resource"] for reason in impossible["reasons"]} == {"memory", "disk"}

        store.create_job(
            "resource-success",
            config("resource-success"),
            "workbench-runs/persistent/resource-success",
        )
        worker = JobWorker(repo, store, worker_id="resource-worker", poll_seconds=0.02)
        assert worker.run(once=True) == 0
        job = store.get_job("resource-success")
        assert job["state"] == "SUCCEEDED"
        telemetry = resources.resources("resource-success")
        assert [entry["phase"] for entry in telemetry["snapshots"]] == [
            "preflight",
            "finished",
        ]
        metrics = {entry["metric"] for entry in telemetry["measurements"]}
        assert {
            "user_cpu_seconds",
            "system_cpu_seconds",
            "maximum_resident_bytes",
            "wall_clock_seconds",
            "exit_code",
        }.issubset(metrics)

        store.create_job(
            "resource-rejected",
            config("resource-rejected", ram_gb=1_000_000, disk_gb=1_000_000),
            "workbench-runs/persistent/resource-rejected",
        )
        assert JobWorker(
            repo, store, worker_id="reject-worker", poll_seconds=0.02
        ).run(once=True) == 0
        rejected = store.get_job("resource-rejected")
        assert rejected["state"] == "FAILED"
        assert rejected["error"]["code"] == "INSUFFICIENT_RESOURCES"
        rejected_resources = resources.resources("resource-rejected")
        assert rejected_resources["snapshots"][0]["phase"] == "preflight"
        assert any(
            measurement["metric"] == "admission_rejected"
            for measurement in rejected_resources["measurements"]
        )

    print("Job resource migration and preflight tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
