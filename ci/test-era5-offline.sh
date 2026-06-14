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

WPS_WORKDIR="${TMP_DIR}/wps-workdir"
WPS_DIR="${TMP_DIR}/wps-bin"
WPS_ASSETS_DIR="${TMP_DIR}/wps-assets"
FAKE_BIN_DIR="${TMP_DIR}/fake-bin"
SYNTH_MANIFEST="${TMP_DIR}/synthetic-manifest.json"

mkdir -p "${WPS_WORKDIR}" "${WPS_DIR}" "${WPS_ASSETS_DIR}/run" "${WPS_ASSETS_DIR}/Variable_Tables" "${FAKE_BIN_DIR}"

cat > "${WPS_WORKDIR}/namelist.wps" <<'NAMELIST'
&ungrib
 prefix = 'FILE',
/
&metgrid
 fg_name = 'FILE',
/
NAMELIST

cat > "${WPS_DIR}/geogrid.exe" <<'SH'
#!/bin/sh
set -eu
touch geo_em.d01.nc
SH
chmod +x "${WPS_DIR}/geogrid.exe"

cat > "${WPS_DIR}/ungrib.exe" <<'SH'
#!/bin/sh
set -eu
prefix=$(sed -n "s/^[[:space:]]*prefix[[:space:]]*=[[:space:]]*'\\([^']*\\)'.*/\\1/p" namelist.wps | head -n1)
[ -n "${prefix}" ]
[ -L GRIBFILE.AAA ]
touch "${prefix}:2026-01-01_00"
SH
chmod +x "${WPS_DIR}/ungrib.exe"

cat > "${WPS_DIR}/metgrid.exe" <<'SH'
#!/bin/sh
set -eu
touch met_em.d01.2026-01-01_00:00:00.nc
SH
chmod +x "${WPS_DIR}/metgrid.exe"

cat > "${FAKE_BIN_DIR}/ncdump" <<'SH'
#!/bin/sh
set -eu
exit 0
SH
chmod +x "${FAKE_BIN_DIR}/ncdump"

cat > "${WPS_ASSETS_DIR}/run/GEOGRID.TBL.ARW" <<'TXT'
dummy geogrid table
TXT
cat > "${WPS_ASSETS_DIR}/run/METGRID.TBL.ARW" <<'TXT'
dummy metgrid table
TXT
cat > "${WPS_ASSETS_DIR}/Variable_Tables/Vtable.ERA-interim.pl" <<'TXT'
dummy vtable
TXT

cat > "${SYNTH_MANIFEST}" <<JSON
{
  "config": "synthetic",
  "config_sha256": "synthetic",
  "outputs": [
    {
      "name": "synthetic_cached_grib",
      "dataset": "reanalysis-era5-single-levels",
      "target": "${OUTPUT_DIR}/dummy-era5.grib",
      "ungrib_prefix": "SYNTH",
      "cached": true,
      "size_bytes": 1,
      "request_sha256": "synthetic"
    }
  ]
}
JSON

python3 "${REPO_ROOT}/ci/prepare-era5-wps.py" \
  --manifest "${SYNTH_MANIFEST}" \
  --workdir "${WPS_WORKDIR}" \
  --wps-dir "${WPS_DIR}" \
  --wps-assets-dir "${WPS_ASSETS_DIR}" \
  --vtable "${WPS_ASSETS_DIR}/Variable_Tables/Vtable.ERA-interim.pl" \
  --run-geogrid

for required in \
  "${WPS_WORKDIR}/geo_em.d01.nc" \
  "${WPS_WORKDIR}/SYNTH:2026-01-01_00" \
  "${WPS_WORKDIR}/met_em.d01.2026-01-01_00:00:00.nc" \
  "${WPS_WORKDIR}/GEOGRID.TBL" \
  "${WPS_WORKDIR}/METGRID.TBL"; do
  [ -f "${required}" ] || {
    echo "Missing expected prepare-era5-wps output: ${required}" >&2
    exit 1
  }
done

PATH="${FAKE_BIN_DIR}:${PATH}" \
ERA5_MANIFEST="${SYNTH_MANIFEST}" \
WPS_WORKDIR="${WPS_WORKDIR}" \
VERIFY_UNGRIB=1 \
VERIFY_METGRID=1 \
sh "${REPO_ROOT}/ci/verify-era5-outputs.sh"

echo "ERA5 offline dry-run checks passed."
