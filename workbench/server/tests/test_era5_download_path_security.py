#!/usr/bin/env python3
"""Security and concurrency regressions for ERA5 job persistence."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from workbench.era5_download_manager import (
    Era5DownloadManager,
    Era5DownloadManagerError,
)
from workbench.era5_service import Era5DataService

REPO_ROOT = Path(__file__).resolve().parents[3]


class Era5DownloadPersistenceSafetyTests(unittest.TestCase):
    def test_user_job_id_never_enters_a_path_expression(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-manager-safe-path-") as temporary:
            service = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = Era5DownloadManager(REPO_ROOT, service)
            try:
                with self.assertRaises(Era5DownloadManagerError) as context:
                    manager.events("../../etc/passwd")
                self.assertEqual("download_not_found", context.exception.code)
            finally:
                manager.close()

    def test_concurrent_event_appends_have_unique_ordered_sequences(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-manager-events-") as temporary:
            service = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = Era5DownloadManager(REPO_ROOT, service)
            plan_key = "a" * 64
            job_id = f"era5-{plan_key[:12]}-{'b' * 10}"
            state = {"plan_key": plan_key, "id": job_id, "status": "RUNNING"}
            manager._job_directory(plan_key, job_id).mkdir(parents=True)
            try:
                threads = [
                    threading.Thread(
                        target=manager._append_event,
                        args=(state, "test", f"event-{index}"),
                    )
                    for index in range(24)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())

                events_path = manager._job_directory(plan_key, job_id) / "events.jsonl"
                events = [json.loads(line) for line in events_path.read_text().splitlines()]
                self.assertEqual(list(range(1, 25)), [event["sequence"] for event in events])
                self.assertEqual(24, len({event["message"] for event in events}))
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
