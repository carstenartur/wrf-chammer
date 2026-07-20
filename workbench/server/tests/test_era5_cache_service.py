#!/usr/bin/env python3
"""Offline tests for safe global ERA5 cache management."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from workbench.era5_cache_service import (
    CacheCoordinatedEra5DownloadManager,
    Era5CacheService,
    Era5CacheServiceError,
)
from workbench.era5_service import Era5DataService, Era5DataServiceError

REPO_ROOT = Path(__file__).resolve().parents[3]


def write_plan(service: Era5DataService, plan_key: str) -> Path:
    directory = service.plan_directory(plan_key)
    directory.mkdir(parents=True)
    target = directory / "files" / "surface.grib"
    target.parent.mkdir()
    target.write_bytes(b"verified-era5-cache")
    plan = {
        "ok": True,
        "plan_key": plan_key,
        "period": {
            "start": "2013-12-05T12:00:00Z",
            "end": "2013-12-05T18:00:00Z",
            "time_points": 7,
        },
        "domain": {
            "bounds": {"west": 2, "south": 51, "east": 14, "north": 58},
            "margin_degrees": 1,
        },
        "requests": [{
            "name": "single_levels_20131205",
            "target": "files/surface.grib",
            "request_key": "b" * 64,
        }],
        "cache": {},
        "provenance": {
            "source": "Copernicus Climate Data Store ERA5 reanalysis",
            "datasets": ["reanalysis-era5-single-levels"],
            "artificial_weather_data": False,
        },
        "download_config": {
            "requests": {
                "single_levels_20131205": {
                    "dataset": "reanalysis-era5-single-levels",
                    "request": {"year": ["2013"]},
                    "target": "files/surface.grib",
                }
            }
        },
    }
    (directory / "era5-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (directory / "era5-download-config.json").write_text(
        json.dumps(plan["download_config"]), encoding="utf-8"
    )
    return directory


def write_job(directory: Path, plan_key: str, job_id: str, status: str) -> None:
    job_directory = directory / "downloads" / job_id
    job_directory.mkdir(parents=True)
    (job_directory / "state.json").write_text(json.dumps({
        "version": 1,
        "id": job_id,
        "plan_key": plan_key,
        "status": status,
        "created_at": "2026-07-20T12:00:00Z",
        "started_at": "2026-07-20T12:01:00Z" if status != "QUEUED" else None,
        "finished_at": "2026-07-20T12:02:00Z" if status in {"SUCCEEDED", "FAILED", "CANCELLED"} else None,
        "retry_of": None,
        "message": status,
        "error": None,
        "progress": {},
        "artifacts": {},
    }), encoding="utf-8")


class Era5CacheServiceTests(unittest.TestCase):
    def test_lists_safe_metadata_and_dependencies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-cache-list-") as temporary:
            service = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = CacheCoordinatedEra5DownloadManager(REPO_ROOT, service)
            try:
                plan_key = "a" * 64
                directory = write_plan(service, plan_key)
                job_id = f"era5-{plan_key[:12]}-{'c' * 10}"
                write_job(directory, plan_key, job_id, "SUCCEEDED")
                cache = Era5CacheService(service, manager)
                entries = cache.list_entries()
                self.assertEqual(1, len(entries))
                entry = entries[0]
                self.assertEqual(plan_key, entry["plan_key"])
                self.assertEqual("complete", entry["status"])
                self.assertEqual(100.0, entry["coverage"]["percent"])
                self.assertGreater(entry["storage"]["size_bytes"], 0)
                self.assertEqual([job_id], entry["deletion"]["confirmation"]["dependent_job_ids"])
                self.assertTrue(entry["deletion"]["allowed"])
                rendered = json.dumps(entry)
                self.assertNotIn(str(Path(temporary)), rendered)
                self.assertFalse(entry["provenance"]["artificial_weather_data"])
            finally:
                manager.close()

    def test_active_download_blocks_deletion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-cache-active-") as temporary:
            service = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = CacheCoordinatedEra5DownloadManager(REPO_ROOT, service)
            try:
                plan_key = "d" * 64
                directory = write_plan(service, plan_key)
                job_id = f"era5-{plan_key[:12]}-{'e' * 10}"
                write_job(directory, plan_key, job_id, "RUNNING")
                cache = Era5CacheService(service, manager)
                entry = cache.detail(plan_key)
                self.assertFalse(entry["deletion"]["allowed"])
                with self.assertRaises(Era5CacheServiceError) as context:
                    cache.delete(plan_key, {
                        "confirm_plan_key": plan_key,
                        "dependent_job_ids": [job_id],
                    })
                self.assertEqual("cache_entry_in_use", context.exception.code)
                self.assertTrue(directory.is_dir())
            finally:
                manager.close()

    def test_delete_requires_current_dependency_snapshot_and_writes_audit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-cache-delete-") as temporary:
            service = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = CacheCoordinatedEra5DownloadManager(REPO_ROOT, service)
            try:
                plan_key = "f" * 64
                directory = write_plan(service, plan_key)
                job_id = f"era5-{plan_key[:12]}-{'1' * 10}"
                write_job(directory, plan_key, job_id, "FAILED")
                cache = Era5CacheService(service, manager)
                with self.assertRaises(Era5CacheServiceError) as context:
                    cache.delete(plan_key, {
                        "confirm_plan_key": plan_key,
                        "dependent_job_ids": [],
                    })
                self.assertEqual("cache_dependency_snapshot_changed", context.exception.code)

                result = cache.delete(plan_key, {
                    "confirm_plan_key": plan_key,
                    "dependent_job_ids": [job_id],
                })
                self.assertTrue(result["ok"])
                self.assertFalse(directory.exists())
                audit = service.cache_root / ".audit" / "cache-events.jsonl"
                self.assertTrue(audit.is_file())
                event = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
                self.assertEqual(plan_key, event["plan_key"])
                self.assertEqual([job_id], event["dependent_job_ids"])
            finally:
                manager.close()

    def test_waiting_enqueue_revalidates_after_cache_deletion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-cache-race-") as temporary:
            service = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = CacheCoordinatedEra5DownloadManager(REPO_ROOT, service)
            plan_key = "3" * 64
            directory = write_plan(service, plan_key)
            cache = Era5CacheService(service, manager)
            errors: list[BaseException] = []

            def enqueue() -> None:
                try:
                    manager._enqueue(plan_key, retry_of=None)
                except BaseException as exc:  # captured for the test thread
                    errors.append(exc)

            try:
                with manager.cache_operation_lock:
                    thread = threading.Thread(target=enqueue)
                    thread.start()
                    self.assertTrue(thread.is_alive())
                    result = cache.delete(plan_key, {
                        "confirm_plan_key": plan_key,
                        "dependent_job_ids": [],
                    })
                    self.assertTrue(result["ok"])
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(1, len(errors))
                self.assertIsInstance(errors[0], Era5DataServiceError)
                self.assertFalse(directory.exists())
                self.assertFalse((service.cache_root / plan_key).exists())
            finally:
                manager.close()

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinked_plan_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-cache-symlink-") as temporary:
            root = Path(temporary)
            service = Era5DataService(REPO_ROOT, root / "cache")
            service.cache_root.mkdir(parents=True)
            external = root / "external"
            external.mkdir()
            plan_key = "2" * 64
            os.symlink(external, service.cache_root / plan_key)
            manager = CacheCoordinatedEra5DownloadManager(REPO_ROOT, service)
            try:
                cache = Era5CacheService(service, manager)
                with self.assertRaises(Era5CacheServiceError) as context:
                    cache.detail(plan_key)
                self.assertEqual("cache_entry_invalid", context.exception.code)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
