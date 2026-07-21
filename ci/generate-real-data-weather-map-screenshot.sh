#!/bin/sh
# ci/generate-real-data-weather-map-screenshot.sh — capture a documentation weather map from real WRF visualization artifacts.
#
# This script intentionally requires existing real visualization artifacts. It
# never generates synthetic fixture data.

set -eu

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <real-visualization-dir> [layer-id]" >&2
    echo "Example: $0 workbench-runs/xaver-real/visualizations max_wind10m" >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
E2E_DIR="${REPO_ROOT}/workbench/e2e"
REAL_VIS_DIR=$1
LAYER_ID=${2:-max_wind10m}

if [ ! -f "${REAL_VIS_DIR}/metadata.json" ] || [ ! -d "${REAL_VIS_DIR}/layers" ]; then
    echo "ERROR: ${REAL_VIS_DIR} is not a real visualization artifact directory." >&2
    echo "Expected metadata.json and layers/." >&2
    exit 1
fi

python3 - "${REAL_VIS_DIR}" "${LAYER_ID}" <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
layer_id = sys.argv[2]
metadata_path = root / "metadata.json"
if metadata_path.is_symlink() or not metadata_path.is_file():
    raise SystemExit("ERROR: metadata.json must be a regular non-symlink file.")
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
provenance = metadata.get("provenance")
if not isinstance(provenance, dict) or provenance.get("mode") != "wrf":
    raise SystemExit("ERROR: metadata.json does not prove real WRF provenance.")
wrfout = provenance.get("wrfout_files")
if not isinstance(wrfout, list) or not wrfout:
    raise SystemExit("ERROR: metadata.json contains no wrfout provenance.")
layer = next((item for item in metadata.get("layers", []) if item.get("id") == layer_id), None)
if not isinstance(layer, dict):
    raise SystemExit(f"ERROR: layer {layer_id!r} is not listed in metadata.json.")
relative = layer.get("file")
if not isinstance(relative, str) or not relative or os.path.isabs(relative) or ".." in Path(relative).parts:
    raise SystemExit(f"ERROR: layer {layer_id!r} has an unsafe file path.")
layer_path = (root / relative).resolve()
if root not in layer_path.parents or layer_path.is_symlink() or not layer_path.is_file():
    raise SystemExit(f"ERROR: layer {layer_id!r} is not a safe regular product file.")
print(f"Validated real WRF visualization layer: {layer_id} ({layer_path.relative_to(root)})")
PY

cd "${E2E_DIR}"
if [ ! -d node_modules ]; then
    npm install
fi

if [ "${WORKBENCH_SKIP_PLAYWRIGHT_INSTALL:-}" != "1" ]; then
    if [ "${CI:-}" = "true" ]; then
        npx playwright install --with-deps chromium
    else
        npx playwright install chromium
    fi
fi

WORKBENCH_REAL_VIS_DATA_DIR="${REAL_VIS_DIR}" \
WORKBENCH_REAL_VIS_LAYER="${LAYER_ID}" \
npm run screenshots:real-data
