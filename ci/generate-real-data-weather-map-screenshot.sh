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
