#!/bin/sh
# Verify that the three WPS executables are present, fully linked, and can start.
#
# Optional environment:
#   WPS_DIR – directory containing the WPS executables (default: /opt/wps)
set -eu

STARTUP_TIMEOUT=10
WPS_DIR="${WPS_DIR:-/opt/wps}"

cd "${WPS_DIR}"

for exe in geogrid.exe ungrib.exe metgrid.exe; do
  if [ ! -x "${exe}" ]; then
    echo "Missing executable: ${WPS_DIR}/${exe}" >&2
    exit 1
  fi

  ldd "${exe}" > "/tmp/${exe}.ldd"
  cat "/tmp/${exe}.ldd"

  if grep -q "not found" "/tmp/${exe}.ldd"; then
    echo "Missing runtime dependency for ${exe}" >&2
    exit 1
  fi

  set +e
  timeout "${STARTUP_TIMEOUT}" "./${exe}" > "/tmp/${exe}.startup.log" 2>&1
  rc=$?
  set -e

  if [ "${rc}" -eq 126 ] || [ "${rc}" -eq 127 ]; then
    echo "${exe} failed to start (exit ${rc})" >&2
    cat "/tmp/${exe}.startup.log" >&2
    exit 1
  fi

done

echo "WPS runtime verification passed: geogrid.exe, ungrib.exe, metgrid.exe"
