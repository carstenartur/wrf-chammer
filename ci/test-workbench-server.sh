#!/bin/sh
# ci/test-workbench-server.sh — local Workbench API smoke tests.
#
# Requires only Python 3 stdlib.  Does not require Docker, CDS credentials or
# NCAR/HPC infrastructure.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
TMP=$(mktemp -d)
SERVER_PID=""

cleanup() {
    if [ -n "${SERVER_PID}" ]; then
        kill "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
    rm -rf "${TMP}"
}
trap cleanup EXIT INT TERM

PORT=$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)

SERVER_LOG="${TMP}/server.log"

cd "${REPO_ROOT}"
python3 -m workbench.server.server \
    --host 127.0.0.1 \
    --port "${PORT}" \
    >"${SERVER_LOG}" 2>&1 &
SERVER_PID=$!

python3 - "${PORT}" "${SERVER_LOG}" <<'PY'
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen

port = sys.argv[1]
server_log = Path(sys.argv[2])
url = f"http://127.0.0.1:{port}/api/health"

for _ in range(50):
    try:
        with urlopen(url, timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if payload.get("ok"):
                print("Workbench API server is healthy")
                raise SystemExit(0)
    except Exception:
        time.sleep(0.1)

print("Server did not become healthy", file=sys.stderr)
if server_log.exists():
    print(server_log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
raise SystemExit(1)
PY

python3 - "${PORT}" "${TMP}" "${REPO_ROOT}" <<'PY'
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

port = sys.argv[1]
tmp = Path(sys.argv[2])
repo_root = Path(sys.argv[3]).resolve()
base = f"http://127.0.0.1:{port}"


def request(method, path, body=None, expected_status=200):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    if status != expected_status:
        raise AssertionError(f"{method} {path}: expected HTTP {expected_status}, got {status}: {payload}")
    return payload

# Events are served through the core catalogue module.
events = request("GET", "/api/events?q=xaver")
assert events["ok"] is True
assert events["count"] >= 1
assert any(event["id"] == "xaver" for event in events["events"])

xaver = request("GET", "/api/events/xaver")
assert xaver["ok"] is True
assert xaver["event"]["id"] == "xaver"
assert len(xaver["domain_presets"]) >= 2
assert len(xaver["resolution_presets"]) >= 1

user_requested_run_dir = tmp / "api-dry-run-user-requested"
preview = request(
    "POST",
    "/api/jobs/preview",
    {
        "event": "Xaver",
        "domain": "northern-germany-27km",
        "resolution": "quick-preview",
        "mode": "dry-run",
        "job_id": "api-ci-dry-run",
        "output_directory": str(user_requested_run_dir),
    },
)
assert preview["ok"] is True
assert preview["valid"] is True
assert preview["config"]["id"] == "api-ci-dry-run"
assert preview["config"]["metadata"]["event_id"] == "xaver"
assert preview["config"]["metadata"]["domain_preset"] == "northern-germany-27km"

config = preview["config"]
valid = request("POST", "/api/jobs/validate", {"config": config})
assert valid == {"ok": True, "valid": True, "errors": []}

invalid = request("POST", "/api/jobs/validate", {"config": {"id": "bad"}}, expected_status=422)
assert invalid["ok"] is False
assert invalid["valid"] is False
assert invalid["errors"]

created = request("POST", "/api/jobs", {"config": config, "start": True}, expected_status=201)
assert created["ok"] is True
assert created["job"]["status"]["status"] == "succeeded"
assert created["job"]["job_id"] == "api-ci-dry-run"
assert created["job"]["run_token"]

status = request("GET", "/api/jobs/api-ci-dry-run")
assert status["ok"] is True
assert status["job"]["status"]["status"] == "succeeded"
actual_run_dir = Path(status["job"]["run_dir"]).resolve()
assert actual_run_dir.is_dir()
assert actual_run_dir != user_requested_run_dir.resolve()
assert repo_root / "workbench-runs" / "api-runs" in actual_run_dir.parents
assert not user_requested_run_dir.exists(), "API must not create user-requested output paths"

logs = request("GET", "/api/jobs/api-ci-dry-run/logs")
assert logs["ok"] is True
assert any(log["name"] == "workbench.log" for log in logs["logs"])
assert all("/" not in log["relative_path"] and "\\" not in log["relative_path"] for log in logs["logs"])
assert all("path" not in log for log in logs["logs"])

outputs = request("GET", "/api/jobs/api-ci-dry-run/outputs")
assert outputs["ok"] is True
assert isinstance(outputs["outputs"], list)
assert all("path" not in output for output in outputs["outputs"])

visualization = request("GET", "/api/jobs/api-ci-dry-run/visualization")
assert visualization["ok"] is True
assert visualization["visualization"]["available"] is False

cancel = request("POST", "/api/jobs/api-ci-dry-run/cancel", {}, expected_status=501)
assert cancel["ok"] is False
assert cancel["error"]["code"] == "cancel_not_implemented"

print("Workbench API smoke tests passed")
PY
