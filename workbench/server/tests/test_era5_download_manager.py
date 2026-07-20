#!/usr/bin/env python3
"""Offline tests for persistent ERA5 download orchestration."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from workbench.era5_download_manager import Era5DownloadManager
from workbench.era5_service import Era5DataService

REPO_ROOT = Path(__file__).resolve().parents[3]
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def write_prepared_plan(service: Era5DataService, plan_key: str, *, cached: bool) -> None:
    target = "files/single-levels.grib"
    plan = {
        "ok": True,
        "plan_key": plan_key,
        "requests": [
            {
                "name": "single_levels_20131205",
                "dataset": "reanalysis-era5-single-levels",
                "target": target,
                "request_key": "b" * 64,
                "estimated_size_bytes": 1,
            }
        ],
        "cache": {},
        "download_config": {
            "requests": {
                "single_levels_20131205": {
                    "dataset": "reanalysis-era5-single-levels",
                    "request": {"year": ["2013"], "month": ["12"], "day": ["05"]},
                    "target": target,
                    "ungrib_prefix": "SFC",
                }
            }
        },
    }
    directory = service.plan_directory(plan_key)
    directory.mkdir(parents=True)
    (directory / "era5-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (directory / "era5-download-config.json").write_text(
        json.dumps(plan["download_config"]), encoding="utf-8"
    )
    if cached:
        target_path = directory / target
        target_path.parent.mkdir(parents=True)
        target_path.write_bytes(b"real-cache-test-content")


def wait_for(manager: Era5DownloadManager, job_id: str, statuses: set[str], timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"ERA5 download {job_id} did not reach {statuses}: {manager.get(job_id)}")


def write_fake_downloader(path: Path, *, mode: str) -> None:
    if mode == "slow":
        body = r'''
import argparse, json, time
from pathlib import Path
p=argparse.ArgumentParser()
for name in ("config","output-dir","manifest","progress"): p.add_argument(f"--{name}", required=True)
a=p.parse_args()
Path(a.progress).write_text(json.dumps({"status":"running","total_requests":1,"completed_requests":0,"current_request":"slow","current_attempt":1}))
time.sleep(30)
'''
    else:
        body = r'''
import argparse, hashlib, json, sys
from pathlib import Path
p=argparse.ArgumentParser()
for name in ("config","output-dir","manifest","progress"): p.add_argument(f"--{name}", required=True)
a=p.parse_args()
out=Path(a.output_dir); marker=out/".fake-failed-once"
if not marker.exists():
    marker.write_text("failed")
    Path(a.progress).write_text(json.dumps({"status":"failed","total_requests":1,"completed_requests":0,"current_request":"single_levels_20131205","current_attempt":1}))
    raise SystemExit(1)
config=json.loads(Path(a.config).read_text())
outputs=[]
for name, item in config["requests"].items():
    target=(out/item["target"]).resolve(); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b"retried-real-cache-test-content")
    outputs.append({"name":name,"target":str(target),"size_bytes":target.stat().st_size,"sha256":hashlib.sha256(target.read_bytes()).hexdigest()})
Path(a.manifest).write_text(json.dumps({"outputs":outputs}))
Path(a.progress).write_text(json.dumps({"status":"succeeded","total_requests":1,"completed_requests":1,"current_request":None,"current_attempt":None}))
'''
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")


class Era5DownloadManagerTests(unittest.TestCase):
    def test_cached_download_runs_outside_request_and_writes_verified_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-manager-cached-") as temporary:
            service = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            plan_key = "a" * 64
            write_prepared_plan(service, plan_key, cached=True)
            manager = Era5DownloadManager(
                REPO_ROOT,
                service,
                downloader_path=REPO_ROOT / "ci" / "download-era5.py",
            )
            try:
                job = manager._enqueue(plan_key, retry_of=None)
                completed = wait_for(manager, job["id"], TERMINAL)
                self.assertEqual("SUCCEEDED", completed["status"])
                self.assertEqual(1, completed["progress"]["completed_requests"])
                self.assertFalse(Path(completed["artifacts"]["manifest"]).is_absolute())
                self.assertTrue((service.plan_directory(plan_key) / "checksums.json").is_file())
                self.assertTrue((service.plan_directory(plan_key) / "provenance.json").is_file())
                self.assertGreaterEqual(len(manager.events(job["id"])), 2)
            finally:
                manager.close()

    def test_running_download_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-manager-cancel-") as temporary:
            root = Path(temporary)
            service = Era5DataService(REPO_ROOT, root / "cache")
            plan_key = "c" * 64
            write_prepared_plan(service, plan_key, cached=False)
            fake = root / "slow-downloader.py"
            write_fake_downloader(fake, mode="slow")
            manager = Era5DownloadManager(REPO_ROOT, service, downloader_path=fake)
            try:
                job = manager._enqueue(plan_key, retry_of=None)
                wait_for(manager, job["id"], {"RUNNING"})
                manager.cancel(job["id"])
                cancelled = wait_for(manager, job["id"], {"CANCELLED"})
                self.assertTrue(cancelled["retryable"])
                self.assertFalse(cancelled["cancellable"])
            finally:
                manager.close()

    def test_failed_download_can_retry_and_reuse_prepared_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-manager-retry-") as temporary:
            root = Path(temporary)
            service = Era5DataService(REPO_ROOT, root / "cache")
            plan_key = "d" * 64
            write_prepared_plan(service, plan_key, cached=False)
            fake = root / "fail-once-downloader.py"
            write_fake_downloader(fake, mode="fail-once")
            previous_key = os.environ.get("CDSAPI_KEY")
            os.environ["CDSAPI_KEY"] = "test-only-not-a-real-secret"
            manager = Era5DownloadManager(REPO_ROOT, service, downloader_path=fake)
            try:
                first = manager._enqueue(plan_key, retry_of=None)
                failed = wait_for(manager, first["id"], {"FAILED"})
                self.assertTrue(failed["retryable"])
                retried = manager.retry(first["id"])
                succeeded = wait_for(manager, retried["id"], TERMINAL)
                self.assertEqual("SUCCEEDED", succeeded["status"])
                self.assertEqual(first["id"], succeeded["retry_of"])
                self.assertFalse(service.requires_credentials(plan_key))
            finally:
                manager.close()
                if previous_key is None:
                    os.environ.pop("CDSAPI_KEY", None)
                else:
                    os.environ["CDSAPI_KEY"] = previous_key

    def test_startup_marks_abandoned_active_state_retryable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-manager-recovery-") as temporary:
            service = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            plan_key = "e" * 64
            write_prepared_plan(service, plan_key, cached=True)
            job_id = f"era5-{plan_key[:12]}-{'f' * 10}"
            job_directory = service.plan_directory(plan_key) / "downloads" / job_id
            job_directory.mkdir(parents=True)
            (job_directory / "state.json").write_text(json.dumps({
                "version": 1,
                "id": job_id,
                "plan_key": plan_key,
                "status": "RUNNING",
                "created_at": "2026-07-20T00:00:00Z",
                "started_at": "2026-07-20T00:01:00Z",
                "finished_at": None,
                "pid": 12345,
                "retry_of": None,
                "message": "running",
                "error": None,
                "progress": {},
                "artifacts": {},
            }), encoding="utf-8")
            manager = Era5DownloadManager(REPO_ROOT, service)
            try:
                recovered = manager.get(job_id)
                self.assertEqual("FAILED", recovered["status"])
                self.assertEqual("worker_interrupted", recovered["error"]["code"])
                self.assertTrue(recovered["retryable"])
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
