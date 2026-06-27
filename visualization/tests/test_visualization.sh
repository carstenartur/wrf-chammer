#!/bin/sh
# visualization/tests/test_visualization.sh — Visualization MVP test suite
#
# Tests:
#   1. Fixture JSON loads and has required structure
#   2. Postprocessor runs in demo mode (no extra dependencies)
#   3. metadata.json is produced with required fields
#   4. At least one time-varying layer is produced
#   5. max_wind10m layer is produced (maximum-value product)
#   6. Layer JSON has correct structure (frames, times, vmin/vmax)
#   7. Point timeseries extraction works (JSON output)
#   8. Point timeseries extraction works (CSV output)
#   9. Web viewer HTML file exists and contains key elements
#   10. Layer count and IDs match metadata catalogue
#
# Requires: Python 3 (stdlib only)
# Does NOT require: numpy, netCDF4, scipy, Docker, CDS credentials

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)

VIS_DIR="${REPO_ROOT}/visualization"
POSTPROCESS="${VIS_DIR}/postprocess/postprocess.py"
EXTRACT_PT="${VIS_DIR}/postprocess/extract_point.py"
FIXTURE="${VIS_DIR}/examples/demo-fixture.json"
WEB_HTML="${VIS_DIR}/web/index.html"

PASS=0
FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "${TMP}"' EXIT

