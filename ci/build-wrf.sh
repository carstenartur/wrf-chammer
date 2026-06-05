#!/bin/sh
set -eu

./configure_new -p GNU -x -d _build -i /opt/wrf
./compile_new _build -j"$(nproc)"

test -x /opt/wrf/run/real.exe
test -x /opt/wrf/run/wrf.exe
