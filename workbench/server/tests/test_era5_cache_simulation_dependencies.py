#!/usr/bin/env python3
"""Tests for persistent simulation dependencies of ERA5 cache entries."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from workbench.era5_cache_service import (
    CacheCoordinatedEra5DownloadManager,
    Era5CacheService,
    Era5CacheServiceError,
)
from workbench.era5_service import Era5DataService
from workbench.server.tests.test_era5_cache_service import write_plan
from workbench.server.tests.test_simulation_store import (
    FakeSpecificationService,
    SPEC_KEY,
    specification,
)
from workbench.simulation_store import SimulationStore, SimulationStoreError

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAN_KEY = "b" * 64


class DependencyStore:
    def __init__(self, dependencies: list[dict]):
        self.dependencies = dependencies

    def dependencies_for_plan(self, plan_key: str) -> list[dict]:
        if plan_key != PLAN_KEY:
            raise AssertionError(plan_key)
        return copy.deepcopy(self.dependencies)


class ChangingDependencyStore:
    def __init__(self, snapshots: list[list[dict]]):
        self.snapshots = snapshots
        self.calls = 0

    def dependencies_for_plan(self, plan_key: str) -> list[dict]:
        if plan_key != PLAN_KEY:
            raise AssertionError(plan_key)
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return copy.deepcopy(self.snapshots[index])


def simulation(job_id: str, status: str, *, blocking: bool) -> dict:
    return {
        "id": job_id,
        "specification_key": SPEC_KEY,
        "retry_of": None,
        "status": status,
        "created_at": "2026-07-22T05:00:00Z",
        "queued_at": None,
        "started_at": None,
        "finished_at": "2026-07-22T05:10:00Z" if not blocking else None,
        "current_step_id": "input-data" if status == "PREPROCESSING" else None,
        "blocking": blocking,
        "retryable": status in {"FAILED", "CANCELLED"},
    }


class StrictSpecificationService(FakeSpecificationService):
    def __init__(self, data_service: Era5DataService, value: dict):
        self.data_service = data_service
        self.value = value

    def get(self, key: str) -> dict:
        if key != SPEC_KEY:
            raise AssertionError(key)
        return copy.deepcopy(self.value)


class Era5CacheSimulationDependencyTests(unittest.TestCase):
    def test_store_returns_path_free_plan_dependencies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-plan-dependency-") as temporary:
            root = Path(temporary)
            store = SimulationStore(
                REPO_ROOT,
                FakeSpecificationService(),  # type: ignore[arg-type]
                database_path=root / "state.sqlite3",
            )
            ready = store.create_job(SPEC_KEY)
            dependencies = store.dependencies_for_plan(PLAN_KEY)
            self.assertEqual([ready["id"]], [value["id"] for value in dependencies])
            self.assertTrue(dependencies[0]["blocking"])
            self.assertNotIn(str(root), json.dumps(dependencies))

            cancelled = store.request_cancel(ready["id"])
            self.assertEqual("CANCELLED", cancelled["status"])
            dependencies = store.dependencies_for_plan(PLAN_KEY)
            self.assertFalse(dependencies[0]["blocking"])
            self.assertTrue(dependencies[0]["retryable"])

    def test_active_simulation_blocks_cache_deletion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cache-active-simulation-") as temporary:
            data = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = CacheCoordinatedEra5DownloadManager(REPO_ROOT, data)
            directory = write_plan(data, PLAN_KEY)
            dependency = simulation("sim-bbbbbbbbbbbb-111111111111", "READY", blocking=True)
            try:
                cache = Era5CacheService(data, manager, DependencyStore([dependency]))
                entry = cache.detail(PLAN_KEY)
                self.assertFalse(entry["deletion"]["allowed"])
                self.assertEqual(1, entry["dependencies"]["simulation_job_count"])
                self.assertEqual(1, entry["dependencies"]["blocking_simulation_job_count"])
                self.assertEqual(
                    [dependency["id"]],
                    entry["deletion"]["confirmation"]["dependent_simulation_ids"],
                )
                with self.assertRaises(Era5CacheServiceError) as context:
                    cache.delete(
                        PLAN_KEY,
                        {
                            "confirm_plan_key": PLAN_KEY,
                            "dependent_job_ids": [],
                            "dependent_simulation_ids": [dependency["id"]],
                        },
                    )
                self.assertEqual("cache_entry_in_use", context.exception.code)
                self.assertTrue(directory.is_dir())
            finally:
                manager.close()

    def test_terminal_simulation_requires_confirmation_and_is_audited(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cache-terminal-simulation-") as temporary:
            data = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = CacheCoordinatedEra5DownloadManager(REPO_ROOT, data)
            directory = write_plan(data, PLAN_KEY)
            dependency = simulation(
                "sim-bbbbbbbbbbbb-222222222222", "SUCCEEDED", blocking=False
            )
            try:
                cache = Era5CacheService(data, manager, DependencyStore([dependency]))
                entry = cache.detail(PLAN_KEY)
                self.assertTrue(entry["deletion"]["allowed"])
                with self.assertRaises(Era5CacheServiceError) as context:
                    cache.delete(
                        PLAN_KEY,
                        {
                            "confirm_plan_key": PLAN_KEY,
                            "dependent_job_ids": [],
                            "dependent_simulation_ids": [],
                        },
                    )
                self.assertEqual("cache_dependency_snapshot_changed", context.exception.code)

                result = cache.delete(
                    PLAN_KEY,
                    {
                        "confirm_plan_key": PLAN_KEY,
                        "dependent_job_ids": [],
                        "dependent_simulation_ids": [dependency["id"]],
                    },
                )
                self.assertTrue(result["ok"])
                self.assertFalse(directory.exists())
                self.assertEqual(
                    [dependency["id"]],
                    result["deleted"]["dependent_simulation_ids"],
                )
                audit = data.cache_root / ".audit" / "cache-events.jsonl"
                event = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
                self.assertEqual([dependency["id"]], event["dependent_simulation_ids"])
            finally:
                manager.close()

    def test_changed_simulation_snapshot_aborts_before_deletion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cache-simulation-race-") as temporary:
            data = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = CacheCoordinatedEra5DownloadManager(REPO_ROOT, data)
            directory = write_plan(data, PLAN_KEY)
            first = simulation("sim-bbbbbbbbbbbb-333333333333", "SUCCEEDED", blocking=False)
            second = simulation("sim-bbbbbbbbbbbb-444444444444", "SUCCEEDED", blocking=False)
            store = ChangingDependencyStore([[first], [first, second]])
            try:
                cache = Era5CacheService(data, manager, store)
                with self.assertRaises(Era5CacheServiceError) as context:
                    cache.delete(
                        PLAN_KEY,
                        {
                            "confirm_plan_key": PLAN_KEY,
                            "dependent_job_ids": [],
                            "dependent_simulation_ids": [first["id"]],
                        },
                    )
                self.assertEqual("cache_dependency_snapshot_changed", context.exception.code)
                self.assertTrue(directory.is_dir())
            finally:
                manager.close()

    def test_new_simulation_is_rejected_after_verified_cache_disappears(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-cache-disappeared-") as temporary:
            root = Path(temporary)
            data = Era5DataService(REPO_ROOT, root / "cache")
            directory = write_plan(data, PLAN_KEY)
            input_file = directory / "files" / "surface.grib"
            (directory / "checksums.json").write_text("{}", encoding="utf-8")
            (directory / "provenance.json").write_text("{}", encoding="utf-8")
            value = specification()
            value["identity"]["era5_input"]["files"] = [
                {
                    "path": "files/surface.grib",
                    "sha256": "c" * 64,
                    "size_bytes": input_file.stat().st_size,
                    "request_name": "surface",
                }
            ]
            store = SimulationStore(
                REPO_ROOT,
                StrictSpecificationService(data, value),  # type: ignore[arg-type]
                database_path=root / "state.sqlite3",
            )
            created = store.create_job(SPEC_KEY)
            self.assertEqual("READY", created["status"])
            for child in sorted(directory.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            directory.rmdir()
            with self.assertRaises(SimulationStoreError) as context:
                store.create_job(SPEC_KEY)
            self.assertEqual("input_dataset_unavailable", context.exception.code)


if __name__ == "__main__":
    unittest.main()
