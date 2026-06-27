#!/bin/sh
# visualization/web/serve.sh — Serve the WRF visualization viewer
#
# Starts a simple HTTP server so the browser can load metadata.json and
# layer files (fetch() requires HTTP, not file://).
#
# Usage:
#   ./visualization/web/serve.sh [DATA_DIR] [PORT]
#
# Arguments:
#   DATA_DIR   Directory containing metadata.json and layers/.
#              Defaults to visualization/demo-output
#   PORT       HTTP port (default: 8080)
#
# The server serves both the viewer HTML and the data from DATA_DIR.
# Steps:
#   1. Run the demo postprocessor: ./visualization/postprocess/run-demo.sh
#   2. Serve: ./visualization/web/serve.sh
#   3. Open: http://localhost:8080

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)

DATA_DIR="${1:-${REPO_ROOT}/visualization/demo-output}"
PORT="${2:-8080}"

if [ ! -f "${DATA_DIR}/metadata.json" ]; then
    echo "ERROR: metadata.json not found in ${DATA_DIR}" >&2
    echo ""
    echo "Run the postprocessor first:" >&2
    echo "  ./visualization/postprocess/run-demo.sh" >&2
    exit 1
fi

SERVE_DIR=$(mktemp -d)
trap 'rm -rf "${SERVE_DIR}"' EXIT

# Symlink viewer HTML and data into the serve directory
ln -sf "${SCRIPT_DIR}/index.html" "${SERVE_DIR}/index.html"
# Symlink all data files (metadata.json, layers/)
for item in "${DATA_DIR}"/*; do
    ln -sf "${item}" "${SERVE_DIR}/$(basename "${item}")"
done

echo "=== WRF Weather Viewer ==="
echo ""
echo "Data directory : ${DATA_DIR}"
echo "Serving at     : http://localhost:${PORT}"
echo ""
echo "Press Ctrl-C to stop."
echo ""

cd "${SERVE_DIR}"
python3 -m http.server "${PORT}"
