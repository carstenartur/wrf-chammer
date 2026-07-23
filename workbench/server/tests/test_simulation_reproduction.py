#!/usr/bin/env python3
"""Tests for exact persistent simulation reproduction and lineage."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workbench.pipeline_specification_service import PipelineSpecificationServiceError
from workbench.simulation_store import SimulationStore, SimulationStoreError

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_KEY = "a" * 64
STEP_IDS = (
    "input-data",
    "geogrid",
    "ungrib",
    "metgrid",
    "real",
    "wrf",
    "postprocessing",
    "result-indexing",
)


def specification() -> dict:
    return {
        "specification_key": SPEC_KEY,
        "created_at": "2026-07-23T06:00:00Z",
        "immutable": True,
        "execution_started": False,
        "identity": {
            "job": {"id": "xaver-reproduction", "name": "Xaver reproduction"},
            "era5_input": {
                "plan_key": "b" * 64,
                "files": [
                    {
                        "path": "files/surface.grib",
                        "sha256": "c" * 64,
                        "size_bytes": 123,
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


class SimulationReproductionTests(unittest.TestCase):
    def make_store(self, root: Path) -> SimulationStore:
        return SimulationStore(
            REPO_ROOT,
            FakeSpecificationService(),  # type: ignore[arg-type]
            database_path=root / "workbench.sqlite3",
        )

    def test_exact_reproduction_creates_ready_job_with_bidirectional_lineage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-reproduce-") as temporary:
            root = Path(temporary)
            store = self.make_store(root)
            source = store.create_job(SPEC_KEY)

            reproduced = store.reproduce_job(source["id"])

            self.assertNotEqual(source["id"], reproduced["id"])
            self.assertEqual("READY", reproduced["status"])
            self.assertEqual(SPEC_KEY, reproduced["specification_key"])
            self.assertIsNone(reproduced["retry_of"])
            self.assertEqual(source["id"], reproduced["reproduced_from"])
            self.assertEqual([], reproduced["reproductions"])
            self.assertIsNone(reproduced["queued_at"])
            self.assertIsNone(reproduced["started_at"])
            self.assertTrue(
                all(step["status"] == "PENDING" for step in reproduced["steps"])
            )
            reproduction_events = [
                event for event in reproduced["events"] if event["type"] == "job_reproduced"
            ]
            self.assertEqual(1, len(reproduction_events))
            self.assertEqual(
                source["id"], reproduction_events[0]["details"]["source_job_id"]
            )
            self.assertEqual(
                "exact-immutable-specification",
                reproduction_events[0]["details"]["mode"],
            )

            source_after = store.get_job(source["id"])
            self.assertEqual("READY", source_after["status"])
            self.assertEqual([reproduced["id"]], source_after["reproductions"])
            self.assertIsNone(source_after["reproduced_from"])

            reopened = self.make_store(root)
            persisted = reopened.get_job(reproduced["id"])
            self.assertEqual(source["id"], persisted["reproduced_from"])
            self.assertEqual(
                [reproduced["id"]],
                reopened.get_job(source["id"])["reproductions"],
            )
            listed = {job["id"]: job for job in reopened.list_jobs()}
            self.assertEqual(source["id"], listed[reproduced["id"]]["reproduced_from"])
            self.assertEqual(
                [reproduced["id"]], listed[source["id"]]["reproductions"]
            )

    def test_retry_and_reproduction_keep_distinct_lineage_semantics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-lineage-") as temporary:
            store = self.make_store(Path(temporary))
            source = store.enqueue_job(store.create_job(SPEC_KEY)["id"])
            running = store.claim_next_job("worker-failure")
            self.assertIsNotNone(running)
            store.fail_step(
                source["id"],
                "input-data",
                code="INPUT_DATA_MISSING",
                message="Input disappeared.",
            )

            retried = store.retry_job(source["id"])
            reproduced = store.reproduce_job(source["id"])

            self.assertEqual(source["id"], retried["retry_of"])
            self.assertIsNone(retried["reproduced_from"])
            self.assertIsNone(reproduced["retry_of"])
            self.assertEqual(source["id"], reproduced["reproduced_from"])
            self.assertEqual("READY", retried["status"])
            self.assertEqual("READY", reproduced["status"])

    def test_missing_source_is_classified(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-missing-source-") as temporary:
            store = self.make_store(Path(temporary))
            with self.assertRaises(SimulationStoreError) as context:
                store.reproduce_job("sim-000000000000-000000000000")
            self.assertEqual("job_not_found", context.exception.code)


if __name__ == "__main__":
    unittest.main()
