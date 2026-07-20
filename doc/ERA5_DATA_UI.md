# ERA5 data planning, download jobs, and cache management in the Workbench UI

The ERA5 workflow connects the guided simulation domain to real Copernicus Climate Data Store input. Planning remains side-effect free; downloading is a separate, explicit action executed by a persistent local worker process. A global cache view makes the resulting content-addressed storage visible and supports deliberate, dependency-aware cleanup.

The product path never generates replacement weather fields. Offline fixtures remain test-only assets.

## User flow

1. Start the Workbench and create a valid guided simulation preview.
2. In **Plan ERA5 boundary data**, select **Plan real ERA5 data**.
3. Review request count, time coverage, estimated size, cache coverage and provenance.
4. Optionally select **Prepare download files** to materialize the canonical request files without network access.
5. In **Download and verify real ERA5 files**, select **Start real ERA5 download**.
6. Follow the persistent status, request progress and recent state events.
7. Cancel safely when necessary, or retry a failed/cancelled job. Completed files are reused.
8. Use **Managed ERA5 cache** to inspect storage, age, provenance and dependent download jobs, or to delete an unused entry after reviewing its current dependency snapshot.

The browser can be reloaded or closed after the download has been queued. Job state is stored below the content-addressed plan directory and remains visible after the application server restarts.

## What the UI exposes

The UI shows only safe operational information:

- whether CDS credentials are configured;
- a remediation message when credentials are missing;
- managed-cache name, plan count and total size;
- availability of the latest validated wizard preview;
- request and time-point counts;
- estimated download size;
- complete and partial cache coverage;
- ERA5 dataset names and content-addressed plan key;
- persistent download-job status and request progress;
- whether cancel or retry is currently valid;
- recent structured state events;
- per-plan storage size, file count, age and last use;
- dependent persistent ERA5 download jobs and active-job count;
- whether deletion is currently safe and, if not, why it is blocked;
- the explicit statement `Artificial weather data: no`.

Credential values, `.cdsapirc` contents, home-directory paths, worker logs and absolute external-cache paths are never returned to the browser.

## Planning API

### Status

```http
GET /api/data/era5/status
```

Returns credential presence, cache summary and availability of the latest valid wizard preview.

### Plan requests

```http
POST /api/data/era5/plan
Content-Type: application/json

{
  "source": "latest-wizard-preview",
  "interval_hours": 1,
  "margin_degrees": 1
}
```

The server recomputes the canonical request plan. It does not trust a client-supplied cache key or target path. The endpoint also accepts an explicit validated Workbench job or a period plus bounds for non-UI clients.

### Prepare files without downloading

```http
POST /api/data/era5/prepare
Content-Type: application/json

{
  "source": "latest-wizard-preview"
}
```

This atomically writes:

```text
.era5-cache/<plan-key>/era5-plan.json
.era5-cache/<plan-key>/request.json
.era5-cache/<plan-key>/era5-download-config.json
```

The response still contains `download_started: false`. Planning and preparation never contact the CDS.

## Persistent download API

### Queue a download

```http
POST /api/data/era5/downloads
Content-Type: application/json

{
  "source": "latest-wizard-preview",
  "interval_hours": 1,
  "margin_degrees": 1
}
```

The endpoint prepares the canonical plan, verifies whether credentials are needed and returns `202 Accepted` with a persistent job record. A complete cache can be verified without credentials. Missing requests require locally configured CDS credentials.

### List and inspect jobs

```http
GET /api/data/era5/downloads
GET /api/data/era5/downloads/{download-id}
GET /api/data/era5/downloads/{download-id}/events
```

States are:

```text
QUEUED
RUNNING
CANCELLING
CANCELLED
FAILED
SUCCEEDED
```

Each job includes timestamps, safe progress, retry/cancel capabilities and cache-relative artifact references.

### Cancel or retry

```http
POST /api/data/era5/downloads/{download-id}/cancel
POST /api/data/era5/downloads/{download-id}/retry
```

Cancellation first requests a graceful process-group stop and then forces termination after a bounded grace period. A cancelled job is never reported as failed or successful. Retry creates a new immutable job record and reuses every already verified target file.

## Managed cache API

### List and inspect cache entries

```http
GET /api/data/era5/cache
GET /api/data/era5/cache/{plan-key}
```

The list is ordered by last use. Each entry includes safe storage metrics, period and domain summaries, coverage, provenance and persistent download-job dependencies. Invalid or symlinked content-addressed directories are reported as non-deletable rather than followed.

### Delete an unused entry

```http
POST /api/data/era5/cache/{plan-key}/delete
Content-Type: application/json

{
  "confirm_plan_key": "<same plan key>",
  "dependent_job_ids": ["<exact IDs from the latest detail response>"]
}
```

