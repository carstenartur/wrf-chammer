#!/bin/sh
# Product-path postprocessing/indexing compatibility test with synthetic NetCDF.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
IMAGE="${POSTPROCESSING_IMAGE:-wrf-postprocessing:latest}"
WORKDIR="${WORKDIR:-/tmp/postprocessing-container-compatibility}"
DOCKER_USER="$(id -u):$(id -g)"
SPEC_KEY=$(printf 'a%.0s' $(seq 1 64))
PLAN_KEY=$(printf 'b%.0s' $(seq 1 64))
SOURCE_REVISION=$(printf 'c%.0s' $(seq 1 40))
SPEC_DIR="${WORKDIR}/specifications/${SPEC_KEY}"
RUN_DIR="${WORKDIR}/run"
WRF_DIR="${RUN_DIR}/work/wrf/wrf"
POST_WORK="${RUN_DIR}/work/postprocessing/postprocessing"
INDEX_WORK="${RUN_DIR}/work/postprocessing/result-indexing"

rm -rf "${WORKDIR}"
mkdir -p "${SPEC_DIR}" "${WRF_DIR}" "${POST_WORK}" "${INDEX_WORK}"

cat > "${SPEC_DIR}/run-specification.json" <<JSON
{
  "specification_key": "${SPEC_KEY}",
  "immutable": true,
  "execution_started": false,
  "identity": {
    "source": {"repository_revision": "${SOURCE_REVISION}"},
    "era5_input": {
      "plan_key": "${PLAN_KEY}",
      "provenance": {
        "source": "compatibility-test-only",
        "artificial_weather_data": false
      }
    },
    "runtime": {
      "postprocessing": {
        "reference": "${IMAGE}",
        "identity": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      }
    }
  }
}
JSON

GENERATOR_LOG="${WORKDIR}/generator-container.log"
if ! docker run --rm \
  --network=none \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --pids-limit 128 \
  --user "${DOCKER_USER}" \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  --entrypoint python3 \
  -v "${REPO_ROOT}/ci/generate-postprocessing-compatibility-wrfout.py:/generator.py:ro" \
  -v "${RUN_DIR}:/run:rw" \
  "${IMAGE}" \
  /generator.py /run/work/wrf/wrf/wrfout_d01_2013-12-05_12:00:00 \
  >"${GENERATOR_LOG}" 2>&1; then
  cat "${GENERATOR_LOG}"
  exit 1
fi
cat "${GENERATOR_LOG}"

run_step() {
  step=$1
  container_log="${WORKDIR}/${step}-container.log"
  echo "Running isolated postprocessing step: ${step}"
  if ! docker run --rm \
    --network=none \
    --read-only \
    --cap-drop=ALL \
    --security-opt=no-new-privileges:true \
    --pids-limit 256 \
    --user "${DOCKER_USER}" \
    --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    --entrypoint python3 \
    -v "${SPEC_DIR}:/spec:ro" \
    -v "${RUN_DIR}:/run:rw" \
    "${IMAGE}" \
    /usr/local/bin/run-postprocessing-step.py \
      --step "${step}" \
      --specification /spec/run-specification.json \
      --run-root /run \
      --workdir "/run/work/postprocessing/${step}" \
      --result "/run/work/postprocessing/${step}/result.json" \
      --progress "/run/work/postprocessing/${step}/progress.json" \
    >"${container_log}" 2>&1; then
    cat "${container_log}"
    exit 1
  fi
  cat "${container_log}"
  python3 - "${RUN_DIR}/work/postprocessing/${step}/result.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "SUCCEEDED":
    raise SystemExit(f"Step failed: {payload}")
if not payload.get("artifacts"):
    raise SystemExit(f"Step returned no artifacts: {payload}")
print(f"{payload['status']}: {len(payload['artifacts'])} artifacts")
PY
}

run_step postprocessing
run_step result-indexing

python3 - "${RUN_DIR}" "${SPEC_KEY}" "${PLAN_KEY}" "${SOURCE_REVISION}" <<'PY'
import json
import re
import sys
from pathlib import Path
run = Path(sys.argv[1])
metadata = json.loads((run / "visualizations" / "metadata.json").read_text(encoding="utf-8"))
if metadata.get("provenance", {}).get("mode") != "wrf":
    raise SystemExit(f"Product runner did not use WRF input mode: {metadata.get('provenance')}")
if not metadata.get("provenance", {}).get("wrfout_files"):
    raise SystemExit("Postprocessor metadata has no wrfout provenance")
if not metadata.get("layers"):
    raise SystemExit("Postprocessor produced no layers")
index = json.loads((run / "results" / "index.json").read_text(encoding="utf-8"))
if index.get("specification_key") != sys.argv[2]:
    raise SystemExit("Result index specification key mismatch")
if index.get("era5_plan_key") != sys.argv[3]:
    raise SystemExit("Result index ERA5 key mismatch")
if index.get("source_revision") != sys.argv[4]:
    raise SystemExit("Result index source revision mismatch")
if index.get("artificial_weather_data") is not False:
    raise SystemExit("Result index lost the real-data product-path marker")
products = index.get("products")
if not isinstance(products, list) or not products:
    raise SystemExit("Result index has no products")
for product in products:
    if not re.fullmatch(r"[0-9a-f]{64}", product.get("sha256", "")):
        raise SystemExit(f"Invalid checksum: {product}")
    if product.get("size_bytes", 0) <= 0:
        raise SystemExit(f"Invalid size: {product}")
print(f"Validated WRF-mode metadata and {len(products)} indexed products")
PY

echo "Postprocessing and result-indexing product-path compatibility test passed."
