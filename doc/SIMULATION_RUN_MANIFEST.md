# Versioned simulation run manifests

Every persistent simulation can expose a deterministic, machine-readable snapshot of
its immutable configuration, verified inputs, runtime identities, pipeline state,
indexed artifacts and recorded resource measurements.

## Endpoint

```http
GET /api/simulations/{simulation-id}/run-manifest
```

The endpoint is local-only like the rest of the Workbench API. The response contains:

```json
{
  "ok": true,
  "manifest": {
    "format": {
      "name": "wrf-chammer-run-manifest",
      "version": 1
    }
  }
}
```

The manifest is available before, during and after execution. Its `completeness`
object states whether the captured job is terminal and whether it succeeded. A
manifest from a `READY`, `QUEUED`, active, failed or cancelled job is therefore an
honest persistent-state snapshot, not a claim that a scientific run completed.

## Included evidence

Version 1 contains:

- simulation identity, lineage through `retry_of`, status and timestamps;
- the complete immutable pipeline specification;
- the exact resolved `namelist.wps` and `namelist.input`, including SHA-256 and byte
  size;
- path-free ERA5 input metadata, provenance, relative file names, sizes and
  checksums;
- pinned WPS, WRF and postprocessing runtime snapshots;
- ordered step contracts and persisted step outcomes;
- indexed artifacts with relative paths, sizes and checksums;
- persisted resource measurements;
- an aggregated resource report for the complete job and each pipeline step.

The resource report distinguishes measured values from derived storage totals:

- `cpu_seconds_sum` and `wall_seconds_sum` sum persisted measurements;
- `max_rss_bytes` is the largest reported resident-memory value;
- `max_reported_disk_bytes` is the largest reported disk measurement;
- `input_size_bytes` is derived from the frozen ERA5 file list;
- `artifact_size_bytes` is derived from indexed artifact sizes;
- `elapsed_wall_seconds` is calculated only when both job start and finish
  timestamps exist.

## Deterministic integrity value

The manifest includes:

```text
integrity.canonical_payload_sha256
```

To verify it, remove the top-level `integrity` object, serialize the remaining JSON
with sorted keys and compact separators, encode it as UTF-8, and calculate SHA-256.
The same unchanged persistent job state and specification artifacts produce the same
manifest and digest.

This digest protects the exported snapshot against accidental changes. It is not a
digital signature and does not establish an external trust authority.

## Security and path handling

The manifest never includes the simulation event stream, worker environment or raw
process output. Secret-like keys are redacted. Absolute host paths in persisted
metadata are replaced with a redaction marker. Specification artifacts are read only
from the validated content-addressed specification directory; symlinked, missing or
escaping files make manifest generation fail closed.

Resolved namelists are included because they are required for reproducibility. They
must remain free of host-specific secrets by construction.

## Scope within issue #48

This is the first reproducibility/export slice of #48. It establishes a versioned
run-manifest contract and resource summary without changing the simulation state
machine. Search/filter views, exact reproduction/clone workflows, job comparison,
CSV resource export and differentiated artifact retention remain follow-up work.
