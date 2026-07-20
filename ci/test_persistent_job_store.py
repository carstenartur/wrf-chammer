#!/usr/bin/env python3
"""Offline tests for persistent job state, worker execution, cancel and recovery."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from workbench.job_store import JobConflictError, JobStore  # noqa: E402
from workbench.job_worker import JobWorker  # noqa: E402


def make_repo(root: Path, script: str) -> Path:
    repo = root / "repo"
    (repo / "workbench").mkdir(parents=True)
    (repo / "workbench-runs" / "persistent").mkdir(parents=True)
    run_script = repo / "workbench" / "run.sh"
    run_script.write_text(script, encoding="utf-8")
    run_script.chmod(0o755)
    return repo


def config(job_id: str) -> dict:
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
    }


def wait_state(store: JobStore, job_id: str, state: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.get_job(job_id)["state"] == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"Job {job_id} did not reach {state}: {store.get_job(job_id)}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="wrf-persistent-jobs-") as temporary:
        temp = Path(temporary)

        database = temp / "state" / "jobs.sqlite3"
        store = JobStore(database)
        draft = store.create_job(
            "draft-job",
            config("draft-job"),
            "workbench-runs/persistent/draft-job",
            enqueue=False,
        )
        assert draft["state"] == "DRAFT"
        assert store.enqueue("draft-job")["state"] == "QUEUED"

        queued = store.create_job(
            "priority-job",
            config("priority-job"),
            "workbench-runs/persistent/priority-job",
            priority=10,
        )
        assert queued["state"] == "QUEUED"
        assert store.claim_next("queue-worker")["job_id"] == "priority-job"
        store.complete(
            "priority-job",
            state="FAILED",
            exit_code=2,
            log_path="logs/worker-run.log",
            error_code="PROCESS_CRASH",
            error_message="test failure",
        )
        retried = store.retry("priority-job")
        assert retried["state"] == "QUEUED"
        assert retried["attempt"] == 2
        assert len(retried["steps"]) == 2

        reopened = JobStore(database)
        assert reopened.get_job("priority-job")["attempt"] == 2
        assert [event["event_type"] for event in reopened.events("priority-job")] == [
            "job-created",
            "job-claimed",
            "job-finished",
            "job-retried",
        ]
        try:
            reopened.create_job(
                "priority-job",
                config("priority-job"),
                "workbench-runs/persistent/priority-job",
            )
        except JobConflictError:
            pass
        else:
            raise AssertionError("Duplicate persistent job id was accepted")

        success_repo = make_repo(
            temp / "success",
            """#!/bin/sh
set -eu
cfg="$1"
out=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["outputs"]["directory"])' "$cfg")
mkdir -p "$out/outputs" "$out/visualizations"
echo result > "$out/outputs/result.txt"
echo '{"format":"test"}' > "$out/visualizations/metadata.json"
""",
        )
        success_store = JobStore(success_repo / "workbench-runs" / "jobs.sqlite3")
        success_store.create_job(
            "success-job",
            config("success-job"),
            "workbench-runs/persistent/success-job",
        )
        assert JobWorker(
            success_repo,
            success_store,
            worker_id="success-worker",
            poll_seconds=0.05,
        ).run(once=True) == 0
        success = success_store.get_job("success-job")
        assert success["state"] == "SUCCEEDED"
        paths = {artifact["relative_path"] for artifact in success["artifacts"]}
        assert "logs/worker-run.log" in paths
        assert "outputs/result.txt" in paths
        assert "visualizations/metadata.json" in paths
        assert all(artifact["sha256"] for artifact in success["artifacts"])

        cancel_repo = make_repo(
            temp / "cancel",
            """#!/bin/sh
trap 'exit 143' TERM INT
sleep 30
""",
        )
        cancel_store = JobStore(cancel_repo / "workbench-runs" / "jobs.sqlite3")
        cancel_store.create_job(
            "cancel-job",
            config("cancel-job"),
            "workbench-runs/persistent/cancel-job",
        )
        worker = JobWorker(
            cancel_repo,
            cancel_store,
            worker_id="cancel-worker",
            poll_seconds=0.05,
            cancel_grace_seconds=0.2,
        )
        thread = threading.Thread(target=lambda: worker.run(once=True), daemon=True)
        thread.start()
        wait_state(cancel_store, "cancel-job", "SIMULATING")
        assert cancel_store.request_cancel("cancel-job")["state"] == "CANCELLING"
        thread.join(timeout=5)
        assert not thread.is_alive()
        cancelled = cancel_store.get_job("cancel-job")
        assert cancelled["state"] == "CANCELLED"
        assert cancelled["error"]["code"] == "CANCELLED_BY_USER"

        recovery_store = JobStore(temp / "recovery" / "jobs.sqlite3")
        recovery_store.create_job(
            "orphan-job",
            config("orphan-job"),
            "workbench-runs/persistent/orphan-job",
        )
        recovery_store.register_worker("dead-worker", 999999)
        assert recovery_store.claim_next("dead-worker")["state"] == "SIMULATING"
        recovery_store.unregister_worker("dead-worker")
        assert recovery_store.recover_orphaned_jobs(set()) == ["orphan-job"]
        orphan = recovery_store.get_job("orphan-job")
        assert orphan["state"] == "FAILED"
        assert orphan["error"]["code"] == "PROCESS_CRASH"

    print("Persistent job store and worker tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
