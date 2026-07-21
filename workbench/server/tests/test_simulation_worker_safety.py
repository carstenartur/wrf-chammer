#!/usr/bin/env python3
"""Safety regressions for persistent simulation worker shutdown and metadata."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from workbench.server.tests.test_simulation_worker import (
    SPEC_KEY,
    prepare_environment,
    write_blocking_executor,
)
from workbench.simulation_worker import (
    ExternalStepExecutor,
    SimulationWorker,
    _max_rss_bytes,
)


class SimulationWorkerSafetyTests(unittest.TestCase):
    def test_invalid_checksum_metadata_is_classified(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-worker-metadata-") as temporary:
            root = Path(temporary)
            data, specifications, store = prepare_environment(root)
            specifications.specification["identity"]["era5_input"]["files"][0][
                "sha256"
            ] = None
            job = store.create_job(SPEC_KEY)
            store.enqueue_job(job["id"])
            worker = SimulationWorker(
                root,
                store,
                data,
                specifications,  # type: ignore[arg-type]
                worker_id="worker-invalid-metadata",
                executor=ExternalStepExecutor(None),
                poll_seconds=0.02,
            )
            worker.run(once=True)
            result = store.get_job(job["id"])
            self.assertEqual("FAILED", result["status"])
            self.assertEqual("INPUT_DATA_MISSING", result["error"]["code"])
            self.assertNotEqual("WORKER_ERROR", result["error"]["code"])

    def test_worker_shutdown_is_not_user_cancellation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-worker-interrupt-") as temporary:
            root = Path(temporary)
            data, specifications, store = prepare_environment(root)
            executor_path = root / "blocking-executor.py"
            write_blocking_executor(executor_path)
            job = store.create_job(SPEC_KEY)
            store.enqueue_job(job["id"])
            worker = SimulationWorker(
                root,
                store,
                data,
                specifications,  # type: ignore[arg-type]
                worker_id="worker-interrupted",
                executor=ExternalStepExecutor(
                    executor_path, poll_seconds=0.02, cancel_grace_seconds=0.15
                ),
                poll_seconds=0.02,
            )
            thread = threading.Thread(target=lambda: worker.run(once=True))
            thread.start()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = store.get_job(job["id"])
                if (
                    current["current_step_id"] == "geogrid"
                    and current["status"] == "PREPROCESSING"
                ):
                    break
                time.sleep(0.02)
            else:
                self.fail(f"geogrid executor did not start: {store.get_job(job['id'])}")

            worker.request_stop()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            result = store.get_job(job["id"])
            self.assertEqual("FAILED", result["status"])
            self.assertEqual("worker_interrupted", result["error"]["code"])
            self.assertNotEqual("CANCELLED", result["status"])

    def test_max_rss_unit_is_platform_specific(self) -> None:
        self.assertEqual(4096, _max_rss_bytes(4, platform="linux"))
        self.assertEqual(4, _max_rss_bytes(4, platform="darwin"))
        self.assertEqual(0, _max_rss_bytes(-10, platform="linux"))
        self.assertEqual(0, _max_rss_bytes("invalid", platform="linux"))


if __name__ == "__main__":
    unittest.main()
