# Real step-separated WPS container executor

This executor connects the persistent simulation worker to the real WPS binaries already built by the repository. It implements three separate pipeline steps:

```text
geogrid
ungrib
metgrid
```

It does not combine them into a hidden shell pipeline. Each step receives its own persistent `JobStep`, progress events, logs, artifacts and resource measurement.

## Runtime image

The immutable specification's `runtime.wps` snapshot must identify the locally built ERA5/WPS image, not a mutable unverified tag:

```json
{
  "reference": "era5-pipeline:local",
  "identity": "sha256:<64 lowercase hex characters>"
}
```

The executor runs:

```text
docker image inspect <reference>
```

It accepts the image only when the pinned identity equals either:

- the local image ID; or
- the digest of one of the image's repository digests.

The actual `docker run` uses that pinned selector. A tag alone is never the execution identity.

`Dockerfile.era5` installs:

- `/opt/wps/geogrid.exe`;
- `/opt/wps/ungrib.exe`;
- `/opt/wps/metgrid.exe`;
- WPS 4.6.0 `GEOGRID.TBL.ARW` and `METGRID.TBL.ARW`;
- the WPS variable tables including `Vtable.ERA-interim.pl`;
- `/usr/local/bin/run-wps-step.py`.

## Configure the worker

Start the persistent worker with the WPS executor:

```bash
python3 -m workbench.simulation_worker \
  --executor workbench/wps_container_executor.py
```

Or set:

```text
WRF_CHAMMER_SIMULATION_STEP_EXECUTOR=workbench/wps_container_executor.py
```

The executor supports only WPS steps. After a successful `metgrid`, the next `real` step fails with `EXECUTOR_UNAVAILABLE` until the pinned WRF container executor is installed. No fake initialization or model output is created.

## Geography data

`geogrid` requires a real local WPS geography dataset:

```text
WRF_CHAMMER_WPS_GEOG_ROOT=/path/to/WPS_GEOG
```

The path is mounted read-only as `/geog`. It is never copied into the repository or stored in API responses. Missing or unreadable geography is classified as:

```text
WPS_GEOGRAPHY_MISSING
```

The product path accepts only a real geography archive supplied through this mount. It does not select `tests/era5-mini/wps/geo_em.d01.nc`.

The fast compatibility workflow intentionally avoids downloading the multi-gigabyte WPS geography archive. It uses the repository's explicitly synthetic `geo_em.d01.nc` test fixture solely to exercise the real `ungrib.exe` and `metgrid.exe` binaries and their file contracts. That test does not validate `geogrid` or establish meteorological validity.

## Container sandbox

The host executor invokes the configured container engine directly, without a shell, with:

```text
--network=none
--read-only
--cap-drop=ALL
--security-opt=no-new-privileges
--pids-limit 256
--tmpfs /tmp:rw,nosuid,nodev,size=512m
```

On Unix it runs with the current host UID/GID so generated files are not root-owned.

Allowed mounts are:

| Host data | Container path | Mode |
|---|---|---|
| immutable specification directory | `/spec` | read-only |
| content-addressed ERA5 plan | `/era5` | read-only |
| managed simulation run directory | `/run` | read-write |
| optional real WPS geography | `/geog` | read-only |

Optional resource limits:

```text
WRF_CHAMMER_WPS_CPUS
WRF_CHAMMER_WPS_MEMORY
WRF_CHAMMER_WPS_PIDS_LIMIT
```

The container has no CDS or internet requirement. ERA5 input was downloaded and verified before WPS starts.

## Step behavior

### geogrid

The runtime runner:

1. copies the frozen `namelist.wps` into the managed WPS work directory;
2. replaces `geog_data_path` with the read-only `/geog` mount;
3. installs the pinned `GEOGRID.TBL.ARW` as `GEOGRID.TBL`;
4. runs the real `/opt/wps/geogrid.exe`;
5. requires at least one non-symlink `geo_em.d*.nc` output.

Artifacts are indexed as `wps-geographical-grid` plus WPS logs.

### ungrib

For each frozen ERA5 file, the runner:

1. resolves the relative file below `/era5`;
2. rejects traversal, symlinks, missing or empty input;
3. maps pressure-level requests to `PLEV` and single/surface requests to `SFC`;
4. installs `Vtable.ERA-interim.pl`;
5. updates the frozen namelist prefix;
6. runs the real `/opt/wps/ungrib.exe`;
7. requires the expected `<prefix>:*` intermediate files.

Each request updates structured progress. Intermediate files and logs are indexed.

### metgrid

The runner requires:

- `geo_em.d*.nc` produced by the preceding `geogrid` step in the product path;
- all expected `PLEV:*` and `SFC:*` intermediate files;
- the frozen `namelist.wps`.

It writes the deterministic `fg_name` list, installs `METGRID.TBL.ARW`, runs `/opt/wps/metgrid.exe` and requires `met_em.d*.nc` output.

## Error classification

The executor uses the established categories where possible:

```text
INPUT_DATA_MISSING
WPS_GEOGRAPHY_MISSING
NAMELIST_INVALID
RUNTIME_IMAGE_MISMATCH
PROCESS_CRASH
EXECUTOR_OUTPUT_INVALID
EXECUTOR_UNAVAILABLE
```

Raw container-inspection output, absolute mount paths and provider secrets are not written into API error fields.

## Tests

### Fast command-contract tests

```bash
python3 workbench/server/tests/test_wps_container_executor.py -v
```

They use a fake container-engine executable and verify:

- image inspection before execution;
- pinned image selector;
- network and capability isolation;
- read-only specification/ERA5 mounts;
- managed read-write run mount;
- digest mismatch prevents container start;
- non-WPS steps are rejected.

### Step-separated WPS binary compatibility test

```bash
sh ci/test-wps-container-steps.sh
```

This CI workflow builds `wps-reproducible` and `era5-pipeline`, then runs:

- the repository's generated mini pressure- and single-level GRIB test files; and
- the explicitly synthetic `tests/era5-mini/wps/geo_em.d01.nc` fixture

through separate real WPS 4.6.0 `ungrib.exe` and `metgrid.exe` containers. It checks:

- `PLEV:*` and `SFC:*` intermediate output;
- successful structured result documents for both steps;
- structurally readable `met_em.d01.*` output;
- variables `TT`, `UU`, `VV`, `GHT` and `PSFC` using `ncdump`.

This proves executable, table, Vtable, mount and NetCDF-format compatibility. It does **not** prove meteorological correctness, does not test real `geogrid` output and must not be presented as a scientific simulation result.

### Real acceptance requirements

A real acceptance run must use the standard persistent worker path with:

- checksum-verified real ERA5 files from the CDS workflow;
- a real read-only WPS geography archive;
- `geo_em` produced by the `geogrid` step in the same job;
- `met_em` produced from those real inputs;
- the later pinned `real.exe` and `wrf.exe` container steps;
- result provenance linking all of these artifacts.

## Remaining work

The next container-executor slice implements:

```text
real
wrf
postprocessing
result-indexing
```

using the same pinned-image, mount, progress, artifact and cancellation contracts. The first complete real Xaver micro-run then flows through the persistent worker without a separate shell-only shortcut.
