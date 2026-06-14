#!/bin/sh
# ERA5/WPS integration test using the bundled mini GRIB dataset.
#
# Proves that the full pipeline
#   GRIB → ungrib.exe → intermediate files → metgrid.exe → met_em.d01.*
# works correctly without CDS credentials, internet access, or large datasets.
#
# Prerequisites:
#   The era5-pipeline Docker image must be available locally.
#   Build it (with the wps-reproducible base) before running this script:
#
#     docker build -f Dockerfile.wps   -t wps-reproducible .
#     docker build -f Dockerfile.era5  --build-arg WPS_IMAGE=wps-reproducible \
#                                      -t era5-pipeline .
#
# Environment variables (all optional):
#   ERA5_IMAGE        Docker image to run WPS inside (default: era5-pipeline)
#   WORKDIR           Persistent work directory; preserved on failure for
#                     log inspection (default: /tmp/wps-integration-workdir)
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
TEST_DATA="${REPO_ROOT}/tests/era5-mini"
ERA5_IMAGE="${ERA5_IMAGE:-era5-pipeline}"
WORKDIR="${WORKDIR:-/tmp/wps-integration-workdir}"

# ── Prepare work directory ───────────────────────────────────────────────────
rm -rf "${WORKDIR}"
mkdir -p "${WORKDIR}"

# Copy test GRIB files and namelist into the work directory.
cp "${TEST_DATA}/pressure.grib"   "${WORKDIR}/pressure.grib"
cp "${TEST_DATA}/surface.grib"    "${WORKDIR}/surface.grib"
cp "${TEST_DATA}/wps/namelist.wps" "${WORKDIR}/namelist.wps"

# ── Build manifest ───────────────────────────────────────────────────────────
# The manifest uses /work/* paths (inside the container) because both the
# prepare step and the verify step run inside the container with ${WORKDIR}
# bind-mounted at /work.
PRESSURE_SIZE=$(python3 -c "import os; print(os.path.getsize('${WORKDIR}/pressure.grib'))")
SURFACE_SIZE=$(python3 -c "import os; print(os.path.getsize('${WORKDIR}/surface.grib'))")

cat > "${WORKDIR}/manifest.json" <<JSON
{
  "config": "era5-mini",
  "config_sha256": "mini",
  "outputs": [
    {
      "name": "mini_pressure",
      "dataset": "reanalysis-era5-pressure-levels",
      "target": "/work/pressure.grib",
      "ungrib_prefix": "PLEV",
      "cached": true,
      "size_bytes": ${PRESSURE_SIZE},
      "request_sha256": "mini"
    },
    {
      "name": "mini_surface",
      "dataset": "reanalysis-era5-single-levels",
      "target": "/work/surface.grib",
      "ungrib_prefix": "SFC",
      "cached": true,
      "size_bytes": ${SURFACE_SIZE},
      "request_sha256": "mini"
    }
  ]
}
JSON

# ── Run ungrib.exe + metgrid.exe ─────────────────────────────────────────────
echo "Running ungrib.exe and metgrid.exe inside ${ERA5_IMAGE} ..."
docker run --rm \
  --network=none \
  -v "${WORKDIR}:/work" \
  "${ERA5_IMAGE}" \
  python3 /usr/local/bin/prepare-era5-wps.py \
    --manifest      /work/manifest.json \
    --workdir       /work \
    --wps-dir       /opt/wps \
    --wps-assets-dir /opt/wps-assets \
    --vtable        /opt/wps-assets/Variable_Tables/Vtable.ERA-interim.pl

# ── Verify ungrib and metgrid outputs ────────────────────────────────────────
echo "Verifying ungrib and metgrid outputs ..."
docker run --rm \
  --network=none \
  -v "${WORKDIR}:/work" \
  -e ERA5_MANIFEST=/work/manifest.json \
  -e WPS_WORKDIR=/work \
  -e VERIFY_UNGRIB=1 \
  -e VERIFY_METGRID=1 \
  "${ERA5_IMAGE}" \
  sh /usr/local/bin/verify-era5-outputs.sh

# ── Check required meteorological variables ──────────────────────────────────
# Write a small Python helper into the work directory so it can be executed
# inside the container via the bind mount.
cat > "${WORKDIR}/check-vars.py" <<'PY'
"""Assert that core WPS fields are present in the met_em NetCDF output."""
import re
import subprocess
import sys
from pathlib import Path

met_em_files = sorted(Path("/work").glob("met_em.d01.*"))
if not met_em_files:
    raise SystemExit("No met_em.d01.* files found in /work.")

header = subprocess.run(
    ["ncdump", "-h", str(met_em_files[0])],
    capture_output=True,
    text=True,
    check=True,
).stdout

# Extract variable names from lines like:
#   float TT(Time, num_metgrid_levels, south_north, west_east) ;
declared = set(re.findall(r"^\s+\S+\s+(\w+)\s*\(", header, re.MULTILINE))

required = ("TT", "UU", "VV", "GHT", "PSFC")
missing = [v for v in required if v not in declared]
if missing:
    print("Variables found in met_em:", sorted(declared), file=sys.stderr)
    raise SystemExit(f"Required variables missing from met_em: {missing}")

for v in required:
    print(f"  {v}: found")
print(f"Variable check passed ({met_em_files[0].name}).")
PY

echo "Checking required variables in met_em output ..."
docker run --rm \
  --network=none \
  -v "${WORKDIR}:/work" \
  "${ERA5_IMAGE}" \
  python3 /work/check-vars.py

echo "ERA5/WPS integration test passed."
