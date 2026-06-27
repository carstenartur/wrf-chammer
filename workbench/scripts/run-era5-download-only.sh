#!/bin/sh
# workbench/scripts/run-era5-download-only.sh — ERA5 download-only mode
#
# Downloads ERA5 data using the request config specified in inputs.era5.config.
# Reuses ci/download-era5.sh; writes files to <run_dir>/outputs/era5/ and
# writes a manifest to <run_dir>/outputs/era5-manifest.json.
#
# For CI/cached runs, pre-seed <run_dir>/outputs/era5/<target> with a dummy
# file so that download-era5.py skips the CDS download and marks the entry
# as "cached" in the manifest.
#
# Arguments:
#   $1  path to the validated job config JSON file
#   $2  path to the run directory (already created by run.sh)

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)

CONFIG_FILE="$1"
RUN_DIR="$2"

echo "=== WRF Workbench — ERA5 Download Only ==="
echo ""

# ── Extract inputs.era5.config from job config ────────────────────────────────
ERA5_CONFIG_REL=$(python3 -c "
import json, sys
cfg = json.load(open(sys.argv[1]))
inputs = cfg.get('inputs', {})
era5 = inputs.get('era5', {})
val = era5.get('config', '')
if not val:
    raise SystemExit(\"'inputs.era5.config' is missing or empty in the job config.\")
print(val)
" "${CONFIG_FILE}")

# Resolve relative paths from the repository root
case "${ERA5_CONFIG_REL}" in
    /*) ERA5_CONFIG="${ERA5_CONFIG_REL}" ;;
    *)  ERA5_CONFIG="${REPO_ROOT}/${ERA5_CONFIG_REL}" ;;
esac

if [ ! -f "${ERA5_CONFIG}" ]; then
    echo "Error: ERA5 config file not found: ${ERA5_CONFIG}" >&2
    exit 1
fi

# Verify the config contains a non-empty 'requests' object
python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    cfg = json.load(f)
requests = cfg.get('requests')
if not isinstance(requests, dict) or not requests:
    raise SystemExit('ERA5 config must contain a non-empty requests object: ' + sys.argv[1])
" "${ERA5_CONFIG}"

# ── Set environment variables for download-era5.sh ────────────────────────────
ERA5_OUTPUT_DIR="${RUN_DIR}/outputs/era5"
ERA5_MANIFEST="${RUN_DIR}/outputs/era5-manifest.json"

export ERA5_CONFIG
export ERA5_OUTPUT_DIR
export ERA5_MANIFEST

mkdir -p "${ERA5_OUTPUT_DIR}"

echo "ERA5 config  : ${ERA5_CONFIG}"
echo "Output dir   : ${ERA5_OUTPUT_DIR}"
echo "Manifest     : ${ERA5_MANIFEST}"
echo ""

# ── Run download-era5.sh ──────────────────────────────────────────────────────
echo "Running ci/download-era5.sh ..."
sh "${REPO_ROOT}/ci/download-era5.sh"

# ── Verify manifest and target files ─────────────────────────────────────────
echo ""
echo "Verifying manifest and target files ..."

if [ ! -f "${ERA5_MANIFEST}" ]; then
    echo "Error: Manifest file was not created: ${ERA5_MANIFEST}" >&2
    exit 1
fi

python3 - "${ERA5_MANIFEST}" "${ERA5_OUTPUT_DIR}" <<'PY'
import json, sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
output_dir    = Path(sys.argv[2])

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
outputs  = manifest.get("outputs", [])

if not isinstance(outputs, list) or not outputs:
    raise SystemExit(f"Manifest has no 'outputs' entries: {manifest_path}")

missing  = []
empty    = []

for entry in outputs:
    target = entry.get("target", "")
    t_path = Path(target)
    if target and not t_path.is_absolute():
        p = output_dir / target
    else:
        p = t_path
    if not p.exists():
        missing.append(str(p))
    elif p.stat().st_size == 0:
        empty.append(str(p))

if missing:
    raise SystemExit("Manifest references missing target files:\n  " + "\n  ".join(missing))
if empty:
    raise SystemExit("Manifest references empty target files:\n  " + "\n  ".join(empty))

print(f"Manifest OK: {len(outputs)} target(s) verified.")
PY

echo ""
echo "ERA5 download-only completed successfully."
echo "Manifest : ${ERA5_MANIFEST}"
echo "Outputs  : ${ERA5_OUTPUT_DIR}"
