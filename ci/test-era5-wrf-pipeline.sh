#!/bin/sh
# ci/test-era5-wrf-pipeline.sh — cacheable ERA5 -> WRF pipeline checks.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
WORKBENCH="${REPO_ROOT}/workbench"
TMP=$(mktemp -d)
trap 'rm -rf "${TMP}"' EXIT

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

CONFIG="${WORKBENCH}/examples/xaver-era5-wrf.json"
python3 "${WORKBENCH}/validate.py" "${CONFIG}" >/dev/null
pass "Validate xaver-era5-wrf example"

RUN_DIR="${TMP}/era5-wrf-run"
mkdir -p "${RUN_DIR}/outputs/era5"
cp "${REPO_ROOT}/ci/era5/dummy-era5.grib" "${RUN_DIR}/outputs/era5/dummy-era5.grib"

cat > "${TMP}/era5-wrf-ci.json" <<JSON
{
  "id": "wb-ci-era5-wrf",
  "mode": "era5-wrf",
  "name": "CI ERA5 WRF Pipeline",
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
  "outputs": {"directory": "${RUN_DIR}"}
}
JSON

if sh "${WORKBENCH}/run.sh" "${TMP}/era5-wrf-ci.json" >/dev/null 2>&1; then
    pass "Cached era5-wrf workbench run succeeds"
else
    cat "${RUN_DIR}/logs/workbench.log" >&2 || true
    fail "Cached era5-wrf workbench run failed"
fi

for expected in \
    "${RUN_DIR}/namelists/namelist.wps" \
    "${RUN_DIR}/namelists/namelist.input" \
    "${RUN_DIR}/outputs/era5-manifest.json" \
    "${RUN_DIR}/outputs/pipeline-metadata.json" \
    "${RUN_DIR}/visualizations/metadata.json"; do
    [ -f "${expected}" ] || fail "Missing expected file: ${expected}"
done
pass "Pipeline artifacts exist"

grep -q "start_date = '2013-12-05_00:00:00'" "${RUN_DIR}/namelists/namelist.wps" || fail "namelist.wps missing start_date"
grep -q "e_we              =   30" "${RUN_DIR}/namelists/namelist.wps" || fail "namelist.wps missing e_we"
grep -q "dx = 27000" "${RUN_DIR}/namelists/namelist.wps" || fail "namelist.wps missing dx"
grep -q "start_year                          = 2013" "${RUN_DIR}/namelists/namelist.input" || fail "namelist.input missing start_year"
grep -q "e_we                                = 30" "${RUN_DIR}/namelists/namelist.input" || fail "namelist.input missing e_we"
pass "Generated namelists contain expected fields"

python3 - "${RUN_DIR}/outputs/pipeline-metadata.json" <<'PY'
import json
import sys
from pathlib import Path
meta = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
assert meta['mode'] == 'era5-wrf'
assert meta['real_wps_wrf_executed'] is False
assert meta['namelist_wps'] == 'namelists/namelist.wps'
assert meta['visualization_metadata'] == 'visualizations/metadata.json'
assert meta['wrf_outputs']
PY
pass "Pipeline metadata is valid"

MISSING_DIR="${TMP}/era5-wrf-missing-cache"
cat > "${TMP}/era5-wrf-missing.json" <<JSON
{
  "id": "wb-ci-era5-wrf-missing",
  "mode": "era5-wrf",
  "name": "CI ERA5 WRF Missing Cache",
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
  "outputs": {"directory": "${MISSING_DIR}"}
}
JSON

if sh "${WORKBENCH}/run.sh" "${TMP}/era5-wrf-missing.json" >/dev/null 2>&1; then
    fail "era5-wrf should fail when cached ERA5 input is absent"
else
    grep -q "Cached ERA5 input is missing" "${MISSING_DIR}/logs/workbench.log" || fail "Missing-cache failure was not clear"
    pass "Missing cached input fails clearly"
fi

echo "All ERA5-WRF pipeline tests passed."
