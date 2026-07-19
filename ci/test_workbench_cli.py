#!/usr/bin/env python3
"""Integration test for the Workbench lifecycle CLI."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, "-m", "workbench.cli", *args],
        cwd=REPO_ROOT,
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
            "--json",
        )
        started_payload = json.loads(started.stdout)
        assert started_payload["status"] == "running"
        assert started_payload["port"] == port
        assert started_payload["pid"] > 0

        status_payload = json.loads(run("status", "--json").stdout)
        assert status_payload["running"] is True
        assert status_payload["url"] == f"http://127.0.0.1:{port}/"

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/readiness", timeout=10
        ) as response:
            readiness = json.loads(response.read().decode("utf-8"))
        assert readiness["checks"]
        assert "summary" in readiness

        logs = run("logs", "--lines", "20")
        assert "WRF Workbench available" in logs.stdout

        stopped_payload = json.loads(run("stop", "--timeout", "10", "--json").stdout)
        assert stopped_payload["status"] == "stopped"

        stopped_status = run("status", "--json", expected=1)
        assert json.loads(stopped_status.stdout)["running"] is False
    finally:
        run("stop", "--timeout", "2", expected=0)

    print("Workbench lifecycle CLI tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
