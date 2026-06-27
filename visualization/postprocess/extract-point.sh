#!/bin/sh
# visualization/postprocess/extract-point.sh — Point timeseries extractor
#
# Extracts a timeseries of weather variables at a given lat/lon from WRF
# output or a fixture file.
#
# Usage:
#   ./visualization/postprocess/extract-point.sh \
#       --input workbench-runs/xaver-demo/outputs \
#       --lat 54.0 \
#       --lon 9.0
#
#   # With fixture (no extra Python packages needed):
#   ./visualization/postprocess/extract-point.sh \
#       --fixture visualization/examples/demo-fixture.json \
#       --lat 54.0 --lon 9.0 --format csv
#
#   # Demo mode:
#   ./visualization/postprocess/extract-point.sh \
#       --demo --lat 54.0 --lon 9.0

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

exec python3 "${SCRIPT_DIR}/extract_point.py" "$@"