Deletion is rejected when:

- the plan key is not canonical;
- the directory is missing, symlinked or outside the managed cache;
- a dependent job is `QUEUED`, `RUNNING` or `CANCELLING`;
- the confirmed plan key differs;
- the persistent dependency set changed since the browser loaded the entry.

The application serializes cache deletion with creation of new persistent download jobs. A plan directory is first atomically renamed to a fixed-root tombstone and only then removed. A successful deletion appends a minimal, path-free audit event under `.era5-cache/.audit/cache-events.jsonl`.

For the full deletion contract and current dependency limits, see [`ERA5_CACHE_MANAGEMENT.md`](ERA5_CACHE_MANAGEMENT.md).

## Process and persistence model

- HTTP handlers only validate and enqueue work.
- The real downloader runs as a separate Python process.
- The default concurrency is one worker; `WRF_CHAMMER_ERA5_DOWNLOAD_WORKERS` may configure one to four concurrent ERA5 workers.
- State files are written atomically under:

```text
.era5-cache/<plan-key>/downloads/<download-id>/
```

- Job state, progress, events, local worker log and manifest remain available after browser reload.
- Application shutdown handles `SIGTERM` and `SIGINT`, stops active workers and records cancellation.
- If an unclean restart finds an abandoned active state, it is marked `FAILED` with `worker_interrupted`; retry safely reuses completed cache files.
- State-event appends are serialized so concurrent worker, cancellation and recovery updates cannot corrupt JSONL sequence ordering.

This is an ERA5-specific vertical slice of issue #45. The general WPS/WRF state machine, SQLite migrations, pipeline-step model and event streaming remain separate work.

## Cache integrity and provenance

After a successful worker run, the Workbench independently verifies:

- exactly one manifest entry per canonical request;
- expected target path containment;
- non-empty files;
- exact file size;
- SHA-256 checksum equality.

It then writes:

```text
.era5-cache/<plan-key>/checksums.json
.era5-cache/<plan-key>/provenance.json
.era5-cache/<plan-key>/files/...
```

`provenance.json` identifies the ERA5 source, datasets, plan key, verification time and persistent download job. It explicitly records that the files are not artificial weather data.

## Retry behavior

`ci/download-era5.py` writes an atomic progress document and retries each uncached CDS request with exponential backoff. The default is three attempts. Existing non-empty targets are verified and reused without creating a CDS client. Partial `.part` files are never promoted to cache entries.

## Cache isolation

The default cache is `.era5-cache/` below the repository root. Tests and controlled deployments may set:

```text
WRF_CHAMMER_ERA5_CACHE_ROOT
```

A relative value is anchored at the repository root. An absolute configured path is accepted by the local server, but the browser receives only `configured-external-cache`.

## Security properties

- The API accepts loopback clients and loopback origins only.
- ERA5 targets and plan identities are generated server-side.
- Downloader commands never contain CDS credentials.
- Credentials are inherited only by the local worker process.
- `ci/download-era5.py` rejects absolute targets, parent traversal, duplicate targets and symlink escapes.
- API progress deliberately excludes downloader exception text and absolute paths.
- User-provided download IDs are matched against persisted state records before any job path is selected.
- Worker logs are local artifacts and are not returned through the browser API.
- Prepared plans, state, progress, manifests, checksums and provenance are written atomically.
- Cache deletion refuses symlinked entries, stale dependency confirmations and active workers.

## Tests

All tests are credential-free and network-free:

```bash
python3 workbench/server/tests/test_era5_data_api.py
python3 workbench/server/tests/test_era5_download_manager.py -v
python3 workbench/server/tests/test_era5_download_path_security.py -v
python3 workbench/server/tests/test_era5_cache_service.py -v
python3 workbench/server/tests/test_era5_download_api.py
python3 workbench/server/tests/test_era5_cache_api.py
python3 workbench/server/tests/test_ui_public_asset_build.py
node --check workbench/web/era5-download-control.js
node --check workbench/web/era5-cache-management.js
```

Coverage includes cached success, missing-credential rejection, process isolation, queue state, cancellation, retry, checksum validation, restart persistence, abandoned-worker recovery, event-write concurrency, API path redaction, storage metrics, active-job deletion blocking, stale dependency snapshots, audit creation, symlink refusal and Vite public-asset integration.

## Remaining work in issue #44

- Validate CDS credentials with an explicit minimal test request.
- Add richer per-request transfer metrics when CDS exposes reliable byte progress.
- Associate the verified input dataset directly with the later simulation job from issue #46, then include those simulation dependencies in cache deletion warnings.
