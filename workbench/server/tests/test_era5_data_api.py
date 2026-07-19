#!/usr/bin/env python3
"""Integration tests for the local credential-safe ERA5 planning API."""

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


def wait_for_server(base: str, process: subprocess.Popen[str]) -> None:
    for _ in range(100):
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(f"Workbench server exited early\nstdout:\n{stdout}\nstderr:\n{stderr}")
        try:
            payload = request(base, "GET", "/api/health")
            if payload.get("ok"):
                return
        except Exception:
            time.sleep(0.1)
    raise AssertionError("Workbench application did not become healthy")


def wizard_request() -> dict:
    return {
        "event": "xaver",
        "mode": "dry-run",
        "job_id": "era5-api-map-preview",
        "planning": {
            "label": "xaver-era5-api-domain",
            "bounds": {"west": 2, "south": 51, "east": 14, "north": 58},
            "period": {
                "start": "2013-12-05T12:00:00Z",
                "end": "2013-12-06T06:00:00Z",
            },
            "quality_profile": "balanced",
        },
    }


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory(prefix="wrf-era5-api-") as temp:
        cache_root = Path(temp) / "managed-cache"
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
        try:
            wait_for_server(base, process)

            initial_status = request(base, "GET", "/api/data/era5/status")
            assert initial_status["ok"] is True
            assert initial_status["wizard_preview"]["available"] is False
            assert initial_status["cache"]["path"] == "configured-external-cache"
            assert initial_status["cache"]["plan_count"] == 0
            assert isinstance(initial_status["credentials"]["configured"], bool)
            rendered_status = json.dumps(initial_status)
            assert "CDSAPI_KEY" not in rendered_status
            assert str(cache_root) not in rendered_status

            missing_preview = request(
                base,
                "POST",
                "/api/data/era5/plan",
                {"source": "latest-wizard-preview"},
                expected=409,
            )
            assert missing_preview["error"]["code"] == "wizard_preview_required"

            preview = request(base, "POST", "/api/wizard/preview", wizard_request())
            assert preview["valid"] is True
            assert preview["config"]["id"] == "era5-api-map-preview"
            assert preview["config"]["metadata"]["domain_bounds"] == {
                "west": 2.0,
                "south": 51.0,
                "east": 14.0,
                "north": 58.0,
            }

            latest = request(base, "GET", "/api/wizard/latest")
            assert latest["available"] is True
            assert latest["preview"]["config"]["id"] == "era5-api-map-preview"

            status_with_preview = request(base, "GET", "/api/data/era5/status")
            assert status_with_preview["wizard_preview"] == {
                "available": True,
                "job_id": "era5-api-map-preview",
            }

            plan = request(
                base,
                "POST",
                "/api/data/era5/plan",
                {
                    "source": "latest-wizard-preview",
                    "interval_hours": 1,
                    "margin_degrees": 1,
                },
            )
            assert plan["ok"] is True
            assert len(plan["requests"]) == 4
            assert plan["period"]["time_points"] == 19
            assert plan["provenance"]["artificial_weather_data"] is False
            assert plan["cache"]["root"] == "configured-external-cache"
            assert plan["cache"]["plan_directory"].startswith("configured-external-cache/")
            assert str(cache_root) not in json.dumps(plan)
            assert all(not Path(entry["target"]).is_absolute() for entry in plan["requests"])

            prepared = request(
                base,
                "POST",
                "/api/data/era5/prepare",
                {"source": "latest-wizard-preview"},
            )
            assert prepared["ok"] is True
            assert prepared["prepared"]["download_started"] is False
            plan_key = prepared["plan"]["plan_key"]
            plan_path = cache_root / plan_key / "era5-plan.json"
            config_path = cache_root / plan_key / "era5-download-config.json"
            assert plan_path.is_file()
            assert config_path.is_file()
            stored_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            stored_config = json.loads(config_path.read_text(encoding="utf-8"))
            assert stored_plan["plan_key"] == plan_key
            assert len(stored_config["requests"]) == 4

            refreshed = request(base, "GET", "/api/data/era5/status")
            assert refreshed["cache"]["plan_count"] == 1
            assert refreshed["cache"]["size_bytes"] > 0

            invalid_interval = request(
                base,
                "POST",
                "/api/data/era5/plan",
                {"source": "latest-wizard-preview", "interval_hours": 1.5},
                expected=422,
            )
            assert any("integer" in error for error in invalid_interval["errors"])

            direct = request(
                base,
                "POST",
                "/api/data/era5/plan",
                {
                    "bounds": {"west": 2, "south": 51, "east": 14, "north": 58},
                    "period": {
                        "start": "2013-12-05T12:00:00Z",
                        "end": "2013-12-06T06:00:00Z",
                    },
                },
            )
            assert direct["plan_key"] == plan["plan_key"]
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    print("ERA5 data API tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
