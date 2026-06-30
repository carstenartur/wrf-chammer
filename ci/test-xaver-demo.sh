#!/bin/sh
# ci/test-xaver-demo.sh — Xaver end-to-end acceptance scenario smoke test.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
WORKBENCH="${REPO_ROOT}/workbench"
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

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

PORT=$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)

SERVER_LOG="${TMP}/server.log"
cd "${REPO_ROOT}"
python3 -m workbench.server.server --host 127.0.0.1 --port "${PORT}" >"${SERVER_LOG}" 2>&1 &
SERVER_PID=$!

python3 - "${PORT}" "${SERVER_LOG}" <<'PY'
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen
port = sys.argv[1]
log = Path(sys.argv[2])
for _ in range(60):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            if json.loads(response.read().decode("utf-8")).get("ok"):
                raise SystemExit(0)
    except Exception:
        time.sleep(0.1)
print(log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
raise SystemExit("API server did not become healthy")
PY
pass "Local Workbench API is healthy"

python3 - "${PORT}" <<'PY'
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
port = sys.argv[1]
base = f"http://127.0.0.1:{port}"

def get_text(path):
    with urlopen(base + path, timeout=10) as response:
        return response.status, response.read().decode("utf-8")

def request(method, path, body=None, expected_status=200):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    if status != expected_status:
        raise AssertionError(f"{method} {path}: expected {expected_status}, got {status}: {payload}")
    return payload

status, html = get_text("/")
assert status == 200
for expected in ("Event to simulation", "event-query", "preview-job", "run-job"):
    assert expected in html, expected

search = request("GET", "/api/events?q=xaver")
assert any(event["id"] == "xaver" for event in search["events"])

detail = request("GET", "/api/events/xaver")
assert detail["event"]["id"] == "xaver"
assert detail["event"]["default_domain"]
assert detail["domain_presets"]
assert detail["resolution_presets"]

preview = request("POST", "/api/jobs/preview", {
    "event": "Xaver",
    "domain": "northern-germany-27km",
    "resolution": "quick-preview",
    "mode": "dry-run",
    "job_id": "xaver-e2e-ui-dry-run"
})
assert preview["ok"] is True
assert preview["valid"] is True
assert preview["config"]["metadata"]["event_id"] == "xaver"

created = request("POST", "/api/jobs", {"config": preview["config"], "start": True}, expected_status=201)
assert created["ok"] is True
assert created["job"]["status"]["status"] == "succeeded"

status_payload = request("GET", "/api/jobs/xaver-e2e-ui-dry-run")
assert status_payload["job"]["status"]["status"] == "succeeded"
logs = request("GET", "/api/jobs/xaver-e2e-ui-dry-run/logs")
assert any("Dry run complete" in log.get("content", "") for log in logs["logs"])

print("Xaver UI/API dry-run path passed")
PY
pass "Xaver UI/API dry-run path passed"

PIPELINE_DIR="${TMP}/xaver-era5-wrf"
mkdir -p "${PIPELINE_DIR}/outputs/era5"
cp "${REPO_ROOT}/ci/era5/dummy-era5.grib" "${PIPELINE_DIR}/outputs/era5/dummy-era5.grib"

cat > "${TMP}/xaver-era5-wrf.json" <<JSON
{
  "id": "xaver-e2e-era5-wrf",
  "mode": "era5-wrf",
  "name": "Xaver E2E ERA5 WRF Demo",
  "period": {"start": "2013-12-05T00:00:00Z", "end": "2013-12-06T00:00:00Z"},
  "domain": {
    "label": "northern-germany-27km",
    "center_lat": 54.0,
    "center_lon": 9.0,
    "dx_km": 27,
    "dy_km": 27,
    "e_we": 30,
    "e_sn": 24
  },
  "inputs": {
    "source": "era5",
    "era5": {"config": "ci/era5/era5-offline-test-config.json"}
  },
  "outputs": {"directory": "${PIPELINE_DIR}"}
}
JSON

if sh "${WORKBENCH}/run.sh" "${TMP}/xaver-era5-wrf.json" >/dev/null 2>&1; then
    pass "Xaver cached ERA5-WRF path succeeded"
else
    cat "${PIPELINE_DIR}/logs/workbench.log" >&2 || true
    fail "Xaver cached ERA5-WRF path failed"
fi

for expected in \
    "${PIPELINE_DIR}/status.json" \
    "${PIPELINE_DIR}/namelists/namelist.wps" \
    "${PIPELINE_DIR}/namelists/namelist.input" \
    "${PIPELINE_DIR}/outputs/era5-manifest.json" \
    "${PIPELINE_DIR}/outputs/pipeline-metadata.json" \
    "${PIPELINE_DIR}/visualizations/metadata.json"; do
    [ -f "${expected}" ] || fail "Missing Xaver demo artifact: ${expected}"
done
pass "Xaver cached pipeline artifacts exist"

python3 - "${PIPELINE_DIR}" <<'PY'
import json
import sys
from pathlib import Path
run_dir = Path(sys.argv[1])
status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
assert status["status"] == "succeeded"
meta = json.loads((run_dir / "outputs" / "pipeline-metadata.json").read_text(encoding="utf-8"))
assert meta["job_id"] == "xaver-e2e-era5-wrf"
assert meta["mode"] == "era5-wrf"
assert meta["real_wps_wrf_executed"] is False
vis = json.loads((run_dir / "visualizations" / "metadata.json").read_text(encoding="utf-8"))
layer_ids = {layer["id"] for layer in vis.get("layers", [])}
assert "wind10m" in layer_ids
assert "max_wind10m" in layer_ids
assert vis.get("times")
print("Xaver visualization layers:", ", ".join(sorted(layer_ids)))
PY
pass "Xaver visualization metadata contains wind layers"

echo "All Xaver end-to-end demo checks passed."
