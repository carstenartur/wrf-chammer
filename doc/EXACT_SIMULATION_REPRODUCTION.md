# Exact simulation reproduction

The persistent simulation history distinguishes three operations that must not be
confused:

| Operation | Purpose | Source-state restriction | New state | Lineage |
|---|---|---|---|---|
| Create | Start from a selected immutable specification | none | `READY` | none |
| Retry | Repeat a failed or cancelled attempt | `FAILED` or `CANCELLED` | `READY` | `retry_of` |
| Reproduce exact run | Create a new attempt from exactly the same immutable specification | none | `READY` | `reproduced_from` / `reproductions` |

## API

```http
POST /api/simulations/{simulation-id}/reproduce
```

The endpoint creates a new persistent simulation record only. It never queues a
worker and never starts WPS or WRF. The response therefore reports `READY`, with
empty queue/start timestamps and all pipeline steps still `PENDING`.

The source job is not modified. Both directions of the lineage are exposed by job
and list endpoints:

```json
{
  "reproduced_from": "sim-...",
  "reproductions": ["sim-..."]
}
```

Lineage is persisted as an append-only `job_reproduced` event containing the source
job ID, immutable specification key and reproduction mode. It survives application
restart and remains separate from `retry_of`.

## Integrity and availability boundary

Exact reproduction reuses the source job's content-addressed immutable specification.
The normal job-creation boundary is executed again before the new job is created:

- the specification must still exist and pass validation;
- the same WPS, WRF and postprocessing identities remain pinned;
- the same verified ERA5 plan and frozen input-file list are used;
- the managed ERA5 cache entry, checksum/provenance sidecars and input files must
  still be available;
- no client-supplied namelist, runtime or host path is accepted.

If verified input was deliberately deleted, the endpoint fails with
`input_dataset_unavailable` instead of creating a misleading reproducible job.

## GUI behavior

Every persistent simulation detail view offers **Reproduce exact run**. The new job
becomes the selected record and is visibly linked to its source. The source view lists
all exact reproductions created from it. Queueing remains a separate explicit action.

## Scope within issue #48

This implements exact reproduction of a frozen run. It is deliberately not an
editable clone or draft workflow. Creating a modified descendant requires a new
validated wizard configuration and a newly frozen immutable specification; version
or input differences must then be compared and explicitly accepted in a later #48
slice.
