#!/bin/sh
set -eu

SMOKE_DIR="${SMOKE_DIR:-/tmp/wrf-smoke-test}"
CASE_DIR="${WRF_CASE_DIR:-/opt/wrf/test/em_quarter_ss}"
IDEAL_TIMEOUT="${IDEAL_TIMEOUT:-120}"
WRF_TIMEOUT="${WRF_TIMEOUT:-300}"

rm -rf "${SMOKE_DIR}"
cp -a "${CASE_DIR}" "${SMOKE_DIR}"
cd "${SMOKE_DIR}"

for exe in ideal.exe wrf.exe; do
  if [ ! -x "${exe}" ]; then
    echo "Missing executable: ${CASE_DIR}/${exe}" >&2
    exit 1
  fi
done

sed -i \
  -e 's/^[[:space:]]*run_minutes[[:space:]]*=.*/ run_minutes                         = 5,/' \
  -e 's/^[[:space:]]*end_minute[[:space:]]*=.*/ end_minute                          = 5,   5,   5,/' \
  -e 's/^[[:space:]]*history_interval[[:space:]]*=.*/ history_interval                    = 5,    5,    5,/' \
  namelist.input

timeout "${IDEAL_TIMEOUT}" ./ideal.exe > ideal.log 2>&1
timeout "${WRF_TIMEOUT}" ./wrf.exe > wrf.log 2>&1

if [ ! -s wrfinput_d01 ]; then
  echo "Smoke test failed: wrfinput_d01 was not created" >&2
  exit 1
fi

wrfout_file="$(find . -maxdepth 1 -type f -name 'wrfout_d01_*' | head -n 1)"
if [ -z "${wrfout_file}" ] || [ ! -s "${wrfout_file}" ]; then
  echo "Smoke test failed: wrfout_d01 output was not created" >&2
  exit 1
fi

if [ ! -f rsl.error.0000 ] || ! grep -q "SUCCESS COMPLETE WRF" rsl.error.0000; then
  echo "Smoke test failed: WRF did not report successful completion" >&2
  exit 1
fi

echo "WRF smoke test passed: ${wrfout_file}"
