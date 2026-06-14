#!/bin/sh
set -eu

: "${ERA5_MANIFEST:?Set ERA5_MANIFEST to the manifest produced by download-era5.sh.}"

WPS_WORKDIR="${WPS_WORKDIR:-$PWD}"
VERIFY_UNGRIB="${VERIFY_UNGRIB:-1}"
VERIFY_METGRID="${VERIFY_METGRID:-1}"

python3 - "${ERA5_MANIFEST}" "${WPS_WORKDIR}" "${VERIFY_UNGRIB}" "${VERIFY_METGRID}" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1]).resolve()
workdir = Path(sys.argv[2]).resolve()
verify_ungrib = sys.argv[3] == "1"
verify_metgrid = sys.argv[4] == "1"

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
outputs = manifest.get("outputs", [])
if not outputs:
    raise SystemExit("Manifest contains no outputs.")

for output in outputs:
    target = Path(output["target"]).resolve()
    if not target.exists() or target.stat().st_size <= 0:
        raise SystemExit(f"Missing downloaded ERA5 file: {target}")
    if verify_ungrib:
        prefix = output.get("ungrib_prefix", "FILE")
        if not list(workdir.glob(f"{prefix}:*")):
            raise SystemExit(f"Missing ungrib output for prefix {prefix}.")

if verify_metgrid:
    met_em_files = sorted(workdir.glob("met_em.d*"))
    if not met_em_files:
        raise SystemExit("No met_em.d* files were produced.")
    subprocess.run(["ncdump", "-h", str(met_em_files[0])], check=True)

print("ERA5/WPS output verification passed")
PY
