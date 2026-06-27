#!/bin/sh
# visualization/postprocess/run.sh — WRF postprocessing entrypoint
#
# Converts WRF output files into web-friendly visualization artifacts.
#
# Usage:
#   ./visualization/postprocess/run.sh \
#       --input workbench-runs/xaver-demo/outputs \
#       --output workbench-runs/xaver-demo/visualizations
#
# Options mirror postprocess.py — run with --help for full details.
#
# Examples:
#   # Process real WRF output (requires netcdf4 + numpy):
#   ./visualization/postprocess/run.sh \
#       --input workbench-runs/xaver-demo/outputs \
#       --output workbench-runs/xaver-demo/visualizations
#
#   # Use an explicit fixture file:
#   ./visualization/postprocess/run.sh \
#       --fixture visualization/examples/demo-fixture.json \
#       --output /tmp/vis-out
#
#   # Demo mode (built-in fixture, no dependencies):
#   ./visualization/postprocess/run.sh --demo --output /tmp/vis-demo

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

exec python3 "${SCRIPT_DIR}/postprocess.py" "$@"
