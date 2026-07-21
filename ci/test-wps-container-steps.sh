#!/bin/sh
# Step-separated WPS binary compatibility test using bundled test fixtures.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
TEST_DATA="${REPO_ROOT}/tests/era5-mini"
ERA5_IMAGE="${ERA5_IMAGE:-era5-pipeline:latest}"
WORKDIR="${WORKDIR:-/tmp/wps-container-steps}"
DOCKER_USER="$(id -u):$(id -g)"
SPEC_KEY=$(printf 'a%.0s' $(seq 1 64))
PLAN_KEY=$(printf 'b%.0s' $(seq 1 64))
SPEC_DIR="${WORKDIR}/specifications/${SPEC_KEY}"
ERA5_DIR="${WORKDIR}/cache/${PLAN_KEY}"
RUN_DIR="${WORKDIR}/run"
WPS_WORK="${RUN_DIR}/work/wps"

rm -rf "${WORKDIR}"
mkdir -p "${SPEC_DIR}" "${ERA5_DIR}/files" "${WPS_WORK}" \
  "${RUN_DIR}/steps/ungrib" "${RUN_DIR}/steps/metgrid"
cp "${TEST_DATA}/pressure.grib" "${ERA5_DIR}/files/pressure.grib"
cp "${TEST_DATA}/surface.grib" "${ERA5_DIR}/files/surface.grib"
cp "${TEST_DATA}/wps/namelist.wps" "${SPEC_DIR}/namelist.wps"
cp "${TEST_DATA}/wps/geo_em.d01.nc" "${WPS_WORK}/geo_em.d01.nc"

cat > "${SPEC_DIR}/run-specification.json" <<JSON
{
  "specification_key": "${SPEC_KEY}",
  "immutable": true,
  "execution_started": false,
  "identity": {
    "era5_input": {
      "plan_key": "${PLAN_KEY}",
      "files": [
        {
          "path": "files/pressure.grib",
          "request_name": "pressure_levels_mini"
        },
        {
          "path": "files/surface.grib",
          "request_name": "single_levels_mini"
        }
      ]
    }
  }
}
JSON

IMAGE_CONTRACT_LOG="${WORKDIR}/image-contract.log"
if ! docker run --rm \
  --network=none \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --pids-limit 64 \
  --user "${DOCKER_USER}" \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --entrypoint python3 \
  "${ERA5_IMAGE}" \
  /usr/local/bin/run-wps-step.py --help \
  >"${IMAGE_CONTRACT_LOG}" 2>&1; then
  cat "${IMAGE_CONTRACT_LOG}"
  exit 1
fi
cat "${IMAGE_CONTRACT_LOG}"

run_step() {
  step=$1
  container_log="${WORKDIR}/${step}-container.log"
  echo "Running isolated WPS step: ${step}"
  if ! docker run --rm \
    --network=none \
    --read-only \
    --cap-drop=ALL \
    --security-opt=no-new-privileges:true \
    --pids-limit 256 \
    --user "${DOCKER_USER}" \
    --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    --entrypoint python3 \
    -v "${SPEC_DIR}:/spec:ro" \
    -v "${ERA5_DIR}:/era5:ro" \
    -v "${RUN_DIR}:/run:rw" \
    "${ERA5_IMAGE}" \
    /usr/local/bin/run-wps-step.py \
      --step "${step}" \
      --specification /spec/run-specification.json \
      --era5-root /era5 \
      --run-root /run \
      --workdir /run/work/wps \
      --result "/run/steps/${step}/result.json" \
      --progress "/run/steps/${step}/progress.json" \
    >"${container_log}" 2>&1; then
    cat "${container_log}"
    exit 1
  fi
  cat "${container_log}"
  python3 - "${RUN_DIR}/steps/${step}/result.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "SUCCEEDED":
    raise SystemExit(f"WPS step did not succeed: {payload}")
if not payload.get("artifacts"):
    raise SystemExit(f"WPS step returned no artifacts: {payload}")
print(f"{payload['status']}: {len(payload['artifacts'])} artifacts")
PY
}

run_step ungrib
run_step metgrid

test -n "$(find "${WPS_WORK}" -maxdepth 1 -name 'PLEV:*' -print -quit)"
test -n "$(find "${WPS_WORK}" -maxdepth 1 -name 'SFC:*' -print -quit)"
test -n "$(find "${WPS_WORK}" -maxdepth 1 -name 'met_em.d01.*' -print -quit)"

cat > "${RUN_DIR}/check-vars.py" <<'PY'
import re
import subprocess
from pathlib import Path
met_em = sorted(Path('/run/work/wps').glob('met_em.d01.*'))
if not met_em:
    raise SystemExit('No met_em output found')
header = subprocess.run(
    ['ncdump', '-h', str(met_em[0])], capture_output=True, text=True, check=True
).stdout
declared = set(re.findall(r'^\s+\S+\s+(\w+)\s*\(', header, re.MULTILINE))
required = {'TT', 'UU', 'VV', 'GHT', 'PSFC'}
missing = sorted(required - declared)
if missing:
    raise SystemExit(f'Missing variables: {missing}; found: {sorted(declared)}')
print(f'Validated {met_em[0].name}: {sorted(required)}')
PY

VERIFY_LOG="${WORKDIR}/verify-container.log"
if ! docker run --rm \
  --network=none \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --user "${DOCKER_USER}" \
  --entrypoint python3 \
  -v "${RUN_DIR}:/run:ro" \
  "${ERA5_IMAGE}" \
  /run/check-vars.py \
  >"${VERIFY_LOG}" 2>&1; then
  cat "${VERIFY_LOG}"
  exit 1
fi
cat "${VERIFY_LOG}"

echo "Step-separated WPS container compatibility test passed."
