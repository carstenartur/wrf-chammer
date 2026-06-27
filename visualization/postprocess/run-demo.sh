#!/bin/sh
# visualization/postprocess/run-demo.sh — Demo mode postprocessing
#
# Runs the WRF visualization postprocessor with the built-in synthetic
# fixture (no WRF run, no CDS credentials, no extra Python packages needed).
#
# Usage:
#   ./visualization/postprocess/run-demo.sh [OUTPUT_DIR]
#
# Default output directory: visualization/demo-output
# After this completes, open the viewer with:
#   ./visualization/web/serve.sh visualization/demo-output

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)

OUTPUT_DIR="${1:-${REPO_ROOT}/visualization/demo-output}"

echo "=== WRF Visualization Demo ==="
echo ""
echo "Output directory : ${OUTPUT_DIR}"
echo ""

python3 "${SCRIPT_DIR}/postprocess.py" \
    --demo \
    --output "${OUTPUT_DIR}"

echo ""
echo "Demo artifacts written to: ${OUTPUT_DIR}"
echo ""
echo "To open the browser viewer, run:"
echo "  ./visualization/web/serve.sh \"${OUTPUT_DIR}\""
echo ""
echo "Then open: http://localhost:8080"
