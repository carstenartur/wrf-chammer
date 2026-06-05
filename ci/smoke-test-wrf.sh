#!/bin/sh
set -eu

SMOKE_DIR="${SMOKE_DIR:-/tmp/wrf-smoke-test}"
CASE_DIR="${CASE_DIR:-${WRF_CASE_DIR:-/opt/wrf/test/em_quarter_ss}}"
IDEAL_TIMEOUT="${IDEAL_TIMEOUT:-120}"
WRF_TIMEOUT="${WRF_TIMEOUT:-300}"

rm -rf "${SMOKE_DIR}"
cp -a "${CASE_DIR}" "${SMOKE_DIR}"
cd "${SMOKE_DIR}"

for exe in ideal.exe wrf.exe; do
  if [ ! -x "${SMOKE_DIR}/${exe}" ]; then
    echo "Missing executable: ${SMOKE_DIR}/${exe}" >&2
    exit 1
  fi
done

sed -i \
  -e 's/^[[:space:]]*run_minutes[[:space:]]*=.*/ run_minutes                         = 5,/' \
  -e 's/^[[:space:]]*end_minute[[:space:]]*=.*/ end_minute                          = 5,   5,   5,/' \
  -e 's/^[[:space:]]*history_interval[[:space:]]*=.*/ history_interval                    = 5,    5,    5,/' \
  namelist.input
grep -Eq '^[[:space:]]*run_minutes[[:space:]]*=[[:space:]]*5,' namelist.input
grep -Eq '^[[:space:]]*end_minute[[:space:]]*=[[:space:]]*5,[[:space:]]*5,[[:space:]]*5,' namelist.input
grep -Eq '^[[:space:]]*history_interval[[:space:]]*=[[:space:]]*5,[[:space:]]*5,[[:space:]]*5,' namelist.input

set +e
timeout "${IDEAL_TIMEOUT}" ./ideal.exe > ideal.log 2>&1
ideal_rc=$?
set -e
if [ "${ideal_rc}" -eq 124 ]; then
  echo "Smoke test failed: ideal.exe timed out after ${IDEAL_TIMEOUT}s" >&2
  exit 1
elif [ "${ideal_rc}" -ne 0 ]; then
  echo "Smoke test failed: ideal.exe exited with status ${ideal_rc}" >&2
  exit 1
fi

set +e
timeout "${WRF_TIMEOUT}" ./wrf.exe > wrf.log 2>&1
wrf_rc=$?
set -e
if [ "${wrf_rc}" -eq 124 ]; then
  echo "Smoke test failed: wrf.exe timed out after ${WRF_TIMEOUT}s" >&2
  exit 1
elif [ "${wrf_rc}" -ne 0 ]; then
  echo "Smoke test failed: wrf.exe exited with status ${wrf_rc}" >&2
  exit 1
fi

if [ ! -s wrfinput_d01 ]; then
  echo "Smoke test failed: wrfinput_d01 was not created" >&2
  exit 1
fi

wrfout_file=""
for f in wrfout_d01_*; do
  if [ -f "${f}" ]; then
    wrfout_file="${f}"
    break
  fi
done
if [ -z "${wrfout_file}" ] || [ ! -s "${wrfout_file}" ]; then
  echo "Smoke test failed: wrfout_d01 output was not created" >&2
  exit 1
fi

if [ ! -f rsl.error.0000 ] || ! grep -q "SUCCESS COMPLETE WRF" rsl.error.0000; then
  echo "Smoke test failed: WRF did not report successful completion" >&2
  exit 1
fi

echo "WRF smoke test passed: ${wrfout_file}"
