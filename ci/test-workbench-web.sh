#!/bin/sh
# ci/test-workbench-web.sh — local Workbench web UI smoke tests.

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

python3 - "${PORT}" <<'PY'
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

port = sys.argv[1]
base = f"http://127.0.0.1:{port}"


def get_text(path):
    with urlopen(base + path, timeout=10) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8")


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

status, content_type, html = get_text("/")
assert status == 200
assert "text/html" in content_type
for expected in ("Event to simulation", "event-query", "preview-job", "domain-preview", "/web/app.js"):
    assert expected in html, expected

status, content_type, js = get_text("/web/app.js")
assert status == 200
assert "javascript" in content_type
for expected in ("/api/events", "/api/jobs/preview", "/api/jobs", "renderDomainPreview"):
    assert expected in js, expected

status, content_type, css = get_text("/web/styles.css")
assert status == 200
assert "text/css" in content_type
for expected in (".domain-preview", ".event-card", ".status-pill"):
    assert expected in css, expected

xaver = request("GET", "/api/events/xaver")
assert xaver["ok"] is True
assert xaver["event"]["id"] == "xaver"
assert xaver["domain_presets"]
assert xaver["resolution_presets"]

preview = request(
    "POST",
    "/api/jobs/preview",
    {
        "event": "Xaver",
        "domain": "northern-germany-27km",
        "resolution": "quick-preview",
        "mode": "dry-run",
        "job_id": "web-ci-dry-run",
    },
)
assert preview["ok"] is True
assert preview["valid"] is True
assert preview["config"]["id"] == "web-ci-dry-run"
assert preview["config"]["metadata"]["event_id"] == "xaver"

print("Workbench web UI smoke tests passed")
PY
