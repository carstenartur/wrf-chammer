#!/usr/bin/env python3
"""End-to-end API test for the persistent queue and standalone worker."""

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


def request(
    base: str,
    method: str,
    path: str,
    body: dict | None = None,
    *,
    expected: int = 200,
) -> dict:
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
        raise AssertionError(
            f"{method} {path}: expected {expected}, got {status}: {payload}"
        )
    return payload


def wait_for_server(base: str, process: subprocess.Popen[str]) -> None:
    for _ in range(150):
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                "Workbench application exited early\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        try:
            if request(base, "GET", "/api/health").get("ok"):
                return
        except Exception:
            time.sleep(0.1)
    raise AssertionError("Workbench application did not become healthy")


def job_config(job_id: str) -> dict:
    return {
        "id": job_id,
        "mode": "dry-run",
        "name": f"Persistent API test {job_id}",
        "period": {
            "start": "2013-12-05T12:00:00Z",
            "end": "2013-12-05T13:00:00Z",
        },
        "domain": {
            "label": "persistent-api-test",
            "center_lat": 54.0,
            "center_lon": 9.0,
            "dx_km": 27,
            "dy_km": 27,
            "e_we": 10,
            "e_sn": 10,
        },
        "inputs": {"source": "none"},
        "outputs": {"directory": "client-value-is-replaced"},
    }


def run_worker(env: dict[str, str], database: Path, worker_id: str) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "workbench.job_worker",
            "--repo-root",
            str(REPO_ROOT),
            "--database",
            str(database),
            "--worker-id",
            worker_id,
            "--once",
            "--poll-seconds",
            "0.05",
            "--cancel-grace-seconds",
            "0.2",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Worker returned {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def main() -> int:
    token = uuid.uuid4().hex[:10]
    managed_root = REPO_ROOT / "workbench-runs" / f"persistent-api-test-{token}"
    database = managed_root / "jobs.sqlite3"
    runs_root = managed_root / "runs"
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["WRF_CHAMMER_JOB_DATABASE"] = str(database)
    env["WRF_CHAMMER_PERSISTENT_ROOT"] = str(runs_root)

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "workbench.server.application",
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
        wait_for_server(base, process)

        first_id = f"persistent-api-{token}"
        created = request(
            base,
            "POST",
            "/api/jobs",
            {
                "execution": "queued",
                "start": True,
                "priority": 7,
                "config": job_config(first_id),
            },
            expected=202,
        )
        assert created["execution"] == "persistent"
        assert created["job"]["state"] == "QUEUED"
        assert created["job"]["priority"] == 7
        assert created["job"]["run_root"].startswith("workbench-runs/")
        assert str(managed_root) not in json.dumps(created)

        duplicate = request(
            base,
            "POST",
            "/api/jobs",
            {"execution": "queued", "config": job_config(first_id)},
            expected=409,
        )
        assert duplicate["error"]["code"] == "job_conflict"

        listing = request(base, "GET", "/api/jobs")
        assert listing["execution"] == "persistent"
        assert listing["count"] == 1
        assert listing["jobs"][0]["job_id"] == first_id

        run_worker(env, database, f"api-worker-{token}-1")

        finished = request(base, "GET", f"/api/jobs/{first_id}")
        assert finished["job"]["state"] == "SUCCEEDED"
        assert finished["job"]["attempt"] == 1
        assert finished["job"]["steps"][0]["state"] == "SUCCEEDED"

        events = request(base, "GET", f"/api/jobs/{first_id}/events")
        event_types = [event["event_type"] for event in events["events"]]
        assert event_types == ["job-created", "job-claimed", "job-finished"]
        after_first = request(
            base,
            "GET",
            f"/api/jobs/{first_id}/events?after_id={events['events'][0]['id']}",
        )
        assert len(after_first["events"]) == 2

        artifacts = request(base, "GET", f"/api/jobs/{first_id}/artifacts")
        assert artifacts["artifacts"]
        assert any(
            artifact["relative_path"] == "logs/worker-run.log"
            for artifact in artifacts["artifacts"]
        )
        assert all(artifact["sha256"] for artifact in artifacts["artifacts"])

        second_id = f"persistent-cancel-{token}"
        request(
            base,
            "POST",
            "/api/jobs",
            {"execution": "queued", "config": job_config(second_id)},
            expected=202,
        )
        cancelled = request(
            base,
            "POST",
            f"/api/jobs/{second_id}/cancel",
            {},
            expected=202,
        )
        assert cancelled["job"]["state"] == "CANCELLED"

        retried = request(
            base,
            "POST",
            f"/api/jobs/{second_id}/retry",
            {},
            expected=202,
        )
        assert retried["job"]["state"] == "QUEUED"
        assert retried["job"]["attempt"] == 2

        run_worker(env, database, f"api-worker-{token}-2")
        retried_finished = request(base, "GET", f"/api/jobs/{second_id}")
        assert retried_finished["job"]["state"] == "SUCCEEDED"
        assert retried_finished["job"]["attempt"] == 2
        assert len(retried_finished["job"]["steps"]) == 2

        invalid_execution = request(
            base,
            "POST",
            "/api/jobs",
            {"execution": "distributed", "config": job_config(f"invalid-{token}")},
            expected=400,
        )
        assert invalid_execution["error"]["code"] == "invalid_execution"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        shutil.rmtree(managed_root, ignore_errors=True)

    print("Persistent job API and worker tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
