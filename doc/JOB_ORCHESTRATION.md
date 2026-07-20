# Persistent job orchestration

The Workbench uses a local SQLite queue and a separate worker process for work that must not run inside an HTTP request.

This is the first implementation slice of issue #45. It provides durable jobs, queueing, cancellation, retry, events, artifacts, and recovery while keeping the existing synchronous dry-run API compatible.

## Start and stop

```bash
python3 wrf-chammer start
python3 wrf-chammer status --json
python3 wrf-chammer logs --component all
python3 wrf-chammer stop
```

`start` launches both the local API/UI and the persistent worker. The worker can also be run in the foreground:

```bash
python3 wrf-chammer worker
python3 wrf-chammer worker --once --worker-id manual-worker
```

## Storage

Default paths:

```text
workbench-runs/jobs.sqlite3
workbench-runs/persistent/<job-id>/attempt-0001/
workbench-runs/.runtime/server.json
workbench-runs/.runtime/server.log
workbench-runs/.runtime/worker.log
```

Supported local overrides:

```text
WRF_CHAMMER_JOB_DATABASE
WRF_CHAMMER_PERSISTENT_ROOT
WRF_CHAMMER_RUNTIME_DIR
```

The database and persistent output root must remain below `workbench-runs/`.

## Persisted model

Schema version 1 contains:

- `simulation_jobs`: submitted configuration, state and attempt,
- `job_steps`: per-attempt state, progress, timestamps and log reference,
- `job_events`: append-only lifecycle messages,
- `artifacts`: type, relative path, size and SHA-256 checksum,
- `workers`: process identity and heartbeat,
- `schema_migrations`: applied schema versions.

The first worker slice uses one generic step named `workbench-run`. Later WPS/WRF integration splits this into dedicated data, preprocessing, initialization, simulation, and postprocessing steps.

## States

```text
DRAFT
VALIDATING
WAITING_FOR_DATA
DOWNLOADING_DATA
READY
QUEUED
PREPROCESSING
INITIALIZING
SIMULATING
POSTPROCESSING
SUCCEEDED
FAILED
CANCELLING
CANCELLED
```

The current generic worker normally follows:

```text
DRAFT → QUEUED → SIMULATING → SUCCEEDED
                              ↘ FAILED
                              ↘ CANCELLING → CANCELLED
```

## Creating an asynchronous job

The existing endpoint remains available:

```http
POST /api/jobs
```

The default remains synchronous for compatibility. Request persistent execution explicitly:

```json
{
  "execution": "queued",
  "start": true,
  "priority": 0,
  "config": {
    "id": "xaver-persistent-dry-run",
    "mode": "dry-run",
    "name": "Xaver persistent dry-run",
    "period": {
      "start": "2013-12-05T12:00:00Z",
      "end": "2013-12-06T06:00:00Z"
    },
    "domain": {
      "label": "xaver-small",
      "center_lat": 54.5,
      "center_lon": 8.5,
      "dx_km": 9,
      "dy_km": 9,
      "e_we": 91,
      "e_sn": 91
    },
    "inputs": {"source": "era5"},
    "outputs": {"directory": "replaced-by-server"}
  }
}
```

`"async": true` is accepted as an alias. The server validates the configuration, replaces the output path with a managed path, stores the job, and returns immediately.

## API

```http
POST /api/jobs
GET  /api/jobs?limit=100
GET  /api/jobs/{id}
POST /api/jobs/{id}/cancel
POST /api/jobs/{id}/retry
GET  /api/jobs/{id}/events?after_id=0&limit=200
GET  /api/jobs/{id}/artifacts
```

Events use increasing database IDs. Clients can poll with `after_id` without reloading the full history.

## Queue behavior

Workers claim jobs atomically in this order:

```text
priority descending
creation time ascending
job id
```

The normal local start command runs one worker. Multiple explicit workers can claim different jobs, but resource reservations are not part of this first slice.

## Cancellation and retry

A waiting job can be cancelled directly. A running job enters `CANCELLING` while its process tree is stopped, then finishes as `CANCELLED`.

Only `FAILED` and `CANCELLED` jobs can be retried. Retry:

- increments the attempt number,
- creates a new step record,
- clears transient errors,
- returns the job to `QUEUED`,
- preserves previous events and artifacts.

## Recovery

Workers publish heartbeats. On startup, jobs in active states whose worker disappeared are marked:

```text
FAILED / PROCESS_CRASH
```

They can then be retried explicitly instead of remaining incorrectly marked as running.

## Artifact integrity

After each attempt the worker indexes regular files below:

```text
logs/
outputs/
visualizations/
```

It stores the relative path, type, size, attempt, timestamp, and SHA-256 checksum. Symlinks and paths outside the managed attempt directory are ignored.

## Tests

All tests run from a plain checkout:

```bash
python3 ci/test_persistent_job_store.py
python3 ci/test_persistent_job_api.py
python3 ci/test_workbench_cli.py
```

## Current limitations

This slice does not complete all of issue #45:

- no queue/history page in the GUI yet,
- incremental polling instead of Server-Sent Events,
- one generic worker step rather than separate WPS/WRF steps,
- no resource reservation policy,
- retry restarts the complete generic attempt.

These capabilities build on this persistence model in issues #45 and #46.
