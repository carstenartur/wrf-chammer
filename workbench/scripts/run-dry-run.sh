#!/bin/sh
# workbench/scripts/run-dry-run.sh — Dry-run mode
#
# Validates the configuration and prints the planned pipeline steps without
# executing any Docker containers or downloading any data.
#
# Arguments:
#   $1  path to the validated job config JSON file
#   $2  path to the run directory (already created by run.sh)

set -eu

CONFIG_FILE="$1"
RUN_DIR="$2"

echo "=== WRF Workbench — Dry Run ==="
echo ""

python3 - "${CONFIG_FILE}" <<'PY'
import json, sys
from pathlib import Path

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
period  = config["period"]
domain  = config["domain"]
inputs  = config["inputs"]
outputs = config["outputs"]

print("Job configuration:")
print(f"  id          : {config['id']}")
print(f"  name        : {config['name']}")
print(f"  mode        : {config['mode']}")
print(f"  period      : {period['start']}  →  {period['end']}")
print(f"  domain      : {domain['label']}")
print(f"               center ({domain['center_lat']}°N, {domain['center_lon']}°E)")
print(f"               grid   {domain['e_we']} × {domain['e_sn']} pts"
      f"   dx={domain['dx_km']} km   dy={domain['dy_km']} km")
print(f"  input source: {inputs['source']}")
print(f"  output dir  : {outputs['directory']}")
print()

source = inputs["source"]
steps = [
    "[1] Validate configuration                  → DONE",
    "[2] Create run directory and write metadata → DONE",
]
if source == "era5":
    steps += [
        "[3] Download ERA5 GRIB data (CDS API)       → SKIPPED (dry-run)",
        "[4] Run WPS geogrid / ungrib / metgrid      → SKIPPED (dry-run)",
        "[5] Run WRF real.exe + wrf.exe              → SKIPPED (dry-run)",
        "[6] Post-process wrfout_d* outputs          → SKIPPED (dry-run)",
    ]
else:
    steps += [
        "[3] Prepare idealized WRF initial conditions → SKIPPED (dry-run)",
        "[4] Run WRF ideal.exe + wrf.exe              → SKIPPED (dry-run)",
        "[5] Post-process wrfout_d* outputs           → SKIPPED (dry-run)",
    ]

print("Planned pipeline steps:")
for s in steps:
    print(f"  {s}")
PY

echo ""
echo "Docker images that would be used (not pulled in dry-run):"
echo "  wrf-reproducible:latest    — WRF simulation (Dockerfile)"
echo "  wps-reproducible:latest    — WPS preprocessing (Dockerfile.wps)"
echo "  era5-pipeline:latest       — ERA5 data acquisition (Dockerfile.era5)"
echo ""
echo "Run directory layout:"
echo "  ${RUN_DIR}/"
echo "    job.json      — job metadata"
echo "    status.json   — job status"
echo "    logs/         — execution logs"
echo "    outputs/      — simulation outputs (empty in dry-run)"
echo ""
echo "Dry run complete.  No containers were started and no data was downloaded."
