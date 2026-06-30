#!/bin/sh
# workbench/scripts/run-era5-wrf.sh — ERA5 -> WPS -> WRF pipeline mode
#
# Default path is cacheable/offline and suitable for CI. Set
# WORKBENCH_ERA5_WRF_REAL=1 for a manual real WPS/WRF run with local WPS_DIR and
# WRF_DIR binaries.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)

CONFIG_FILE="$1"
RUN_DIR="$2"

NAMELIST_DIR="${RUN_DIR}/namelists"
ERA5_OUTPUT_DIR="${RUN_DIR}/outputs/era5"
WPS_OUTPUT_DIR="${RUN_DIR}/outputs/wps"
WRF_OUTPUT_DIR="${RUN_DIR}/outputs/wrf"
VIS_DIR="${RUN_DIR}/visualizations"
METADATA_PATH="${RUN_DIR}/outputs/pipeline-metadata.json"

mkdir -p "${NAMELIST_DIR}" "${ERA5_OUTPUT_DIR}" "${WPS_OUTPUT_DIR}" "${WRF_OUTPUT_DIR}" "${VIS_DIR}"

printf '%s\n\n' '=== WRF Workbench — ERA5 to WRF Pipeline ==='

ERA5_CONFIG_REL=$(python3 -c "import json,sys; cfg=json.load(open(sys.argv[1])); print(cfg.get('inputs',{}).get('era5',{}).get('config',''))" "${CONFIG_FILE}")
ERA5_CONFIG="${REPO_ROOT}/${ERA5_CONFIG_REL}"
if [ ! -f "${ERA5_CONFIG}" ]; then
    echo "Error: ERA5 config file not found: ${ERA5_CONFIG}" >&2
    exit 1
fi

ALLOW_DOWNLOAD="${WORKBENCH_ERA5_WRF_ALLOW_DOWNLOAD:-0}"
if [ "${ALLOW_DOWNLOAD}" != "1" ]; then
    python3 - "${ERA5_CONFIG}" "${ERA5_OUTPUT_DIR}" <<'PY'
import json
import sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
out = Path(sys.argv[2])
missing = []
for name, request in cfg.get('requests', {}).items():
    target = request.get('target') or f'{name}.grib'
    if not (out / target).is_file():
        missing.append(str(out / target))
if missing:
    raise SystemExit('Cached ERA5 input is missing. Pre-seed files or set WORKBENCH_ERA5_WRF_ALLOW_DOWNLOAD=1:\n  ' + '\n  '.join(missing))
PY
fi

ERA5_MANIFEST="${RUN_DIR}/outputs/era5-manifest.json"
export ERA5_CONFIG ERA5_OUTPUT_DIR ERA5_MANIFEST
sh "${REPO_ROOT}/ci/download-era5.sh"

python3 - "${CONFIG_FILE}" "${NAMELIST_DIR}" <<'PY'
import json
import sys
from pathlib import Path
from datetime import datetime
cfg = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
period = cfg['period']
domain = cfg['domain']

def wrf_time(value):
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%d_%H:%M:%S')

def parts(value):
    dt = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    return dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second

start = wrf_time(period['start'])
end = wrf_time(period['end'])
sy, sm, sd, sh, smin, ss = parts(period['start'])
ey, em, ed, eh, emin, es = parts(period['end'])
dx = int(float(domain['dx_km']) * 1000)
dy = int(float(domain['dy_km']) * 1000)
e_we = int(domain['e_we'])
e_sn = int(domain['e_sn'])
cen_lat = float(domain['center_lat'])
cen_lon = float(domain['center_lon'])
truelat1 = max(min(cen_lat - 5.0, 89.0), -89.0)
truelat2 = max(min(cen_lat + 5.0, 89.0), -89.0)

(out / 'namelist.wps').write_text(f"""&share
 wrf_core = 'ARW',
 max_dom = 1,
 start_date = '{start}',
 end_date   = '{end}',
 interval_seconds = 21600,
 io_form_geogrid = 2,
/

&geogrid
 parent_id         =   1,
 parent_grid_ratio =   1,
 i_parent_start    =   1,
 j_parent_start    =   1,
 e_we              =   {e_we},
 e_sn              =   {e_sn},
 geog_data_res     =   'default',
 dx = {dx},
 dy = {dy},
 map_proj = 'lambert',
 ref_lat   = {cen_lat:.4f},
 ref_lon   = {cen_lon:.4f},
 truelat1  = {truelat1:.4f},
 truelat2  = {truelat2:.4f},
 stand_lon = {cen_lon:.4f},
 geog_data_path = 'geog',
/

&ungrib
 out_format = 'WPS',
 prefix = 'FILE',
/

&metgrid
 fg_name = 'FILE',
 io_form_metgrid = 2,
/
""", encoding='utf-8')

