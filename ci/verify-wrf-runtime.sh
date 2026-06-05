#!/bin/sh
set -eu

cd /opt/wrf/run

for exe in real.exe wrf.exe; do
  if [ ! -x "${exe}" ]; then
    echo "Missing executable: /opt/wrf/run/${exe}" >&2
    exit 1
  fi

  ldd "${exe}" > "/tmp/${exe}.ldd"
  cat "/tmp/${exe}.ldd"

  if grep -q "not found" "/tmp/${exe}.ldd"; then
    echo "Missing runtime dependency for ${exe}" >&2
    exit 1
  fi

  set +e
  timeout 10 "./${exe}" > "/tmp/${exe}.startup.log" 2>&1
  rc=$?
  set -e

  if [ "${rc}" -eq 126 ] || [ "${rc}" -eq 127 ]; then
    echo "${exe} failed to start (exit ${rc})" >&2
    cat "/tmp/${exe}.startup.log" >&2
    exit 1
  fi

done
