#!/bin/sh
set -eu

./configure_new -p GNU -x -d _build -i /opt/wrf
./compile_new _build -j"$(nproc)"

if [ ! -x /opt/wrf/run/real.exe ]; then
  echo "Missing executable: /opt/wrf/run/real.exe" >&2
  exit 1
fi

if [ ! -x /opt/wrf/run/wrf.exe ]; then
  echo "Missing executable: /opt/wrf/run/wrf.exe" >&2
  exit 1
fi
