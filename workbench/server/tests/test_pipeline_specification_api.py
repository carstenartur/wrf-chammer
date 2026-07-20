#!/usr/bin/env python3
"""End-to-end API tests for immutable real pipeline specifications."""

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


def wizard_request() -> dict:
    return {
        "event": "xaver",
        "mode": "dry-run",
        "job_id": "xaver-immutable-api",
        "planning": {
            "label": "xaver-immutable-api-domain",
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
    with tempfile.TemporaryDirectory(prefix="pipeline-spec-api-") as temporary:
        root = Path(temporary)
        cache_root = root / "cache"
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["WRF_CHAMMER_ERA5_CACHE_ROOT"] = str(cache_root)
        env["WRF_CHAMMER_WPS_RUNTIME_REFERENCE"] = "wps:test"
        env["WRF_CHAMMER_WPS_RUNTIME_IDENTITY"] = "sha256:" + "d" * 64
        env["WRF_CHAMMER_WRF_RUNTIME_REFERENCE"] = "wrf:test"
        env["WRF_CHAMMER_WRF_RUNTIME_IDENTITY"] = "sha256:" + "e" * 64
        env["WRF_CHAMMER_POSTPROCESSING_RUNTIME_REFERENCE"] = "postprocess:test"
        env["WRF_CHAMMER_POSTPROCESSING_RUNTIME_IDENTITY"] = "sha256:" + "f" * 64
        env["WRF_CHAMMER_SOURCE_REVISION"] = "1" * 40
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
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=1)
                    raise AssertionError(f"Workbench exited early\n{stdout}\n{stderr}")
                try:
                    if request(base, "GET", "/api/health").get("ok"):
                        break
                except Exception:
                    time.sleep(0.1)
            else:
                raise AssertionError("Workbench application did not become healthy")

            empty_readiness = request(base, "GET", "/api/pipeline/specifications/readiness")
            assert empty_readiness["ready"] is True
            assert empty_readiness["wizard_preview_available"] is False
            assert set(empty_readiness["profiles"]) >= {
                "small-real-data-demo",
                "quick-preview",
                "balanced-regional",
            }

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
                target.write_bytes(f"verified-real-era5:{entry['name']}".encode("utf-8"))
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
                json.dumps({
                    "version": 1,
                    "plan_key": plan_key,
                    "source": "Copernicus Climate Data Store ERA5 reanalysis",
                    "datasets": plan["provenance"]["datasets"],
                    "verified_at": "2026-07-20T12:00:00Z",
                    "download_job_id": "era5-test-provenance",
                    "artificial_weather_data": False,
                }),
                encoding="utf-8",
            )

            readiness = request(base, "GET", "/api/pipeline/specifications/readiness")
            assert readiness["ready"] is True
            assert readiness["wizard_preview_available"] is True
            assert readiness["runtime"]["wrf"]["identity"] == "sha256:" + "e" * 64

            created = request(
                base,
                "POST",
                "/api/pipeline/specifications",
                {"plan_key": plan_key, "profile": "small-real-data-demo"},
                expected=201,
            )["specification"]
            spec_key = created["specification_key"]
            specification_directory = REPO_ROOT / "workbench-runs" / "specifications" / spec_key
            assert len(spec_key) == 64
            assert created["immutable"] is True
            assert created["execution_started"] is False
            assert len(created["identity"]["steps"]) == 8
            assert created["identity"]["era5_input"]["plan_key"] == plan_key
            assert created["identity"]["source"]["repository_revision"] == "1" * 40

            repeated = request(
                base,
                "POST",
                "/api/pipeline/specifications",
                {"plan_key": plan_key, "profile": "small-real-data-demo"},
                expected=201,
            )["specification"]
            assert repeated["specification_key"] == spec_key
            assert repeated["created_at"] == created["created_at"]

            listing = request(base, "GET", "/api/pipeline/specifications")
            assert any(item["specification_key"] == spec_key for item in listing["specifications"])
            fetched = request(base, "GET", f"/api/pipeline/specifications/{spec_key}")
            assert fetched["specification"]["identity"] == created["identity"]

            invalid = request(
                base,
                "POST",
                "/api/pipeline/specifications",
                {"plan_key": plan_key, "profile": "unbounded-expert"},
                expected=422,
            )
            assert invalid["error"]["code"] == "invalid_pipeline_profile"

            rendered = json.dumps({
                "readiness": readiness,
                "created": created,
                "listing": listing,
            })
            assert str(root) not in rendered
            assert "CDSAPI_KEY" not in rendered
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if specification_directory is not None:
                shutil.rmtree(specification_directory, ignore_errors=True)

    print("Immutable pipeline specification API tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
