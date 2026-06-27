#!/bin/sh
# ci/test-workbench.sh — Workbench MVP test suite
#
# Tests:
#   1. Config validation — all example configs pass
#   2. Config validation — invalid configs are rejected with useful errors
#   3. Event catalogue   — catalogue loads and contains required events
#   4. Dry-run execution — run.sh creates the run directory and status files
#   5. ERA5 offline mode — era5-offline mode succeeds through the Workbench
#   6. Failure path      — run.sh exits non-zero for an invalid config
#
# Requires: Python 3 (stdlib only)
# Does NOT require: Docker, CDS credentials, NCAR infrastructure

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

WORKBENCH="${REPO_ROOT}/workbench"
PASS=0
FAIL=0

pass() { echo "[PASS] $*"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $*" >&2; FAIL=$((FAIL + 1)); }

echo "=== Workbench tests ==="
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. Config validation — valid example configs
# ─────────────────────────────────────────────────────────────────────────────
echo "-- Test 1: Valid example configs --"

for cfg in \
    "${WORKBENCH}/examples/xaver-dry-run.json" \
    "${WORKBENCH}/examples/kyrill-dry-run.json" \
    "${WORKBENCH}/examples/wrf-smoke.json" \
    "${WORKBENCH}/examples/era5-offline.json"; do
    name=$(basename "${cfg}")
    if python3 "${WORKBENCH}/validate.py" "${cfg}" >/dev/null 2>&1; then
        pass "Validate ${name}"
    else
        fail "Validate ${name} (expected to pass)"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
# 2. Config validation — invalid configs are rejected
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 2: Invalid config rejection --"

TMP=$(mktemp -d)
trap 'rm -rf "${TMP}"' EXIT

# Missing required fields
cat > "${TMP}/missing-fields.json" <<'JSON'
{"id": "bad-config"}
JSON
if python3 "${WORKBENCH}/validate.py" "${TMP}/missing-fields.json" >/dev/null 2>&1; then
    fail "Missing-fields config should have been rejected"
else
    pass "Missing-fields config rejected"
fi

# Invalid mode
cat > "${TMP}/bad-mode.json" <<'JSON'
{
  "id": "test-job",
  "mode": "not-a-real-mode",
  "name": "Test",
  "period": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-02T00:00:00Z"},
  "domain": {
    "label": "test", "center_lat": 50.0, "center_lon": 10.0,
    "dx_km": 9, "dy_km": 9, "e_we": 10, "e_sn": 10
  },
  "inputs": {"source": "none"},
  "outputs": {"directory": "workbench-runs/test"}
}
JSON
if python3 "${WORKBENCH}/validate.py" "${TMP}/bad-mode.json" >/dev/null 2>&1; then
    fail "Bad-mode config should have been rejected"
else
    pass "Bad-mode config rejected"
fi

# start >= end
cat > "${TMP}/bad-period.json" <<'JSON'
{
  "id": "test-period",
  "mode": "dry-run",
  "name": "Test",
  "period": {"start": "2024-01-02T00:00:00Z", "end": "2024-01-01T00:00:00Z"},
  "domain": {
    "label": "test", "center_lat": 50.0, "center_lon": 10.0,
    "dx_km": 9, "dy_km": 9, "e_we": 10, "e_sn": 10
  },
  "inputs": {"source": "none"},
  "outputs": {"directory": "workbench-runs/test"}
}
JSON
if python3 "${WORKBENCH}/validate.py" "${TMP}/bad-period.json" >/dev/null 2>&1; then
    fail "Bad-period config (start >= end) should have been rejected"
else
    pass "Bad-period config (start >= end) rejected"
fi

# Out-of-range latitude
cat > "${TMP}/bad-lat.json" <<'JSON'
{
  "id": "test-lat",
  "mode": "dry-run",
  "name": "Test",
  "period": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-02T00:00:00Z"},
  "domain": {
    "label": "test", "center_lat": 999.0, "center_lon": 10.0,
    "dx_km": 9, "dy_km": 9, "e_we": 10, "e_sn": 10
  },
  "inputs": {"source": "none"},
  "outputs": {"directory": "workbench-runs/test"}
}
JSON
if python3 "${WORKBENCH}/validate.py" "${TMP}/bad-lat.json" >/dev/null 2>&1; then
    fail "Out-of-range latitude should have been rejected"
else
    pass "Out-of-range latitude rejected"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. Event catalogue loading
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 3: Event catalogue --"

python3 - "${WORKBENCH}/events/catalogue.json" <<'PY'
import json, sys
from pathlib import Path

path = sys.argv[1]
catalogue = json.loads(Path(path).read_text(encoding="utf-8"))
events = catalogue.get("events", {})

required_events = ("xaver", "kyrill", "custom-template")
required_fields = ("name", "description", "start", "end", "default_domain", "domain", "suggested_outputs")

for event_id in required_events:
    if event_id not in events:
        raise SystemExit(f"Catalogue missing required event: {event_id!r}")
    event = events[event_id]
    for field in required_fields:
        if field not in event:
            raise SystemExit(f"Event {event_id!r} is missing field: {field!r}")
    if not isinstance(event["suggested_outputs"], list) or not event["suggested_outputs"]:
        raise SystemExit(f"Event {event_id!r} suggested_outputs must be a non-empty list")

print(f"Catalogue OK: {len(events)} events, all required fields present")
PY
pass "Event catalogue loads with required events and fields"

# ─────────────────────────────────────────────────────────────────────────────
# 4. Dry-run execution
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 4: Dry-run execution --"

DRY_RUN_DIR="${TMP}/dry-run-outputs"
cat > "${TMP}/dry-run-test.json" <<JSON
{
  "id": "wb-ci-dry-run",
  "mode": "dry-run",
  "name": "CI Dry-Run Test",
  "period": {"start": "2013-12-05T00:00:00Z", "end": "2013-12-06T00:00:00Z"},
  "domain": {
    "label": "northern-germany",
    "center_lat": 54.0, "center_lon": 9.0,
    "dx_km": 9, "dy_km": 9, "e_we": 50, "e_sn": 50
  },
  "inputs": {"source": "era5"},
  "outputs": {"directory": "${DRY_RUN_DIR}"}
}
JSON

if sh "${WORKBENCH}/run.sh" "${TMP}/dry-run-test.json" >/dev/null 2>&1; then
    # Verify run directory structure
    all_ok=1
    for expected in "job.json" "status.json" "logs" "outputs"; do
        if [ ! -e "${DRY_RUN_DIR}/${expected}" ]; then
            fail "Dry-run missing '${expected}' in run directory"
            all_ok=0
        fi
    done

    # Verify status = succeeded
    STATUS=$(python3 -c "import json; print(json.load(open('${DRY_RUN_DIR}/status.json'))['status'])")
    if [ "${STATUS}" != "succeeded" ]; then
        fail "Dry-run status expected 'succeeded', got '${STATUS}'"
        all_ok=0
    fi

    # Verify job.json has expected fields
    python3 - "${DRY_RUN_DIR}/job.json" <<'PY' || all_ok=0
import json, sys
from pathlib import Path
job = json.loads(Path(sys.argv[1]).read_text())
for field in ("job_id", "mode", "name", "config_file", "run_dir", "start_time", "config"):
    if field not in job:
        raise SystemExit(f"job.json missing field: {field!r}")
PY

    if [ "${all_ok}" -eq 1 ]; then
        pass "Dry-run creates correct run directory structure"
        pass "Dry-run status = succeeded"
        pass "job.json contains all required fields"
    fi
else
    fail "Dry-run execution failed (expected success)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. ERA5 offline mode via Workbench
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 5: ERA5 offline mode via Workbench --"

ERA5_RUN_DIR="${TMP}/era5-offline-outputs"
cat > "${TMP}/era5-offline-test.json" <<JSON
{
  "id": "wb-ci-era5-offline",
  "mode": "era5-offline",
  "name": "CI ERA5 Offline Test",
  "period": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
  "domain": {
    "label": "europe",
    "center_lat": 50.0, "center_lon": 10.0,
    "dx_km": 9, "dy_km": 9, "e_we": 50, "e_sn": 50
  },
  "inputs": {"source": "era5"},
  "outputs": {"directory": "${ERA5_RUN_DIR}"}
}
JSON

if sh "${WORKBENCH}/run.sh" "${TMP}/era5-offline-test.json" >/dev/null 2>&1; then
    ERA5_STATUS=$(python3 -c "import json; print(json.load(open('${ERA5_RUN_DIR}/status.json'))['status'])")
    if [ "${ERA5_STATUS}" = "succeeded" ]; then
        pass "ERA5 offline mode via Workbench succeeded"
    else
        fail "ERA5 offline mode status expected 'succeeded', got '${ERA5_STATUS}'"
    fi
else
    fail "ERA5 offline mode via Workbench failed"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6. Failure path with invalid config
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 6: Failure path --"

cat > "${TMP}/invalid-for-run.json" <<'JSON'
{"id": "bad"}
JSON

if sh "${WORKBENCH}/run.sh" "${TMP}/invalid-for-run.json" >/dev/null 2>&1; then
    fail "run.sh should have failed with invalid config"
else
    pass "run.sh exits non-zero with invalid config"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi

echo "All Workbench tests passed."
