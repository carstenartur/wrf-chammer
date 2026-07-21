# Persistent simulation worker and step-executor protocol

The simulation worker is a standalone process that atomically claims `QUEUED` simulation jobs from the persistent SQLite store. It is not part of an HTTP request and it never treats a test fixture as a real WPS or WRF result.

## Start the worker

```bash
python3 -m workbench.simulation_worker
```

Useful options:

```text
--worker-id <stable-local-name>
--once
--poll-seconds <seconds>
--cancel-grace-seconds <seconds>
--executor <local-program>
```

The executor can also be configured with:

```text
WRF_CHAMMER_SIMULATION_STEP_EXECUTOR
```

Without an executor, the worker still performs the real `input-data` step. It then fails the first WPS step with:

```text
EXECUTOR_UNAVAILABLE
```

This is intentional. The product path does not create fake geographical grids, `met_em` files, WRF initialization files, model output or visualizations.

## Worker lifecycle

At startup the worker:

1. opens the same simulation database as the application;
2. marks abandoned active jobs `FAILED` with `worker_interrupted`;
3. atomically claims the oldest `QUEUED` job;
4. starts its first pending step through the store state machine;
5. executes and records each step until success, failure or cancellation.

Multiple worker processes may poll the same database. SQLite `BEGIN IMMEDIATE` queue claiming ensures that only one worker claims a job.

## Real input-data verification

The first step is implemented directly by the worker and does not depend on an external executor. For every ERA5 file frozen into the immutable specification, the worker checks:

- relative path containment below the content-addressed plan directory;
- no symbolic link;
- file presence;
- exact file size;
- SHA-256 equality using constant-time comparison.

It records progress as:

```json
{
  "verified_files": 2,
  "total_files": 4,
  "verified_bytes": 123456
}
```

On success it writes and indexes:

```text
workbench-runs/simulations/<job-id>/steps/input-data/verified-input.json
```

The artifact explicitly records:

```json
{
  "artificial_weather_data": false
}
```

## External step-executor protocol

All later steps use one explicitly configured local executor program. The worker invokes it without a shell and passes:

```text
--step <step-id>
--job-id <job-id>
--specification-key <key>
--specification-directory <directory>
--run-directory <directory>
--step-directory <directory>
--result <result.json>
--progress <progress.json>
```

A Python executor is invoked with the current Python interpreter. Another executable is invoked directly.

Known secret environment variables such as `CDSAPI_KEY`, `OPENAI_API_KEY` and `GEMINI_API_KEY` are removed before the executor starts. ERA5 has already been downloaded and verified; WPS and WRF do not need CDS credentials.

### Progress file

The executor may atomically update the progress file with a JSON object. The worker detects changes and persists them as structured `step_progress` events.

Example:

```json
{
  "simulation_time": "2013-12-05T14:00:00Z",
  "simulated_seconds": 7200,
  "output_files": 2,
  "eta_seconds": 110
}
```

### Result file

A successful executor writes:

```json
{
  "status": "SUCCEEDED",
  "progress": {"percent": 100},
  "artifacts": [
    {
      "path": "steps/geogrid/geo_em.d01.nc",
      "kind": "wps-geographical-grid",
      "sha256": "optional-expected-sha256",
      "metadata": {}
    }
  ],
  "resources": {
    "cpu_seconds": 12.5,
    "max_rss_bytes": 840000000,
    "disk_bytes": 32400000,
    "wall_seconds": 14.1
  }
}
```

Artifact paths are relative to the managed simulation directory. The worker rejects:

- absolute paths;
- parent traversal;
- backslashes;
- symbolic links;
- missing files;
- checksum mismatches.

It independently computes and stores final SHA-256 and file size values.

A failed executor may write:

```json
{
  "status": "FAILED",
  "error": {
    "code": "WPS_GEOGRAPHY_MISSING",
    "message": "Required geographical data is not available."
  }
}
```

If the result is absent or invalid, the worker uses a classified executor/process error instead of exposing a raw traceback through the API.

## Cancellation

For an active job:

1. the API/store changes the job to `CANCELLING`;
2. the worker sends `SIGTERM` to the executor process group;
3. it waits for the configured grace period;
4. it sends `SIGKILL` if the process remains alive;
5. it finalizes the job as `CANCELLED`.

A cancelled job is not reported as failed or successful. A retry creates a new immutable job attempt.

## Resource measurements and artifacts

For every completed step the worker records:

- wall-clock time;
- child CPU time;
- maximum child RSS where available;
- bytes of indexed output artifacts;
- worker ID.

Executor logs are local step artifacts. Browser/API responses expose only safe relative paths and structured metadata.

## Tests

```bash
python3 workbench/server/tests/test_simulation_worker.py -v
```

The test suite is network-free and covers:

1. real SHA-256 input verification followed by honest `EXECUTOR_UNAVAILABLE` failure;
2. all eight steps through a test-only executor protocol, including progress, artifacts and measurements;
3. removal of CDS credentials from executor environments;
4. process-group cancellation and terminal `CANCELLED` state.

The successful test executor is a fixture used only to test the protocol. It is never selected by the product application.

## Remaining implementation

The next slice supplies the real container executor for:

```text
geogrid
ungrib
metgrid
real
wrf
postprocessing
result-indexing
```

It must use the pinned runtime identities from the immutable specification, enforce mount/resource boundaries, classify known model failures and produce the declared artifacts. The real Xaver micro-run then uses this same worker path.
