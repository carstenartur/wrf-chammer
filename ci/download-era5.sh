#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

: "${ERA5_CONFIG:?Set ERA5_CONFIG to a JSON config file.}"

ERA5_OUTPUT_DIR="${ERA5_OUTPUT_DIR:-$PWD/.era5-cache}"
ERA5_MANIFEST="${ERA5_MANIFEST:-$ERA5_OUTPUT_DIR/era5-manifest.json}"

mkdir -p "${ERA5_OUTPUT_DIR}"

if [ -n "${CDSAPI_KEY:-}" ]; then
  mkdir -p "${HOME}"
  cat > "${HOME}/.cdsapirc" <<EOF
url: ${CDSAPI_URL:-https://cds.climate.copernicus.eu/api}
key: ${CDSAPI_KEY}
EOF
fi

python3 "${SCRIPT_DIR}/download-era5.py" \
  --config "${ERA5_CONFIG}" \
  --output-dir "${ERA5_OUTPUT_DIR}" \
  --manifest "${ERA5_MANIFEST}"
