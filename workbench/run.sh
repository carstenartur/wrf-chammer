#!/bin/sh
# workbench/run.sh — WRF Workbench entrypoint
#
# Usage:
#   ./workbench/run.sh <config-file.json>
#
# Supported modes:
#   dry-run        Validate config and show planned steps (no Docker required)
#   wrf-smoke      Run the WRF idealized smoke test via wrf-reproducible:latest
#   era5-offline   Run ERA5 offline validation via existing ci/ scripts
#
# Each run creates a directory at the path specified by outputs.directory
# (relative paths are resolved from the repository root):
#
#   <outputs.directory>/
#     job.json      — full job metadata and configuration
#     status.json   — job status (created/running/succeeded/failed)
#     logs/         — workbench and mode-specific logs
#     outputs/      — simulation or validation outputs
#
# See workbench/README.md for full documentation.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

usage() {
    cat >&2 <<EOF
Usage: $0 <config-file.json>

Run a WRF Workbench job from a JSON configuration file.

Supported modes:
  dry-run        Validate config and print planned steps (no Docker required)
  wrf-smoke      Run WRF idealized smoke test (requires wrf-reproducible:latest)
  era5-offline   Run ERA5 offline validation (requires Python 3)

Example:
  $0 workbench/examples/xaver-dry-run.json
EOF
    exit 1
}

[ "$#" -eq 1 ] || usage

CONFIG_FILE="$1"

if [ ! -f "${CONFIG_FILE}" ]; then
    echo "Error: Config file not found: ${CONFIG_FILE}" >&2
    exit 1
fi

# ── Validate configuration ────────────────────────────────────────────────────
echo "==> Validating config: ${CONFIG_FILE}"
python3 "${SCRIPT_DIR}/validate.py" "${CONFIG_FILE}" || exit 1

# ── Extract required fields ───────────────────────────────────────────────────
JOB_ID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['id'])" "${CONFIG_FILE}")
JOB_MODE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['mode'])" "${CONFIG_FILE}")
JOB_NAME=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['name'])" "${CONFIG_FILE}")
OUTPUTS_DIR=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['outputs']['directory'])" "${CONFIG_FILE}")

# Resolve outputs directory: absolute paths are used as-is; relative paths are
# anchored at the repository root.
case "${OUTPUTS_DIR}" in
    /*) RUN_DIR="${OUTPUTS_DIR}" ;;
    *)  RUN_DIR="${REPO_ROOT}/${OUTPUTS_DIR}" ;;
esac

START_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "==> Job     : ${JOB_NAME} (id=${JOB_ID})"
echo "==> Mode    : ${JOB_MODE}"
echo "==> Run dir : ${RUN_DIR}"
echo ""

# ── Create run directory ──────────────────────────────────────────────────────
mkdir -p "${RUN_DIR}/logs" "${RUN_DIR}/outputs"

# ── Write job.json ────────────────────────────────────────────────────────────
python3 -c "
import json, sys
from pathlib import Path
config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
job = {
    'job_id':       config['id'],
    'mode':         config['mode'],
    'name':         config['name'],
    'config_file':  sys.argv[1],
    'run_dir':      sys.argv[2],
    'start_time':   sys.argv[3],
    'config':       config,
}
Path(sys.argv[2], 'job.json').write_text(json.dumps(job, indent=2) + '\n', encoding='utf-8')
" "${CONFIG_FILE}" "${RUN_DIR}" "${START_TIME}"

# ── Write initial status.json ─────────────────────────────────────────────────
python3 -c "
import json, sys
from pathlib import Path
status = {
    'job_id':     sys.argv[1],
    'mode':       sys.argv[2],
    'status':     'running',
    'start_time': sys.argv[3],
    'end_time':   None,
    'exit_code':  None,
    'error':      None,
}
Path(sys.argv[4], 'status.json').write_text(json.dumps(status, indent=2) + '\n', encoding='utf-8')
" "${JOB_ID}" "${JOB_MODE}" "${START_TIME}" "${RUN_DIR}"

# ── Select mode script ────────────────────────────────────────────────────────
MODE_SCRIPT="${SCRIPT_DIR}/scripts/run-${JOB_MODE}.sh"
if [ ! -f "${MODE_SCRIPT}" ]; then
    echo "Error: No script found for mode '${JOB_MODE}': ${MODE_SCRIPT}" >&2
    exit 1
fi

# ── Execute mode ──────────────────────────────────────────────────────────────
EXIT_CODE=0
set +e
sh "${MODE_SCRIPT}" "${CONFIG_FILE}" "${RUN_DIR}" > "${RUN_DIR}/logs/workbench.log" 2>&1
EXIT_CODE=$?
set -e

# Always echo the log so CI and interactive runs see the output
cat "${RUN_DIR}/logs/workbench.log"

END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [ "${EXIT_CODE}" -eq 0 ]; then
    FINAL_STATUS="succeeded"
else
    FINAL_STATUS="failed"
fi

# ── Update status.json ────────────────────────────────────────────────────────
python3 -c "
import json, sys
from pathlib import Path
p = Path(sys.argv[1], 'status.json')
s = json.loads(p.read_text(encoding='utf-8'))
s['status']    = sys.argv[2]
s['end_time']  = sys.argv[3]
s['exit_code'] = int(sys.argv[4])
if int(sys.argv[4]) != 0:
    s['error'] = 'Job exited with code ' + sys.argv[4]
p.write_text(json.dumps(s, indent=2) + '\n', encoding='utf-8')
" "${RUN_DIR}" "${FINAL_STATUS}" "${END_TIME}" "${EXIT_CODE}"

echo ""
echo "==> Status  : ${FINAL_STATUS}"
echo "==> Outputs : ${RUN_DIR}"

exit "${EXIT_CODE}"
