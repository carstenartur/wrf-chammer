#!/bin/sh
set -eu

NETCDFF_LIBRARY=${NETCDFF_LIBRARY:-$(sh ./ci/find-netcdff-library.sh)}

./configure_new -p GNU -x -d _build -i /opt/wrf -- \
    "-DnetCDF-Fortran_LIBRARY=${NETCDFF_LIBRARY}"
./compile_new _build -j"$(nproc)"

if [ ! -x /opt/wrf/run/real.exe ]; then
  echo "Missing executable: /opt/wrf/run/real.exe" >&2
  exit 1
fi

if [ ! -x /opt/wrf/run/wrf.exe ]; then
  echo "Missing executable: /opt/wrf/run/wrf.exe" >&2
  exit 1
fi
