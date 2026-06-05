#!/bin/sh
# Build WPS from the current directory (WPS source root).
#
# Required environment:
#   WRF_DIR   – path to a compiled WRF source tree (Makefile build)
#
# Optional environment (GRIB2 support via JasPer):
#   JASPERINC – JasPer include directory  (default: /opt/jasper/include)
#   JASPERLIB – JasPer library directory  (default: /opt/jasper/lib)
#   NETCDF    – NetCDF installation prefix (default: /usr)
#
# Note: libjasper-dev is not available in Ubuntu 22.04 repositories.
# Jasper is built from source and installed to /opt/jasper in Dockerfile.wps.
set -eu

: "${WRF_DIR:=/src/wrf}"
: "${NETCDF:=/usr}"
: "${JASPERINC:=/opt/jasper/include}"
: "${JASPERLIB:=/opt/jasper/lib}"

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
