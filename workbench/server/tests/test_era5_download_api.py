#!/usr/bin/env python3
"""End-to-end API tests for persistent, offline-safe ERA5 download jobs."""

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
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


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


def start_server(port: int, cache_root: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["WRF_CHAMMER_ERA5_CACHE_ROOT"] = str(cache_root)
    env.pop("CDSAPI_KEY", None)
    env.pop("CDSAPI_URL", None)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "workbench.server.application",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(f"Workbench server exited early\nstdout:\n{stdout}\nstderr:\n{stderr}")
        try:
            if request(base, "GET", "/api/health").get("ok"):
                return process
        except Exception:
            time.sleep(0.1)
    raise AssertionError("Workbench application did not become healthy")


def stop_server(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wizard_request() -> dict:
    return {
        "event": "xaver",
        "mode": "dry-run",
        "job_id": "era5-download-api-preview",
        "planning": {
            "label": "xaver-era5-download-domain",
            "bounds": {"west": 2, "south": 51, "east": 14, "north": 58},
            "period": {
                "start": "2013-12-05T12:00:00Z",
                "end": "2013-12-05T18:00:00Z",
            },
            "quality_profile": "balanced",
        },
    }


def wait_for_terminal(base: str, job_id: str) -> dict:
    for _ in range(200):
        payload = request(base, "GET", f"/api/data/era5/downloads/{job_id}")
        job = payload["download"]
        if job["status"] in TERMINAL:
            return job
        time.sleep(0.05)
    raise AssertionError(f"ERA5 download did not finish: {job_id}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="wrf-era5-download-api-") as temporary:
        cache_root = Path(temporary) / "managed-cache"
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        process = start_server(port, cache_root)
        try:
            preview = request(base, "POST", "/api/wizard/preview", wizard_request())
            assert preview["valid"] is True
            plan = request(
                base,
                "POST",
                "/api/data/era5/plan",
                {"source": "latest-wizard-preview"},
            )
            assert plan["cache"]["status"] == "missing"

            missing_credentials = request(
                base,
                "POST",
                "/api/data/era5/downloads",
                {"source": "latest-wizard-preview"},
                expected=409,
            )
            assert missing_credentials["error"]["code"] == "credentials_required"

            plan_directory = cache_root / plan["plan_key"]
            for entry in plan["requests"]:
                target = plan_directory / entry["target"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(f"cached-real-era5-test:{entry['name']}".encode("utf-8"))

            started = request(
                base,
                "POST",
                "/api/data/era5/downloads",
                {"source": "latest-wizard-preview"},
                expected=202,
            )
            job_id = started["download"]["id"]
            completed = wait_for_terminal(base, job_id)
            assert completed["status"] == "SUCCEEDED", completed
            assert completed["progress"]["completed_requests"] == len(plan["requests"])
            assert completed["progress"]["total_requests"] == len(plan["requests"])
            assert completed["artifacts"]["manifest"].startswith("configured-external-cache/")
            rendered = json.dumps(completed)
            assert str(cache_root) not in rendered
            assert "CDSAPI_KEY" not in rendered

            listing = request(base, "GET", "/api/data/era5/downloads")
            assert listing["count"] == 1
            assert listing["downloads"][0]["id"] == job_id

            events = request(base, "GET", f"/api/data/era5/downloads/{job_id}/events")
            assert events["count"] >= 2
            assert events["events"][-1]["status"] == "SUCCEEDED"

            not_retryable = request(
                base,
                "POST",
                f"/api/data/era5/downloads/{job_id}/retry",
                {},
                expected=409,
            )
            assert not_retryable["error"]["code"] == "download_not_retryable"
        finally:
            stop_server(process)

        restart_port = free_port()
        restart_base = f"http://127.0.0.1:{restart_port}"
        restarted = start_server(restart_port, cache_root)
        try:
            persisted = request(restart_base, "GET", "/api/data/era5/downloads")
            assert persisted["count"] == 1
            assert persisted["downloads"][0]["status"] == "SUCCEEDED"
            assert persisted["downloads"][0]["id"] == job_id
        finally:
            stop_server(restarted)

    print("Persistent ERA5 download API tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
