# Simulation-aware ERA5 cache deletion

Persistent simulations freeze the ERA5 plan key, verified file list and
provenance into their immutable pipeline specification. The managed ERA5 cache
therefore treats simulation records as first-class dependencies, in addition to
ERA5 download jobs.

## Dependency states

The cache API exposes path-free simulation records below:

```text
dependencies.simulation_jobs
```

Each record contains the simulation ID, immutable specification key, status,
relevant timestamps, current step and whether it blocks deletion.

The following states block deletion because the job can still start or is using
the input files:

```text
READY
QUEUED
PREPROCESSING
INITIALIZING
SIMULATING
POSTPROCESSING
CANCELLING
```

Terminal `SUCCEEDED`, `FAILED` and `CANCELLED` jobs remain visible as historical
dependencies. They do not block deletion indefinitely, but their IDs must be
included in the explicit confirmation. The UI warns that deleting the cache
prevents those records from reusing or independently reverifying the input bytes.

## Safe deletion protocol

The detail response includes two dependency snapshots:

```json
{
  "dependent_job_ids": ["era5-..."],
  "dependent_simulation_ids": ["sim-..."]
}
```

Deletion requires the exact current lists. The service re-reads both snapshots
inside the critical section. A newly created download or simulation causes:

```text
cache_dependency_snapshot_changed
```

rather than deleting data behind a stale confirmation dialog.

Simulation creation and cache deletion also share a process-local lock in the
threaded Workbench server. If deletion wins, later creation from an older
immutable specification is rejected with `input_dataset_unavailable` unless the
verified plan directory, checksum/provenance sidecars and every frozen input file
still exist safely.

## Audit

Successful deletion writes both dependency lists to:

```text
<cache-root>/.audit/cache-events.jsonl
```

The audit contains no credential values or absolute host paths.

## Scope

This is a local Workbench coordination contract. It does not replace filesystem
quotas or distributed locks for multiple independent Workbench server processes
sharing one cache root. Such deployments should use one authoritative API server
or an external storage coordinator.
