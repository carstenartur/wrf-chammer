#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

: "${ERA5_MANIFEST:?Set ERA5_MANIFEST to the manifest produced by download-era5.sh.}"

WPS_WORKDIR="${WPS_WORKDIR:-$PWD}"
WPS_DIR="${WPS_DIR:-/opt/wps}"
WPS_ASSETS_DIR="${WPS_ASSETS_DIR:-/opt/wps-assets}"
WPS_VTABLE="${WPS_VTABLE:-${WPS_ASSETS_DIR}/Variable_Tables/Vtable.ERA-interim.pl}"
RUN_GEOGRID="${RUN_GEOGRID:-1}"
RUN_METGRID="${RUN_METGRID:-1}"

run_geogrid_flag=""
skip_metgrid_flag=""

if [ "${RUN_GEOGRID}" = "1" ]; then
  run_geogrid_flag="--run-geogrid"
fi

if [ "${RUN_METGRID}" != "1" ]; then
  skip_metgrid_flag="--skip-metgrid"
fi

python3 "${SCRIPT_DIR}/prepare-era5-wps.py" \
  --manifest "${ERA5_MANIFEST}" \
  --workdir "${WPS_WORKDIR}" \
  --wps-dir "${WPS_DIR}" \
  --wps-assets-dir "${WPS_ASSETS_DIR}" \
  --vtable "${WPS_VTABLE}" \
  ${run_geogrid_flag} \
  ${skip_metgrid_flag}