pass() { echo "[PASS] $*"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $*" >&2; FAIL=$((FAIL + 1)); }

echo "=== Visualization MVP Tests ==="
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. Fixture JSON structure
# ─────────────────────────────────────────────────────────────────────────────
echo "-- Test 1: Fixture JSON structure --"

python3 - "${FIXTURE}" <<'PY'
import json, sys
from pathlib import Path

fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

assert fixture.get("format") == "wrf-vis-fixture", "format field missing or wrong"
assert fixture.get("version"), "version field missing"
assert "domain" in fixture, "domain missing"
assert "times" in fixture and len(fixture["times"]) >= 2, "times must have >= 2 entries"
assert "variables" in fixture, "variables missing"

vars_required = ["U10", "V10", "T2", "XLAT", "XLONG"]
for v in vars_required:
    assert v in fixture["variables"], f"Variable {v!r} missing from fixture"

# Check shape consistency
times = fixture["times"]
u10 = fixture["variables"]["U10"]["data"]
assert len(u10) == len(times), f"U10 time dim {len(u10)} != times {len(times)}"

print(f"Fixture OK: {len(times)} time steps, {len(fixture['variables'])} variables")
PY
pass "Fixture JSON has required structure"

# ─────────────────────────────────────────────────────────────────────────────
# 2. Postprocessor demo mode
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 2: Postprocessor demo mode --"

OUT="${TMP}/vis-out"
if python3 "${POSTPROCESS}" --demo --output "${OUT}" >/dev/null 2>&1; then
    pass "Postprocessor runs successfully in demo mode"
else
    fail "Postprocessor failed in demo mode"
    # Print stderr for diagnosis
    python3 "${POSTPROCESS}" --demo --output "${OUT}" 2>&1 | head -20 >&2
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. metadata.json is produced
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 3: metadata.json produced --"

if [ -f "${OUT}/metadata.json" ]; then
    pass "metadata.json exists"
else
    fail "metadata.json not found in ${OUT}"
fi

python3 - "${OUT}/metadata.json" <<'PY' && pass "metadata.json has required fields" || fail "metadata.json missing required fields"
import json, sys
from pathlib import Path

meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = ("jobId", "domain", "times", "layers")
for f in required:
    assert f in meta, f"metadata.json missing field: {f!r}"

assert "bounds" in meta["domain"], "domain.bounds missing"
assert "nx" in meta["domain"], "domain.nx missing"
assert isinstance(meta["times"], list) and len(meta["times"]) >= 1, \
    "times must be a non-empty list"
assert isinstance(meta["layers"], list) and len(meta["layers"]) >= 1, \
    "layers must be a non-empty list"

# Each layer entry must have required fields
for layer in meta["layers"]:
    for field in ("id", "label", "unit", "type", "file"):
        assert field in layer, f"Layer entry missing field: {field!r} in {layer}"

print(f"metadata.json OK: {len(meta['times'])} times, {len(meta['layers'])} layers")
PY

# ─────────────────────────────────────────────────────────────────────────────
# 4. At least one time-varying layer
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 4: Time-varying layer --"

python3 - "${OUT}/metadata.json" "${OUT}" <<'PY' && pass "Time-varying layer exists and is valid" || fail "Time-varying layer check failed"
import json, sys
from pathlib import Path

meta_path = Path(sys.argv[1])
out_dir   = Path(sys.argv[2])
meta = json.loads(meta_path.read_text(encoding="utf-8"))

ts_layers = [l for l in meta["layers"] if l["type"] == "raster-time-series"]
assert ts_layers, "No time-series layers found in metadata"

# Check the first one
entry = ts_layers[0]
layer_file = out_dir / entry["file"]
assert layer_file.is_file(), f"Layer file not found: {layer_file}"

layer = json.loads(layer_file.read_text(encoding="utf-8"))
assert "frames" in layer, "Layer missing 'frames'"
assert "times"  in layer, "Layer missing 'times'"
assert len(layer["frames"]) == len(layer["times"]), \
    f"frames count {len(layer['frames'])} != times count {len(layer['times'])}"
assert len(layer["frames"]) >= 1, "frames is empty"

ny = len(layer["frames"][0])
nx = len(layer["frames"][0][0])
assert ny > 0 and nx > 0, "Frame grid is empty"

print(f"Layer '{entry['id']}': {len(layer['times'])} time steps, {ny}x{nx} grid")
PY

# ─────────────────────────────────────────────────────────────────────────────
# 5. Maximum-value product (max_wind10m)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 5: Maximum-value product --"

python3 - "${OUT}/metadata.json" "${OUT}" <<'PY' && pass "max_wind10m layer exists and is valid" || fail "max_wind10m layer check failed"
import json, sys
from pathlib import Path

meta_path = Path(sys.argv[1])
out_dir   = Path(sys.argv[2])
meta = json.loads(meta_path.read_text(encoding="utf-8"))

max_layers = [l for l in meta["layers"] if l["type"] == "raster-max"]
assert max_layers, "No raster-max layers found in metadata"

wind_max = next((l for l in max_layers if "wind" in l["id"]), None)
assert wind_max is not None, "max_wind layer not found"

layer_file = out_dir / wind_max["file"]
assert layer_file.is_file(), f"Max wind layer file not found: {layer_file}"

layer = json.loads(layer_file.read_text(encoding="utf-8"))
assert "data" in layer, "Max layer missing 'data' field"
assert layer["type"] == "raster-max", f"Expected raster-max, got {layer['type']!r}"
assert "source_layer" in layer, "Max layer missing 'source_layer'"
assert "source_times" in layer, "Max layer missing 'source_times'"

# Verify max values >= any single time frame
source_id = layer["source_layer"]
source_entry = next((l for l in meta["layers"] if l["id"] == source_id), None)
if source_entry:
    src_file = out_dir / source_entry["file"]
    src_layer = json.loads(src_file.read_text(encoding="utf-8"))
    for t_idx, frame in enumerate(src_layer["frames"]):
        for j, row in enumerate(frame):
            for i, v in enumerate(row):
                max_v = layer["data"][j][i]
                assert max_v >= v - 1e-6, \
                    f"Max value {max_v} < frame value {v} at (t={t_idx},j={j},i={i})"

print(f"Max layer '{wind_max['id']}' OK: {len(layer['data'])} rows, max={layer['vmax']:.2f} {wind_max['unit']}")
PY

# ─────────────────────────────────────────────────────────────────────────────
# 6. Layer JSON structure
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 6: Layer JSON structure --"

python3 - "${OUT}/metadata.json" "${OUT}" <<'PY' && pass "All layer files have valid structure" || fail "Layer structure check failed"
import json, sys
from pathlib import Path

meta_path = Path(sys.argv[1])
out_dir   = Path(sys.argv[2])
meta = json.loads(meta_path.read_text(encoding="utf-8"))

for entry in meta["layers"]:
    layer_file = out_dir / entry["file"]
    assert layer_file.is_file(), f"Layer file not found: {layer_file}"
    layer = json.loads(layer_file.read_text(encoding="utf-8"))
    assert "id"    in layer, f"Layer {entry['id']!r} missing 'id'"
    assert "unit"  in layer, f"Layer {entry['id']!r} missing 'unit'"
    assert "vmin"  in layer, f"Layer {entry['id']!r} missing 'vmin'"
    assert "vmax"  in layer, f"Layer {entry['id']!r} missing 'vmax'"
    assert layer["vmax"] >= layer["vmin"], \
        f"Layer {entry['id']!r}: vmax < vmin"

print(f"All {len(meta['layers'])} layer files are structurally valid")
PY

# ─────────────────────────────────────────────────────────────────────────────
# 7. Point timeseries extraction — JSON
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 7: Point timeseries (JSON) --"

TS_JSON="${TMP}/ts.json"
if python3 "${EXTRACT_PT}" --demo --lat 54.0 --lon 9.0 \
   --output "${TS_JSON}" >/dev/null 2>&1; then
    pass "extract_point.py runs successfully in demo mode"
else
    fail "extract_point.py failed"
    python3 "${EXTRACT_PT}" --demo --lat 54.0 --lon 9.0 2>&1 | head -10 >&2
fi

python3 - "${TS_JSON}" <<'PY' && pass "Timeseries JSON has required structure" || fail "Timeseries JSON structure check failed"
import json, sys
from pathlib import Path

ts = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for field in ("requested_lat", "requested_lon", "grid_j", "grid_i", "timeseries"):
    assert field in ts, f"Timeseries missing field: {field!r}"

rows = ts["timeseries"]
assert len(rows) >= 1, "timeseries is empty"
assert "time" in rows[0], "timeseries row missing 'time'"
assert len(rows[0]) >= 2, "timeseries row has no variables besides 'time'"

print(f"Timeseries OK: {len(rows)} time steps, "
      f"lat={ts['requested_lat']}, lon={ts['requested_lon']}, "
      f"grid=({ts['grid_j']},{ts['grid_i']})")
PY

# ─────────────────────────────────────────────────────────────────────────────
# 8. Point timeseries extraction — CSV
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 8: Point timeseries (CSV) --"

TS_CSV="${TMP}/ts.csv"
if python3 "${EXTRACT_PT}" --demo --lat 54.0 --lon 9.0 \
   --format csv --output "${TS_CSV}" >/dev/null 2>&1; then
    pass "extract_point.py produces CSV output"
else
    fail "extract_point.py CSV mode failed"
fi

python3 - "${TS_CSV}" <<'PY' && pass "CSV timeseries has valid header and data rows" || fail "CSV structure check failed"
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
lines = [l for l in text.splitlines() if l.strip()]
assert len(lines) >= 2, f"CSV should have header + at least 1 data row, got {len(lines)} lines"

headers = lines[0].split(",")
assert "time" in headers, f"CSV header missing 'time': {headers}"
assert len(headers) >= 2, f"CSV must have at least 2 columns (time + variable), got: {headers}"

# Verify data rows have same column count
for i, line in enumerate(lines[1:], 1):
    cols = line.split(",")
    assert len(cols) == len(headers), \
        f"Row {i} has {len(cols)} columns, expected {len(headers)}"

print(f"CSV OK: {len(headers)} columns, {len(lines)-1} data rows")
PY

# ─────────────────────────────────────────────────────────────────────────────
# 9. Web viewer HTML
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 9: Web viewer --"

if [ -f "${WEB_HTML}" ]; then
    pass "index.html exists"
else
    fail "index.html not found at ${WEB_HTML}"
fi

python3 - "${WEB_HTML}" <<'PY' && pass "index.html contains required viewer elements" || fail "index.html missing required elements"
import sys
from pathlib import Path

html = Path(sys.argv[1]).read_text(encoding="utf-8")
required = [
    "metadata.json",       # loads metadata
    "main-canvas",         # canvas element
    "layer-list",          # layer selector
    "time-slider",         # time navigation
    "btn-play",            # play button
    "point-info",          # point inspection
    "raster-max",          # max-value product handling
]
for marker in required:
    assert marker in html, f"HTML missing required element/reference: {marker!r}"

print(f"index.html OK: {len(html)} characters, all required elements found")
PY

# ─────────────────────────────────────────────────────────────────────────────
# 10. Layer count matches metadata catalogue
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Test 10: Layer count consistency --"

python3 - "${OUT}/metadata.json" "${OUT}/layers" <<'PY' && pass "Layer files match metadata catalogue" || fail "Layer file count mismatch"
import json, sys
from pathlib import Path

meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
layers_dir = Path(sys.argv[2])

expected = set(l["id"] for l in meta["layers"])
found    = set(f.stem for f in layers_dir.glob("*.json"))

# Remove coordinate files from comparison
found -= {"xlat", "xlong"}

missing = expected - found
extra   = found - expected

assert not missing, f"Layer files missing: {missing}"
# Extra files are OK (they don't need to be in metadata)

print(f"OK: {len(expected)} layers in metadata, all have corresponding files")
PY

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi

echo "All visualization tests passed."
