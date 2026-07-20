#!/usr/bin/env python3
"""End-to-end API test for persistent simulation job records without a worker."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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


def request(
    base: str,
    method: str,
    path: str,
    body: dict | None = None,
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


def start_server(port: int, env: dict[str, str]) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [sys.executable, "-m", "workbench.server.application", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(f"Workbench exited early\n{stdout}\n{stderr}")
        try:
            if request(base, "GET", "/api/health").get("ok"):
                return process
        except Exception:
            time.sleep(0.1)
    process.terminate()
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
        "job_id": "xaver-persistent-simulation-api",
        "planning": {
            "label": "xaver-persistent-simulation-domain",
            "bounds": {"west": 7.0, "south": 51.5, "east": 10.5, "north": 55.5},
            "period": {
                "start": "2013-12-05T12:00:00Z",
                "end": "2013-12-05T18:00:00Z",
            },
            "quality_profile": "balanced",
        },
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    specification_directory: Path | None = None
    with tempfile.TemporaryDirectory(prefix="simulation-job-api-") as temporary:
        root = Path(temporary)
        cache_root = root / "cache"
        database = root / "state" / "simulations.sqlite3"
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["WRF_CHAMMER_ERA5_CACHE_ROOT"] = str(cache_root)
        env["WRF_CHAMMER_SIMULATION_DATABASE"] = str(database)
        env["WRF_CHAMMER_WPS_RUNTIME_REFERENCE"] = "wps:test"
        env["WRF_CHAMMER_WPS_RUNTIME_IDENTITY"] = "sha256:" + "d" * 64
        env["WRF_CHAMMER_WRF_RUNTIME_REFERENCE"] = "wrf:test"
        env["WRF_CHAMMER_WRF_RUNTIME_IDENTITY"] = "sha256:" + "e" * 64
        env["WRF_CHAMMER_POSTPROCESSING_RUNTIME_REFERENCE"] = "postprocess:test"
        env["WRF_CHAMMER_POSTPROCESSING_RUNTIME_IDENTITY"] = "sha256:" + "f" * 64
        env["WRF_CHAMMER_SOURCE_REVISION"] = "1" * 40

        process = start_server(port, env)
        try:
            preview = request(base, "POST", "/api/wizard/preview", wizard_request())
            assert preview["valid"] is True
            prepared = request(
                base,
                "POST",
                "/api/data/era5/prepare",
                {"source": "latest-wizard-preview"},
            )
            plan = prepared["plan"]
            plan_key = plan["plan_key"]
            plan_directory = cache_root / plan_key
            checksum_files = {}
            for entry in plan["requests"]:
                target = plan_directory / entry["target"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(
                    f"verified-real-era5:{entry['name']}".encode("utf-8")
                )
                checksum_files[entry["target"]] = {
                    "sha256": sha256_file(target),
                    "size_bytes": target.stat().st_size,
                    "request_name": entry["name"],
                }
            (plan_directory / "checksums.json").write_text(
                json.dumps({"version": 1, "plan_key": plan_key, "files": checksum_files}),
                encoding="utf-8",
            )
            (plan_directory / "provenance.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "plan_key": plan_key,
                        "source": "Copernicus Climate Data Store ERA5 reanalysis",
                        "datasets": plan["provenance"]["datasets"],
                        "verified_at": "2026-07-20T12:00:00Z",
                        "download_job_id": "era5-test-provenance",
                        "artificial_weather_data": False,
                    }
                ),
                encoding="utf-8",
            )

            specification = request(
                base,
                "POST",
                "/api/pipeline/specifications",
                {"plan_key": plan_key, "profile": "small-real-data-demo"},
                expected=201,
            )["specification"]
            spec_key = specification["specification_key"]
            specification_directory = (
                REPO_ROOT / "workbench-runs" / "specifications" / spec_key
            )

            created = request(
                base,
                "POST",
                "/api/simulations",
                {"specification_key": spec_key},
                expected=201,
            )["simulation"]
            job_id = created["id"]
            assert created["status"] == "READY"
            assert created["started_at"] is None
            assert created["worker_id"] is None
            assert len(created["steps"]) == 8
            assert {step["status"] for step in created["steps"]} == {"PENDING"}
            assert len(created["input_datasets"]) == 1
            assert len(created["runtime_snapshots"]) == 3
            assert created["events"][0]["type"] == "job_created"

            listing = request(base, "GET", "/api/simulations")
            assert any(job["id"] == job_id for job in listing["simulations"])
            detail = request(base, "GET", f"/api/simulations/{job_id}")["simulation"]
            assert detail["specification_key"] == spec_key
            events = request(base, "GET", f"/api/simulations/{job_id}/events")
            assert events["count"] == 1
            artifacts = request(base, "GET", f"/api/simulations/{job_id}/artifacts")
            assert artifacts["artifacts"] == []

            queued = request(
                base,
                "POST",
                f"/api/simulations/{job_id}/enqueue",
                expected=202,
            )["simulation"]
            assert queued["status"] == "QUEUED"
            assert queued["queued_at"] is not None
            assert queued["started_at"] is None
            assert queued["worker_id"] is None
        finally:
            stop_server(process)

        # Reopen the application with the same database. Queue state must persist.
        process = start_server(port, env)
        try:
            persisted = request(base, "GET", f"/api/simulations/{job_id}")["simulation"]
            assert persisted["status"] == "QUEUED"
            assert persisted["started_at"] is None
            assert persisted["worker_id"] is None

            cancelled = request(
                base,
                "POST",
                f"/api/simulations/{job_id}/cancel",
                expected=202,
            )["simulation"]
            assert cancelled["status"] == "CANCELLED"
            assert cancelled["retryable"] is True
            assert {step["status"] for step in cancelled["steps"]} == {"CANCELLED"}

            retry = request(
                base,
                "POST",
                f"/api/simulations/{job_id}/retry",
                expected=201,
            )["simulation"]
            assert retry["id"] != job_id
            assert retry["retry_of"] == job_id
            assert retry["specification_key"] == spec_key
            assert retry["status"] == "READY"
            assert retry["started_at"] is None

            bad = request(
                base,
                "POST",
                "/api/simulations",
                {"specification_key": "not-a-specification"},
                expected=404,
            )
            assert bad["error"]["code"] == "specification_not_found"

            rendered = json.dumps(
                {
                    "created": created,
                    "queued": queued,
                    "persisted": persisted,
                    "cancelled": cancelled,
                    "retry": retry,
                }
            )
            assert str(root) not in rendered
            assert "CDSAPI_KEY" not in rendered
        finally:
            stop_server(process)
            if specification_directory is not None:
                shutil.rmtree(specification_directory, ignore_errors=True)

    print("Persistent simulation job API tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
