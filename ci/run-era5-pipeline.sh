#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

: "${ERA5_CONFIG:?Set ERA5_CONFIG to a JSON config file.}"

ERA5_OUTPUT_DIR="${ERA5_OUTPUT_DIR:-$PWD/.era5-cache}"
ERA5_MANIFEST="${ERA5_MANIFEST:-$ERA5_OUTPUT_DIR/era5-manifest.json}"
RUN_PREPROCESS="${RUN_PREPROCESS:-1}"
RUN_VERIFY="${RUN_VERIFY:-1}"

export ERA5_OUTPUT_DIR ERA5_MANIFEST

"${SCRIPT_DIR}/download-era5.sh"

if [ "${RUN_PREPROCESS}" = "1" ]; then
  "${SCRIPT_DIR}/prepare-era5-wps.sh"
fi

if [ "${RUN_VERIFY}" = "1" ]; then
  "${SCRIPT_DIR}/verify-era5-outputs.sh"
fi
