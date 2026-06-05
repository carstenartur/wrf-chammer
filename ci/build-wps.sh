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

# Select Linux x86_64 gfortran serial option from available configure choices.
rm -f configure.wps
wps_opt=""
for opt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
  rm -f configure.wps
  if printf '%s\n' "${opt}" | ./configure > /tmp/wps_configure.log 2>&1; then
    if grep -Eiq '(^|[[:space:]])gfortran([[:space:]]|$)' configure.wps; then
      wps_opt="${opt}"
      break
    fi
  fi
done

if [ -z "${wps_opt}" ]; then
  echo "Could not determine WPS gfortran serial configure option" >&2
  cat /tmp/wps_configure.log >&2
  exit 1
fi

echo "Using WPS configure option ${wps_opt}"

./compile 2>&1 | tee /tmp/build_wps.log

for exe in geogrid/src/geogrid.exe metgrid/src/metgrid.exe ungrib/src/ungrib.exe; do
  if [ ! -x "${exe}" ]; then
    echo "Missing executable: ${exe}" >&2
    exit 1
  fi
done

echo "WPS build successful: geogrid.exe, ungrib.exe, metgrid.exe"
