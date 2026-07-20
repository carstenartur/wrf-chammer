#!/usr/bin/env python3
"""Tests for persistent SQLite simulation jobs and step state."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from workbench.pipeline_specification_service import PipelineSpecificationServiceError
from workbench.simulation_store import SimulationStore, SimulationStoreError

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_KEY = "a" * 64
STEP_IDS = [
    "input-data",
    "geogrid",
    "ungrib",
    "metgrid",
    "real",
    "wrf",
    "postprocessing",
    "result-indexing",
]


def specification() -> dict:
    return {
        "specification_key": SPEC_KEY,
        "created_at": "2026-07-20T12:00:00Z",
        "immutable": True,
        "execution_started": False,
        "identity": {
            "job": {"id": "xaver-real", "name": "Xaver real simulation"},
            "era5_input": {
                "plan_key": "b" * 64,
                "files": [
                    {
                        "path": "files/surface.grib",
                        "sha256": "c" * 64,
                        "size_bytes": 123,
                        "request_name": "surface",
                    }
                ],
                "provenance": {
                    "source": "Copernicus Climate Data Store ERA5 reanalysis",
                    "artificial_weather_data": False,
                },
            },
            "runtime": {
                "wps": {"reference": "wps:test", "identity": "sha256:" + "d" * 64},
                "wrf": {"reference": "wrf:test", "identity": "sha256:" + "e" * 64},
                "postprocessing": {
                    "reference": "postprocess:test",
                    "identity": "sha256:" + "f" * 64,
                },
            },
            "steps": [
                {
                    "id": step_id,
                    "label": step_id.replace("-", " ").title(),
                    "status": "PENDING",
                    "inputs": [],
                    "outputs": [],
                    "progress_metrics": [],
                }
                for step_id in STEP_IDS
            ],
        },
    }


class FakeSpecificationService:
    def get(self, key: str) -> dict:
        if key != SPEC_KEY:
            raise PipelineSpecificationServiceError(
                "specification_not_found", "Immutable pipeline specification not found."
            )
        return specification()


class SimulationStoreTests(unittest.TestCase):
    def make_store(self, root: Path) -> SimulationStore:
        return SimulationStore(
            REPO_ROOT,
            FakeSpecificationService(),  # type: ignore[arg-type]
            database_path=root / "workbench.sqlite3",
        )

    def test_migration_and_job_materialization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-store-create-") as temporary:
            root = Path(temporary)
            store = self.make_store(root)
            self.assertEqual(1, store.schema_version())
            job = store.create_job(SPEC_KEY)
            self.assertEqual("READY", job["status"])
            self.assertEqual(SPEC_KEY, job["specification_key"])
            self.assertEqual(STEP_IDS, [step["id"] for step in job["steps"]])
            self.assertTrue(all(step["status"] == "PENDING" for step in job["steps"]))
            self.assertEqual("b" * 64, job["input_datasets"][0]["plan_key"])
            self.assertEqual(3, len(job["runtime_snapshots"]))
            self.assertEqual([1], [event["sequence"] for event in job["events"]])

            with sqlite3.connect(root / "workbench.sqlite3") as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertTrue(
                {
                    "simulation_job",
                    "job_step",
                    "job_event",
                    "artifact",
                    "input_dataset",
                    "runtime_snapshot",
                    "resource_measurement",
                    "schema_migration",
                }.issubset(tables)
            )

    def test_fifo_claim_and_full_step_progression(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-store-flow-") as temporary:
            store = self.make_store(Path(temporary))
            first = store.enqueue_job(store.create_job(SPEC_KEY)["id"])
            second = store.enqueue_job(store.create_job(SPEC_KEY)["id"])
            claimed = store.claim_next_job("worker-one")
            self.assertIsNotNone(claimed)
            assert claimed is not None
            self.assertEqual(first["id"], claimed["id"])
            self.assertEqual("PREPROCESSING", claimed["status"])
            self.assertEqual("input-data", claimed["current_step_id"])
            self.assertEqual("QUEUED", store.get_job(second["id"])["status"])

            current = claimed
            while current["status"] != "SUCCEEDED":
                step_id = current["current_step_id"]
                self.assertIsNotNone(step_id)
                store.update_step_progress(
                    current["id"],
                    step_id,
                    {"completed": 1, "total": 1},
                )
                current = store.complete_step(current["id"], step_id)
                if current["current_step_id"] == "real":
                    self.assertEqual("INITIALIZING", current["status"])
                if current["current_step_id"] == "wrf":
                    self.assertEqual("SIMULATING", current["status"])
                if current["current_step_id"] == "postprocessing":
                    self.assertEqual("POSTPROCESSING", current["status"])

            self.assertEqual("SUCCEEDED", current["status"])
            self.assertTrue(all(step["status"] == "SUCCEEDED" for step in current["steps"]))
            sequences = [event["sequence"] for event in current["events"]]
            self.assertEqual(list(range(1, len(sequences) + 1)), sequences)

    def test_concurrent_claim_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-store-claim-") as temporary:
            store = self.make_store(Path(temporary))
            job = store.enqueue_job(store.create_job(SPEC_KEY)["id"])
            results: list[dict | None] = []
            errors: list[BaseException] = []
            barrier = threading.Barrier(3)

            def claim(worker: str) -> None:
                try:
                    barrier.wait(timeout=5)
                    results.append(store.claim_next_job(worker))
                except BaseException as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(target=claim, args=("worker-a",)),
                threading.Thread(target=claim, args=("worker-b",)),
            ]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

            self.assertEqual([], errors)
            claimed = [result for result in results if result is not None]
            self.assertEqual(1, len(claimed))
            self.assertEqual(job["id"], claimed[0]["id"])
            self.assertEqual(1, len([result for result in results if result is None]))

    def test_failure_retry_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-store-retry-") as temporary:
            root = Path(temporary)
            store = self.make_store(root)
            job = store.enqueue_job(store.create_job(SPEC_KEY)["id"])
            running = store.claim_next_job("worker-failure")
            assert running is not None
            failed = store.fail_step(
                job["id"],
                "input-data",
                code="INPUT_DATA_MISSING",
                message="Verified input disappeared before execution.",
            )
            self.assertEqual("FAILED", failed["status"])
            self.assertTrue(failed["retryable"])
            retried = store.retry_job(job["id"])
            self.assertEqual(job["id"], retried["retry_of"])
            self.assertEqual("READY", retried["status"])

            active = store.enqueue_job(store.create_job(SPEC_KEY)["id"])
            store.claim_next_job("worker-interrupted")
            reopened = self.make_store(root)
            recovered = reopened.recover_interrupted_jobs()
            self.assertIn(active["id"], recovered)
            recovered_job = reopened.get_job(active["id"])
            self.assertEqual("FAILED", recovered_job["status"])
            self.assertEqual("worker_interrupted", recovered_job["error"]["code"])

    def test_cancel_before_and_during_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-store-cancel-") as temporary:
            store = self.make_store(Path(temporary))
            queued = store.enqueue_job(store.create_job(SPEC_KEY)["id"])
            cancelled = store.request_cancel(queued["id"])
            self.assertEqual("CANCELLED", cancelled["status"])
            self.assertTrue(all(step["status"] == "CANCELLED" for step in cancelled["steps"]))

            active = store.enqueue_job(store.create_job(SPEC_KEY)["id"])
            store.claim_next_job("worker-cancel")
            cancelling = store.request_cancel(active["id"])
            self.assertEqual("CANCELLING", cancelling["status"])
            final = store.finalize_cancel(active["id"])
            self.assertEqual("CANCELLED", final["status"])
            self.assertIsNone(final["current_step_id"])

    def test_artifacts_and_measurements_are_persistent_and_path_safe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-store-artifacts-") as temporary:
            root = Path(temporary)
            store = self.make_store(root)
            job = store.create_job(SPEC_KEY)
            artifact = store.add_artifact(
                job["id"],
                step_id="input-data",
                kind="verified-input-manifest",
                relative_path="artifacts/input/manifest.json",
                sha256="1" * 64,
                size_bytes=42,
                metadata={"verified": True},
            )
            self.assertEqual("artifacts/input/manifest.json", artifact["relative_path"])
            measurement = store.add_resource_measurement(
                job["id"],
                step_id="input-data",
                cpu_seconds=0.5,
                max_rss_bytes=4096,
                disk_bytes=42,
                wall_seconds=0.7,
            )
            self.assertEqual(4096.0, measurement["max_rss_bytes"])
            with self.assertRaises(SimulationStoreError):
                store.add_artifact(
                    job["id"],
                    step_id=None,
                    kind="escape",
                    relative_path="../../outside",
                )

            reopened = self.make_store(root)
            persisted = reopened.get_job(job["id"])
            self.assertEqual(1, len(persisted["artifacts"]))
            self.assertEqual(1, len(persisted["resource_measurements"]))


if __name__ == "__main__":
    unittest.main()
