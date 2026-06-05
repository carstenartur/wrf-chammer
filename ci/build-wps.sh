#!/bin/sh
# Build WPS from the current directory (WPS source root).
#
# Required environment:
#   WRF_DIR   – path to a compiled WRF source tree (Makefile build)
#
# Optional environment (GRIB2 support via JasPer):
#   JASPERINC – JasPer include directory  (default: /usr/include)
#   JASPERLIB – JasPer library directory  (default: /usr/lib/x86_64-linux-gnu)
#   NETCDF    – NetCDF installation prefix (default: /usr)
set -eu

: "${WRF_DIR:=/src/wrf}"
: "${NETCDF:=/usr}"
: "${JASPERINC:=/usr/include}"
: "${JASPERLIB:=/usr/lib/x86_64-linux-gnu}"

export WRF_DIR NETCDF JASPERINC JASPERLIB

# Select option 1: Linux x86_64 gfortran serial (with GRIB2 when JasPer is present)
printf '1\n' | ./configure

./compile 2>&1 | tee /tmp/build_wps.log

for exe in geogrid/src/geogrid.exe metgrid/src/metgrid.exe ungrib/src/ungrib.exe; do
  if [ ! -x "${exe}" ]; then
    echo "Missing executable: ${exe}" >&2
    exit 1
  fi
done

echo "WPS build successful: geogrid.exe, ungrib.exe, metgrid.exe"
