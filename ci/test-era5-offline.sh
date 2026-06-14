#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

OUTPUT_DIR="${TMP_DIR}/cache"
MANIFEST_PATH="${TMP_DIR}/era5-manifest.json"
mkdir -p "${OUTPUT_DIR}"

cp "${REPO_ROOT}/ci/era5/dummy-era5.grib" "${OUTPUT_DIR}/dummy-era5.grib"
unset CDSAPI_KEY CDSAPI_URL || true

python3 "${REPO_ROOT}/ci/download-era5.py" \
  --config "${REPO_ROOT}/ci/era5/era5-offline-test-config.json" \
  --output-dir "${OUTPUT_DIR}" \
  --manifest "${MANIFEST_PATH}"

python3 - "${MANIFEST_PATH}" "${OUTPUT_DIR}/dummy-era5.grib" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1]).resolve()
target_path = Path(sys.argv[2]).resolve()

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
outputs = manifest.get("outputs", [])
if len(outputs) != 1:
    raise SystemExit("Expected exactly one output in offline manifest.")

entry = outputs[0]
if entry.get("target") != str(target_path):
    raise SystemExit("Manifest target path mismatch.")
if entry.get("cached") is not True:
    raise SystemExit("Expected cached=true for pre-seeded dummy GRIB.")
if entry.get("size_bytes", 0) <= 0:
    raise SystemExit("Expected size_bytes > 0 for dummy GRIB.")
PY

cat > "${TMP_DIR}/invalid-config.json" <<'JSON'
{
  "requests": []
}
JSON

if python3 "${REPO_ROOT}/ci/download-era5.py" --config "${TMP_DIR}/invalid-config.json" --output-dir "${OUTPUT_DIR}" >/dev/null 2>&1; then
  echo "Expected invalid config validation to fail, but it succeeded." >&2
  exit 1
fi

echo "ERA5 offline dry-run checks passed."
