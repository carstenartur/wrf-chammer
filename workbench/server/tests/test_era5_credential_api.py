#!/usr/bin/env python3
"""End-to-end API test for explicit secret-safe CDS credential validation."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cds-validation-api-") as temporary:
        root = Path(temporary)
        cache_root = root / "cache"
        fake = root / "validator.py"
        fake.write_text(
            "#!/usr/bin/env python3\n" + textwrap.dedent("""
            import argparse, json
            from pathlib import Path
            p = argparse.ArgumentParser(); p.add_argument("--result", required=True); a = p.parse_args()
            Path(a.result).write_text(json.dumps({
                "version": 1,
                "status": "VALID",
                "code": "credentials_valid",
                "summary": "minimal request succeeded",
                "checked_at": "2026-07-20T12:00:00Z",
                "duration_seconds": 0.1,
                "request": {
                    "dataset": "reanalysis-era5-single-levels",
                    "variable": "2m_temperature",
                    "date": "2013-12-05",
                    "time": "12:00 UTC",
                    "area": [52.0, 7.0, 51.75, 7.25]
                },
                "response": {"size_bytes": 12, "sha256": "a" * 64, "retained": False},
                "artificial_weather_data": False
            }))
            """),
            encoding="utf-8",
        )
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["WRF_CHAMMER_ERA5_CACHE_ROOT"] = str(cache_root)
        env["WRF_CHAMMER_CDS_VALIDATOR"] = str(fake)
        env["CDSAPI_KEY"] = "TEST-SECRET-API-KEY"
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

            initial = request(base, "GET", "/api/data/era5/credentials/validation")
            assert initial["configured"] is True
            assert initial["validation"] is None
            assert "TEST-SECRET-API-KEY" not in json.dumps(initial)
            assert str(cache_root) not in json.dumps(initial)

            started = request(
                base,
                "POST",
                "/api/data/era5/credentials/validate",
                {},
                expected=202,
            )
            assert started["validation"]["status"] == "RUNNING"

            for _ in range(100):
                current = request(base, "GET", "/api/data/era5/credentials/validation")
                if current["validation"] and current["validation"]["terminal"]:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("credential validation did not finish")
            validation = current["validation"]
            assert validation["status"] == "VALID"
            assert validation["code"] == "credentials_valid"
            assert validation["result"]["response"]["retained"] is False
            rendered = json.dumps(current)
            assert "TEST-SECRET-API-KEY" not in rendered
            assert str(root) not in rendered
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    print("CDS credential validation API tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
