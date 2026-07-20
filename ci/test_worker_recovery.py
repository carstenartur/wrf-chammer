#!/usr/bin/env python3
"""Regression test for recovery after a worker restarts with the same id."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from workbench.job_store import JobStore  # noqa: E402
from workbench.job_worker import JobWorker  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="wrf-worker-recovery-") as temporary:
        root = Path(temporary)
        (root / "workbench-runs").mkdir()
        store = JobStore(root / "workbench-runs" / "jobs.sqlite3")
        config = {
            "id": "reused-worker-job",
            "mode": "dry-run",
            "name": "Recovery test",
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
        store.create_job(
            "reused-worker-job",
            config,
            "workbench-runs/persistent/reused-worker-job",
        )
        store.register_worker("stable-worker", 111111)
        assert store.claim_next("stable-worker")["state"] == "SIMULATING"

        # The restarted process registers the same stable id before recovery.
        restarted = JobWorker(root, store, worker_id="stable-worker", poll_seconds=0.05)
        store.register_worker("stable-worker", 222222)
        assert restarted.recover() == ["reused-worker-job"]
        recovered = store.get_job("reused-worker-job")
        assert recovered["state"] == "FAILED"
        assert recovered["error"]["code"] == "PROCESS_CRASH"

    print("Reused worker id recovery test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
