#!/bin/sh
# workbench/scripts/run-wrf-smoke.sh — WRF smoke-test mode
#
# Runs the WRF idealized smoke test through the wrf-reproducible Docker image.
# The image must be built locally first:
#
#   docker build -f Dockerfile -t wrf-reproducible:latest .
#
# Arguments:
#   $1  path to the validated job config JSON file
#   $2  path to the run directory (already created by run.sh)

set -eu

CONFIG_FILE="$1"
RUN_DIR="$2"

echo "=== WRF Workbench — WRF Smoke Test ==="
echo ""

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is not available. Install Docker to run wrf-smoke mode." >&2
    exit 1
fi

if ! docker image inspect wrf-reproducible:latest >/dev/null 2>&1; then
    echo "Error: Docker image 'wrf-reproducible:latest' not found." >&2
    echo "Build it first with:" >&2
    echo "  docker build -f Dockerfile -t wrf-reproducible:latest ." >&2
    exit 1
fi

echo "Running WRF smoke test inside wrf-reproducible:latest ..."
echo ""

docker run --rm \
    --name "wrf-workbench-smoke-$$" \
    -v "${RUN_DIR}/logs:/workbench-logs" \
    -v "${RUN_DIR}/outputs:/workbench-outputs" \
    wrf-reproducible:latest \
    sh -c '
        set -eu
        smoke-test-wrf.sh
        cp /tmp/wrf-smoke-test/wrfout_d01_* /workbench-outputs/ 2>/dev/null || true
        cp /tmp/wrf-smoke-test/*.log /workbench-logs/ 2>/dev/null || true
    '

echo ""
echo "WRF smoke test completed successfully."
echo "Outputs written to: ${RUN_DIR}/outputs/"