(out / 'namelist.input').write_text(f"""&time_control
 run_days                            = 0,
 run_hours                           = 6,
 start_year                          = {sy},
 start_month                         = {sm},
 start_day                           = {sd},
 start_hour                          = {sh},
 start_minute                        = {smin},
 start_second                        = {ss},
 end_year                            = {ey},
 end_month                           = {em},
 end_day                             = {ed},
 end_hour                            = {eh},
 end_minute                          = {emin},
 end_second                          = {es},
 interval_seconds                    = 21600,
 input_from_file                     = .true.,
 history_interval                    = 60,
 frames_per_outfile                  = 1,
 restart                             = .false.,
 io_form_history                     = 2,
 io_form_input                       = 2,
 io_form_boundary                    = 2,
/

&domains
 time_step                           = 54,
 max_dom                             = 1,
 e_we                                = {e_we},
 e_sn                                = {e_sn},
 e_vert                              = 35,
 dx                                  = {dx},
 dy                                  = {dy},
 grid_id                             = 1,
 parent_id                           = 0,
 i_parent_start                      = 1,
 j_parent_start                      = 1,
 parent_grid_ratio                   = 1,
 parent_time_step_ratio              = 1,
 feedback                            = 0,
 smooth_option                       = 0,
/

&physics
 mp_physics                          = 3,
 ra_lw_physics                       = 1,
 ra_sw_physics                       = 1,
 bl_pbl_physics                      = 1,
 cu_physics                          = 1,
/

&dynamics
 hybrid_opt                          = 2,
 w_damping                           = 0,
 diff_opt                            = 1,
 km_opt                              = 4,
/

&bdy_control
 spec_bdy_width                      = 5,
 specified                           = .true.,
/

&namelist_quilt
 nio_tasks_per_group                 = 0,
 nio_groups                          = 1,
/
""", encoding='utf-8')

print('Generated namelists:')
print('  ' + str(out / 'namelist.wps'))
print('  ' + str(out / 'namelist.input'))
PY

cp "${NAMELIST_DIR}/namelist.wps" "${WPS_OUTPUT_DIR}/namelist.wps"
cp "${NAMELIST_DIR}/namelist.input" "${WRF_OUTPUT_DIR}/namelist.input"

REAL_RUN="${WORKBENCH_ERA5_WRF_REAL:-0}"
if [ "${REAL_RUN}" = "1" ]; then
    : "${WPS_DIR:?Set WPS_DIR to a directory containing geogrid.exe, ungrib.exe and metgrid.exe}"
    : "${WRF_DIR:?Set WRF_DIR to a directory containing real.exe and wrf.exe}"
    for exe in geogrid.exe ungrib.exe metgrid.exe; do [ -x "${WPS_DIR}/${exe}" ] || { echo "Missing ${WPS_DIR}/${exe}" >&2; exit 1; }; done
    for exe in real.exe wrf.exe; do [ -x "${WRF_DIR}/${exe}" ] || { echo "Missing ${WRF_DIR}/${exe}" >&2; exit 1; }; done
    WPS_WORK="${RUN_DIR}/work/wps"
    WRF_WORK="${RUN_DIR}/work/wrf"
    mkdir -p "${WPS_WORK}" "${WRF_WORK}"
    cp "${NAMELIST_DIR}/namelist.wps" "${WPS_WORK}/namelist.wps"
    cp "${NAMELIST_DIR}/namelist.input" "${WRF_WORK}/namelist.input"
    ln -sf "${ERA5_OUTPUT_DIR}"/*.grib "${WPS_WORK}/" 2>/dev/null || true
    (cd "${WPS_WORK}" && "${WPS_DIR}/geogrid.exe" && "${WPS_DIR}/ungrib.exe" && "${WPS_DIR}/metgrid.exe")
    ln -sf "${WPS_WORK}"/met_em* "${WRF_WORK}/" 2>/dev/null || true
    (cd "${WRF_WORK}" && "${WRF_DIR}/real.exe" && "${WRF_DIR}/wrf.exe")
    cp "${WRF_WORK}"/wrfout_d01_* "${WRF_OUTPUT_DIR}/"
    python3 "${REPO_ROOT}/visualization/postprocess/postprocess.py" --input "${WRF_OUTPUT_DIR}" --output "${VIS_DIR}"
else
    FIXTURE="${REPO_ROOT}/visualization/examples/demo-fixture.json"
    cp "${FIXTURE}" "${WRF_OUTPUT_DIR}/cached-wrf-visualization-fixture.json"
    python3 "${REPO_ROOT}/visualization/postprocess/postprocess.py" --fixture "${FIXTURE}" --output "${VIS_DIR}"
fi

python3 - "${CONFIG_FILE}" "${RUN_DIR}" "${METADATA_PATH}" "${REAL_RUN}" <<'PY'
import json
import sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
run_dir = Path(sys.argv[2])
meta = {
  'job_id': cfg['id'],
  'mode': cfg['mode'],
  'real_wps_wrf_executed': sys.argv[4] == '1',
  'namelist_wps': 'namelists/namelist.wps',
  'namelist_input': 'namelists/namelist.input',
  'era5_manifest': 'outputs/era5-manifest.json',
  'wrf_outputs': [str(p.relative_to(run_dir)) for p in sorted((run_dir / 'outputs' / 'wrf').glob('*'))],
  'visualization_metadata': 'visualizations/metadata.json',
}
Path(sys.argv[3]).write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
print('Pipeline metadata: ' + sys.argv[3])
PY

echo "ERA5 -> WRF pipeline mode completed."
