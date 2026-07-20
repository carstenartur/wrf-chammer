#!/usr/bin/env python3
"""End-to-end API tests for safe global ERA5 cache management."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[3]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(base: str, method: str, path: str, body: dict | None = None, expected: int = 200) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    if status != expected:
        raise AssertionError(f"{method} {path}: expected {expected}, got {status}: {payload}")
    return payload


def write_plan(cache_root: Path, plan_key: str) -> Path:
    directory = cache_root / plan_key
    target = directory / "files" / "surface.grib"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"api-cache-content")
    plan = {
        "ok": True,
        "plan_key": plan_key,
        "period": {"start": "2013-12-05T12:00:00Z", "end": "2013-12-05T18:00:00Z"},
        "domain": {"bounds": {"west": 2, "south": 51, "east": 14, "north": 58}},
        "requests": [{"name": "surface", "target": "files/surface.grib", "request_key": "b" * 64}],
        "cache": {},
        "provenance": {"source": "ERA5", "datasets": ["surface"], "artificial_weather_data": False},
        "download_config": {"requests": {"surface": {"dataset": "surface", "request": {"year": ["2013"]}, "target": "files/surface.grib"}}},
    }
    (directory / "era5-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (directory / "era5-download-config.json").write_text(json.dumps(plan["download_config"]), encoding="utf-8")
    return directory


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="era5-cache-api-") as temporary:
        cache_root = Path(temporary) / "cache"
        plan_key = "7" * 64
        directory = write_plan(cache_root, plan_key)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["WRF_CHAMMER_ERA5_CACHE_ROOT"] = str(cache_root)
        process = subprocess.Popen(
            [sys.executable, "-m", "workbench.server.application", "--port", str(port)],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            for _ in range(100):
                try:
                    if request(base, "GET", "/api/health").get("ok"):
                        break
                except Exception:
                    time.sleep(0.1)
            else:
                raise AssertionError("Workbench application did not become healthy")

            listing = request(base, "GET", "/api/data/era5/cache")
            assert listing["count"] == 1
            entry = listing["entries"][0]
            assert entry["plan_key"] == plan_key
            assert entry["deletion"]["allowed"] is True
            assert str(cache_root) not in json.dumps(listing)

            detail = request(base, "GET", f"/api/data/era5/cache/{plan_key}")
            confirmation = detail["entry"]["deletion"]["confirmation"]
            stale = request(
                base,
                "POST",
                f"/api/data/era5/cache/{plan_key}/delete",
                {"confirm_plan_key": plan_key, "dependent_job_ids": ["stale-job"]},
                expected=409,
            )
            assert stale["error"]["code"] == "cache_dependency_snapshot_changed"

            deleted = request(
                base,
                "POST",
                f"/api/data/era5/cache/{plan_key}/delete",
                {
                    "confirm_plan_key": confirmation["plan_key"],
                    "dependent_job_ids": confirmation["dependent_job_ids"],
                },
            )
            assert deleted["deleted"]["plan_key"] == plan_key
            assert not directory.exists()
            assert request(base, "GET", "/api/data/era5/cache")["count"] == 0
            assert (cache_root / ".audit" / "cache-events.jsonl").is_file()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    print("ERA5 cache management API tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
