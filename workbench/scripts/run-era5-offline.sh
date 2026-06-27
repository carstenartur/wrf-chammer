#!/bin/sh
# workbench/scripts/run-era5-offline.sh — ERA5 offline-validation mode
#
# Runs the ERA5 offline validation suite (ci/test-era5-offline.sh) through the
# Workbench.  This verifies the download script, manifest generation, WPS
# preparation, and output verification using pre-seeded dummy data.
# No CDS credentials or real ERA5 download are required.
#
# Arguments:
#   $1  path to the validated job config JSON file
#   $2  path to the run directory (already created by run.sh)

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)

CONFIG_FILE="$1"
RUN_DIR="$2"

echo "=== WRF Workbench — ERA5 Offline Validation ==="
echo ""
echo "Running ERA5 offline checks (ci/test-era5-offline.sh) ..."
echo ""

sh "${REPO_ROOT}/ci/test-era5-offline.sh"

echo ""
echo "ERA5 offline validation passed."
echo "Note: This mode uses pre-seeded dummy GRIB data (ci/era5/dummy-era5.grib)."
echo "      No real ERA5 data was downloaded."
