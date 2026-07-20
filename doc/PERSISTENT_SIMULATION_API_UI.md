# Persistent simulation jobs in the Workbench API and UI

This layer turns an immutable real-pipeline specification into a persistent simulation record. It deliberately separates four different facts:

1. a reproducible specification exists;
2. a persistent simulation record exists (`READY`);
3. the user has queued it (`QUEUED`);
4. a separate worker has claimed it and started a concrete pipeline step.

Creating or queueing a record never claims that WPS or WRF has executed.

## User flow

1. Complete the guided simulation preview.
2. Download and verify the required real ERA5 files.
3. Freeze an immutable WPS/WRF specification.
4. In **Create and queue real simulation jobs**, select that specification.
5. Select **Create READY job**.
6. Review the eight persisted step contracts, ERA5 input and runtime snapshots.
7. Select **Queue for worker** only when execution should be allowed.
8. Cancel a waiting job or create a new retry after cancellation/failure.

Until a worker is installed and running, a queued job remains honestly `QUEUED`, with `started_at: null`, `worker_id: null` and all steps still `PENDING`.

## API

### Create a persistent record

```http
POST /api/simulations
Content-Type: application/json

{
  "specification_key": "<64-hex immutable specification key>"
}
```

Returns `201 Created` and a `READY` simulation. The server loads the immutable specification itself; the browser cannot submit substitute steps, runtime identities, input files or namelists.

### List and inspect

```http
GET /api/simulations
GET /api/simulations/{simulation-id}
GET /api/simulations/{simulation-id}/events
GET /api/simulations/{simulation-id}/artifacts
```

The detail record contains:

- immutable specification key and optional `retry_of` relation;
- lifecycle timestamps and assigned worker ID;
- all eight ordered `JobStep` records;
- verified ERA5 `InputDataset` materialization;
- pinned WPS, WRF and postprocessing `RuntimeSnapshot` records;
- structured events, indexed artifacts and resource measurements;
- classified error information;
- server-derived `cancellable` and `retryable` capabilities.

No database path, host cache path or secret value is returned.

### Queue explicitly

```http
POST /api/simulations/{simulation-id}/enqueue
```

Only a `READY` job can transition to `QUEUED`. This operation records execution intent. A separate worker must later claim the job atomically before the first step becomes `RUNNING`.

### Cancel

```http
POST /api/simulations/{simulation-id}/cancel
```

- `READY` and `QUEUED` jobs become `CANCELLED` immediately because no process exists.
- Active jobs become `CANCELLING`; the worker must stop its process tree and call the internal cancellation-finalization operation.
- A terminal job remains terminal.

### Retry

```http
POST /api/simulations/{simulation-id}/retry
```

Only `FAILED` and `CANCELLED` jobs can be retried. Retry creates a new `READY` job with:

- a new immutable job ID;
- the same immutable specification key;
- a `retry_of` reference to the previous attempt;
- fresh step records and events.

The old attempt is never overwritten.

## Persistent state

The default database is:

```text
workbench-runs/state/workbench.sqlite3
```

A controlled deployment or test may set:

```text
WRF_CHAMMER_SIMULATION_DATABASE
```

SQLite uses foreign keys, WAL mode, full synchronous durability and transactional state/event updates. Restarting the browser or application does not lose `READY`, `QUEUED`, `CANCELLED`, `FAILED` or `SUCCEEDED` history.

## Browser component

Canonical source:

```text
workbench/web/simulation-job-queue.js
```

Vite public copy:

```text
workbench/ui/public/simulation-job-queue.js
```

All Workbench custom-element copies are synchronized with:

```bash
python3 ci/sync-workbench-public-assets.py
python3 ci/sync-workbench-public-assets.py --check
```

The component displays:

- immutable specification selection;
- explicit creation and queue actions;
- job history and lifecycle status;
- ordered step state and attempts;
- ERA5 input and pinned runtime summaries;
- artifact and measurement counts;
- recent structured events;
- cancel/retry capabilities;
- an explicit warning that queue state does not imply execution.

## Security and integrity

- Loopback/origin restrictions from the Workbench API remain in force.
- The server accepts only a specification key when creating a job.
- The validated store facade rechecks immutable status, exact step order, real ERA5 provenance and pinned runtime identities before database insertion.
- Job IDs and specification keys are regex validated.
- Artifact paths use a portable relative POSIX representation and reject absolute paths, drive letters, backslashes and traversal components.
- API responses contain no database path, cache path, secrets or raw SQLite exceptions.

## Tests

```bash
python3 workbench/server/tests/test_simulation_store.py -v
python3 workbench/server/tests/test_simulation_store_validation.py -v
python3 workbench/server/tests/test_simulation_job_api.py
node --check workbench/web/simulation-job-queue.js
python3 ci/sync-workbench-public-assets.py --check
```

The end-to-end API test covers:

```text
verified ERA5 input
→ immutable specification
→ READY simulation
→ QUEUED simulation
→ application restart
→ persisted QUEUED state
→ CANCELLED
→ new READY retry
```

It does not run a fake model process. Real execution remains the responsibility of the next worker/executor slice.

## Remaining work

- standalone worker that claims `QUEUED` simulations;
- controlled process/container cancellation;
- real per-step executors for input verification, WPS, `real.exe`, `wrf.exe`, postprocessing and result indexing;
- structured log/progress parsers;
- resource preflight and measurements from actual processes;
- the real Xaver micro-run through this standard path.
