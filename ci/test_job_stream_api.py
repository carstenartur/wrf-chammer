#!/usr/bin/env python3
"""HTTP integration tests for job SSE reconnect and resource telemetry."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def json_request(base: str, method: str, path: str, body=None, expected=200):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    if status != expected:
        raise AssertionError(f"{method} {path}: expected {expected}, got {status}: {payload}")
    return payload


def stream_request(base: str, path: str, last_event_id: int | None = None) -> str:
    headers = {"Accept": "text/event-stream"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    request = Request(base + path, headers=headers, method="GET")
    with urlopen(request, timeout=10) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == "text/event-stream"
        return response.read().decode("utf-8")


def wait_for_server(base: str, process: subprocess.Popen[str]) -> None:
    for _ in range(100):
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(f"server exited\nstdout:\n{stdout}\nstderr:\n{stderr}")
        try:
            if json_request(base, "GET", "/api/health").get("ok"):
                return
        except Exception:
            time.sleep(0.1)
    raise AssertionError("streaming application did not become healthy")


def config(job_id: str) -> dict:
    return {
        "id": job_id,
        "mode": "dry-run",
        "name": job_id,
        "period": {
            "start": "2013-12-05T12:00:00Z",
            "end": "2013-12-05T13:00:00Z",
        },
        "domain": {
            "label": "stream-test",
            "center_lat": 54,
            "center_lon": 9,
            "dx_km": 27,
            "dy_km": 27,
            "e_we": 10,
            "e_sn": 10,
        },
        "inputs": {"source": "none"},
        "outputs": {"directory": "managed"},
        "metadata": {
            "resource_estimate": {
                "estimated_ram_gb": {"minimum": 0.1, "recommended": 0.2},
                "estimated_storage_gb": {"working_total": 0.1},
            }
        },
    }


def main() -> int:
    token = uuid.uuid4().hex[:10]
    test_root = REPO_ROOT / "workbench-runs" / f"stream-api-test-{token}"
    database = test_root / "jobs.sqlite3"
    runs = test_root / "runs"
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["WRF_CHAMMER_JOB_DATABASE"] = str(database)
    env["WRF_CHAMMER_PERSISTENT_ROOT"] = str(runs)

    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "workbench.server.streaming_application",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--repo-root",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for_server(base, server)

        queued_id = f"stream-queued-{token}"
        json_request(
            base,
            "POST",
            "/api/jobs",
            {"execution": "queued", "config": config(queued_id)},
            expected=202,
        )
        initial = stream_request(base, f"/api/jobs/{queued_id}/events/stream?timeout=1")
        assert "id: 1" in initial
        assert '"event_type":"job-created"' in initial
        assert "heartbeat" in initial
        assert "event: job-complete" not in initial

        json_request(base, "POST", f"/api/jobs/{queued_id}/cancel", {}, expected=202)
        replay = stream_request(
            base,
            f"/api/jobs/{queued_id}/events/stream?timeout=2",
            last_event_id=1,
        )
        assert "id: 1" not in replay
        assert "id: 2" in replay
        assert '"event_type":"cancel-requested"' in replay
        assert "event: job-complete" in replay
        assert '"state":"CANCELLED"' in replay

        resource_id = f"stream-resource-{token}"
        json_request(
            base,
            "POST",
            "/api/jobs",
            {"execution": "queued", "config": config(resource_id)},
            expected=202,
        )
        worker = subprocess.run(
            [
                sys.executable,
                "-m",
                "workbench.job_worker",
                "--repo-root",
                str(REPO_ROOT),
                "--database",
                str(database),
                "--worker-id",
                f"stream-worker-{token}",
                "--once",
                "--poll-seconds",
                "0.05",
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if worker.returncode != 0:
            raise AssertionError(f"worker failed\n{worker.stdout}\n{worker.stderr}")

        resources = json_request(base, "GET", f"/api/jobs/{resource_id}/resources")
        assert resources["ok"] is True
        assert [snapshot["phase"] for snapshot in resources["snapshots"]] == [
            "preflight",
            "finished",
        ]
        metrics = {measurement["metric"] for measurement in resources["measurements"]}
        assert "wall_clock_seconds" in metrics
        assert "exit_code" in metrics

        finished_stream = stream_request(
            base,
            f"/api/jobs/{resource_id}/events/stream?timeout=2",
        )
        assert "event: job-complete" in finished_stream
        assert '"state":"SUCCEEDED"' in finished_stream
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        shutil.rmtree(test_root, ignore_errors=True)

    print("Job stream and resource API tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
