#!/usr/bin/env python3
"""Integration test for the Workbench server and worker lifecycle CLI."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import uuid
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = REPO_ROOT / "workbench-runs" / f"cli-test-{uuid.uuid4().hex[:10]}"
TEST_ENV = os.environ.copy()
TEST_ENV["WRF_CHAMMER_RUNTIME_DIR"] = str(TEST_ROOT / "runtime")
TEST_ENV["WRF_CHAMMER_JOB_DATABASE"] = str(TEST_ROOT / "jobs.sqlite3")
TEST_ENV["WRF_CHAMMER_PERSISTENT_ROOT"] = str(TEST_ROOT / "persistent")


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, "-m", "workbench.cli", *args],
        cwd=REPO_ROOT,
        env=TEST_ENV,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"Command {args!r} returned {completed.returncode}, expected {expected}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    port = free_port()
    run("stop", "--timeout", "2", expected=0)
    try:
        doctor = run("doctor", "--json", "--skip-images", expected=0)
        doctor_payload = json.loads(doctor.stdout)
        assert doctor_payload["status"] in {"ready", "warning"}
        assert doctor_payload["checks"]
        assert any(
            check["id"] == "workspace" and check["status"] == "ready"
            for check in doctor_payload["checks"]
        )

        started = run(
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--timeout",
            "15",
            "--worker-poll-seconds",
            "0.1",
            "--json",
        )
        started_payload = json.loads(started.stdout)
        assert started_payload["status"] == "running"
        assert started_payload["port"] == port
        assert started_payload["server_pid"] > 0
        assert started_payload["worker_pid"] > 0
        assert started_payload["server_running"] is True
        assert started_payload["worker_running"] is True

        status_payload = json.loads(run("status", "--json").stdout)
        assert status_payload["running"] is True
        assert status_payload["server_running"] is True
        assert status_payload["worker_running"] is True
        assert status_payload["degraded"] is False
        assert status_payload["url"] == f"http://127.0.0.1:{port}/"

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/readiness", timeout=10
        ) as response:
            readiness = json.loads(response.read().decode("utf-8"))
        assert readiness["checks"]
        assert "summary" in readiness

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/jobs", timeout=10
        ) as response:
            jobs = json.loads(response.read().decode("utf-8"))
        assert jobs == {
            "ok": True,
            "execution": "persistent",
            "count": 0,
            "jobs": [],
        }
        assert (TEST_ROOT / "jobs.sqlite3").is_file()

        logs = run("logs", "--component", "all", "--lines", "20")
        assert "== server:" in logs.stdout
        assert "WRF Workbench available" in logs.stdout
        assert "== worker:" in logs.stdout

        stopped_payload = json.loads(run("stop", "--timeout", "10", "--json").stdout)
        assert stopped_payload["status"] == "stopped"
        assert stopped_payload["server_pid"] == started_payload["server_pid"]
        assert stopped_payload["worker_pid"] == started_payload["worker_pid"]

        stopped_status = run("status", "--json", expected=1)
        stopped_payload = json.loads(stopped_status.stdout)
        assert stopped_payload["running"] is False
        assert stopped_payload["server_running"] is False
        assert stopped_payload["worker_running"] is False
    finally:
        run("stop", "--timeout", "2", expected=0)
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    print("Workbench server and persistent worker lifecycle tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
