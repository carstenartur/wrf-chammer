# Pinned real.exe and wrf.exe container executor

This executor extends the persistent simulation worker with two separate WRF steps:

```text
real
wrf
```

It uses the existing reproducible WRF image built by `Dockerfile`. The immutable specification must contain a pinned WRF runtime snapshot:

```json
{
  "reference": "wrf-reproducible:local",
  "identity": "sha256:<64 lowercase hex characters>"
}
```

The host executor inspects the local image and runs only the matching image ID or repository digest. A mutable tag is not accepted as the execution identity.

## Configure the persistent worker

Use the combined pipeline dispatcher:

```bash
python3 -m workbench.simulation_worker \
  --executor workbench/pipeline_container_executor.py
```

The dispatcher routes:

```text
geogrid, ungrib, metgrid → wps_container_executor.py
real, wrf                → wrf_container_executor.py
other steps              → EXECUTOR_UNAVAILABLE
```

Postprocessing and result indexing remain explicitly unavailable until their own executor is installed.

## Container isolation

The WRF host executor uses:

```text
--network=none
--read-only
--cap-drop=ALL
--security-opt=no-new-privileges:true
--pids-limit 512
--tmpfs /tmp:rw,nosuid,nodev,size=512m
```

On Unix, the container runs with the current UID/GID. Optional limits are:

```text
WRF_CHAMMER_WRF_CPUS
WRF_CHAMMER_WRF_MEMORY
WRF_CHAMMER_WRF_PIDS_LIMIT
```

Allowed mounts are:

| Host data | Container path | Mode |
|---|---|---|
| immutable specification | `/spec` | read-only |
| managed simulation directory | `/run` | read-write |

Every mount target must be an absolute container path without `:` and its mode must be exactly `ro` or `rw`.

No CDS credentials are required or inherited by the worker executor.

## Runtime image contract

`Dockerfile` installs:

- `/opt/wrf/run/real.exe`;
- `/opt/wrf/run/wrf.exe`;
- Python 3;
- `/usr/local/bin/_run_wrf_step_core.py`;
- `/usr/local/bin/run-wrf-step.py`.

`ci/verify-wrf-runtime.sh` verifies both binaries, all shared libraries, the Python runtime and the step-runner CLI during the image build.

## real step

The runner uses a dedicated directory:

```text
workbench-runs/simulations/<job-id>/work/wrf/real/
```

It:

1. copies the frozen `namelist.input` from the immutable specification;
2. links read-only WRF runtime tables from `/opt/wrf/run`;
3. requires `met_em.d*.nc` files from the preceding persistent `metgrid` step;
4. rejects symlinked or external handoff files;
5. hard-links or copies those inputs into the `real` work directory;
6. removes outputs from previous attempts;
7. runs `/opt/wrf/run/real.exe`;
8. requires both `wrfinput_d*` and `wrfbdy_d*` output;
9. indexes initialization files and immutable `rsl` logs.

Known failures are classified as input, namelist, domain, memory, disk or process errors instead of exposing raw runtime exceptions.

## wrf step

The simulation step uses a separate directory:

```text
workbench-runs/simulations/<job-id>/work/wrf/wrf/
```

It:

1. recopies the frozen `namelist.input`;
2. requires the preceding `real` step's `wrfinput`/`wrfbdy` files;
3. hard-links or copies them into the WRF work directory;
4. removes old `wrfout` and `rsl` files;
5. runs `/opt/wrf/run/wrf.exe`;
6. parses only the last 64 KiB of `rsl.out.0000` while the process is running;
7. publishes simulation time, simulated seconds, fraction, output count and an estimated remaining time;
8. requires at least one `wrfout_d*` file before success;
9. indexes model outputs and `rsl` logs.

The bounded tail scan prevents progress polling from repeatedly reading an unbounded simulation log. The persistent worker independently computes final artifact SHA-256 hashes and sizes before recording them.

## Progress format

Example progress document:

```json
{
  "phase": "wrf",
  "simulation_time": "2013-12-05T15:00:00Z",
  "simulated_seconds": 10800,
  "total_seconds": 21600,
  "fraction": 0.5,
  "output_files": 1,
  "eta_seconds": 30
}
```

This is persisted as structured step progress and can later drive the GUI without scraping raw log text in the browser.

## Cancellation

The persistent worker supervises the host executor process group. A user cancellation sends `SIGTERM`, waits for the configured grace period, then sends `SIGKILL` if necessary. The final job state is `CANCELLED`.

An independent worker shutdown is classified as `FAILED / worker_interrupted` so it remains retryable and is not misrepresented as a user action.

## Failure classification

The runtime runner maps recognizable failures to:

```text
INPUT_DATA_MISSING
DOMAIN_CONFIGURATION_INVALID
NAMELIST_INVALID
INSUFFICIENT_MEMORY
DISK_FULL
WRF_NUMERICAL_INSTABILITY
RUNTIME_IMAGE_MISMATCH
PROCESS_CRASH
EXECUTOR_OUTPUT_INVALID
```

## Tests

Fast tests:

```bash
python3 workbench/server/tests/test_wrf_container_executor.py -v
python3 workbench/server/tests/test_wrf_step_runner.py -v
```

They verify:

- pinned image inspection and selector usage;
- sandbox flags and validated mount modes;
- byte-exact routing between WPS, WRF and unsupported steps;
- immutable namelist reset on retry;
- symlink-safe `met_em` and `wrfinput` handoff;
- symlink-safe output indexing;
- structured WRF progress parsing from representative `rsl` timing lines;
- bounded tail parsing when `rsl.out.0000` is larger than 64 KiB.

The existing reproducible Docker build additionally compiles and verifies the actual `real.exe` and `wrf.exe` binaries and checks the installed runner.

## Scientific acceptance boundary

The fast tests do not claim that a meteorologically valid forecast has completed. A real acceptance run must combine:

- checksum-verified real ERA5 input;
- real WPS geography data;
- `geogrid`, `ungrib` and `metgrid` outputs from the same persistent job;
- successful `real.exe` and `wrf.exe` steps through this executor;
- real postprocessing and result indexing;
- provenance that links every input, image digest, namelist and output.

The complete Xaver micro-run remains the acceptance target after postprocessing and result-indexing executors are connected.
