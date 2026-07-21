# Pinned real WRF postprocessing and result indexing

This executor completes the last two frozen pipeline contracts:

```text
postprocessing
result-indexing
```

Together with the persistent worker, WPS executor and WRF executor, these steps allow a job to progress through all eight ordered contracts. They do not reinterpret a fixture as a model result: the product postprocessor accepts only actual `wrfout_d*` files from the managed WRF step.

## Runtime image

The immutable specification contains a pinned postprocessing runtime snapshot:

```json
{
  "reference": "wrf-postprocessing:local",
  "identity": "sha256:<64 lowercase hex characters>"
}
```

`Dockerfile.postprocessing` installs:

- the existing product postprocessor at `/app/postprocess.py`;
- NetCDF4, NumPy and xarray;
- `/usr/local/bin/run-postprocessing-step.py`.

The host executor inspects the configured local image and starts only the matching image ID or repository digest. It does not trust a mutable tag alone.

## Worker configuration

The combined dispatcher now routes all executable pipeline contracts:

```text
input-data                       → built into simulation_worker.py
geogrid, ungrib, metgrid         → wps_container_executor.py
real, wrf                        → wrf_container_executor.py
postprocessing, result-indexing  → postprocessing_container_executor.py
```

Start the worker with:

```bash
python3 -m workbench.simulation_worker \
  --executor workbench/pipeline_container_executor.py
```

## Container sandbox

The postprocessing container uses:

```text
--network=none
--read-only
--cap-drop=ALL
--security-opt=no-new-privileges:true
--pids-limit 256
--tmpfs /tmp:rw,nosuid,nodev,size=512m
```

On Unix it runs with the current UID/GID. Optional resource limits:

```text
WRF_CHAMMER_POSTPROCESSING_CPUS
WRF_CHAMMER_POSTPROCESSING_MEMORY
WRF_CHAMMER_POSTPROCESSING_PIDS_LIMIT
```

Allowed mounts:

| Host data | Container path | Mode |
|---|---|---|
| immutable specification | `/spec` | read-only |
| managed simulation run directory | `/run` | read-write |

The container receives no CDS or LLM credential requirement. The host executor discards raw container stdout/stderr; classified result documents are the only persistent status channel.

## Product postprocessing step

The runner requires:

```text
/run/work/wrf/wrf/wrfout_d*
```

It rejects missing, external or symbolic-link inputs. It then invokes the existing product postprocessor strictly with:

```text
python3 /app/postprocess.py --input <wrf-directory> --output <visualization-directory>
```

The test-only `--fixture` option is never selected by this executor.

Before success, the runner verifies `visualizations/metadata.json`:

- `provenance.mode` must equal `wrf`;
- the processed `wrfout_files` list must be non-empty;
- the layer list must be non-empty.

A fixture-mode metadata document is rejected as a process failure. All visualization files and the local postprocessing log are returned as managed artifacts; the persistent worker recomputes final SHA-256 hashes and sizes.

## Result-indexing step

Indexing is a separate step so postprocessing output remains immutable after completion. It:

1. revalidates product metadata as WRF mode;
2. recursively enumerates visualization products without following symlinks;
3. calculates SHA-256 and byte size for every product;
4. writes `results/index.json` atomically;
5. records the immutable specification key;
6. records the source revision and ERA5 plan key;
7. embeds pinned runtime snapshots and visualization provenance;
8. explicitly stores `artificial_weather_data: false`.

Example structure:

```json
{
  "version": 1,
  "specification_key": "...",
  "source_revision": "...",
  "era5_plan_key": "...",
  "runtime": {},
  "visualization_provenance": {
    "mode": "wrf",
    "wrfout_files": []
  },
  "artificial_weather_data": false,
  "products": [
    {
      "path": "visualizations/metadata.json",
      "sha256": "...",
      "size_bytes": 1234
    }
  ]
}
```

This index is itself persisted as a `result-index` artifact.

## Retry and path safety

Every attempt:

- removes the prior managed visualization or result directory;
- refuses symbolic-link components;
- refuses paths outside the simulation run root;
- validates every discovered artifact before indexing;
- writes result, progress and index JSON atomically.

A previous attempt cannot make a new attempt succeed through stale products.

## Failure classification

The executor uses the same classified failures as the other pipeline executors:

```text
NAMELIST_INVALID
INPUT_DATA_MISSING
RUNTIME_IMAGE_MISMATCH
EXECUTOR_OUTPUT_INVALID
PROCESS_CRASH
```

Raw Python tracebacks, Docker inspection output, host paths and secret values are not returned through API error fields.

## Tests

Fast tests:

```bash
python3 workbench/server/tests/test_postprocessing_container_executor.py -v
python3 workbench/server/tests/test_postprocessing_step_runner.py -v
```

They verify:

- pinned image selection and container sandbox flags;
- exact routing of both final step IDs;
- rejection of fixture-mode metadata by the product runner;
- deterministic product checksums and sizes;
- specification/ERA5/runtime provenance in the final index;
- symbolic-link product rejection.

## Product-path compatibility test

```bash
sh ci/test-postprocessing-container.sh
```

The workflow builds the real postprocessing image and creates a tiny deterministic WRF-like NetCDF file containing synthetic numeric values. It then executes:

```text
postprocessing → result-indexing
```

through the product runner, not through `postprocess.py --fixture`. The test requires:

- metadata with `provenance.mode = wrf`;
- non-empty WRF input provenance and layer definitions;
- a final result index with valid SHA-256 and non-zero size for every product;
- `artificial_weather_data: false` on the product-path result index.

This proves the real NetCDF input, postprocessor, image, file-contract and indexing compatibility. The synthetic numeric input is a test fixture and must not be presented as a scientific WRF simulation result.

## Scientific acceptance boundary

The complete real Xaver acceptance run still requires one persistent job to use:

- checksum-verified real CDS ERA5 input;
- real WPS geography data;
- actual geogrid, ungrib and metgrid outputs;
- actual real.exe and wrf.exe outputs;
- this WRF-mode postprocessor and result index;
- pinned image identities and frozen namelists throughout.

Only that complete path may be documented as a real meteorological simulation result.
