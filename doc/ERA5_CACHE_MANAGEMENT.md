# Managed ERA5 cache administration

The WRF Workbench stores real Copernicus ERA5 requests and verified GRIB files in a content-addressed cache. The cache administration view makes that storage visible and allows deliberate cleanup without exposing host paths or deleting data used by an active worker.

## User experience

The **Managed ERA5 cache** section lists every valid content-addressed plan and shows:

- the stable plan key;
- request coverage and partial-file count;
- stored bytes and regular-file count;
- simulation period and geographic bounds;
- creation, modification and last-use timestamps;
- age in days;
- source, dataset and checksum/provenance availability;
- persistent ERA5 download jobs stored below the plan;
- the number of active dependent jobs;
- whether deletion is currently allowed and, if not, why it is blocked.

Absolute cache locations, worker logs and credential material are not returned to the browser.

## API

### List cache entries

```http
GET /api/data/era5/cache
```

The response contains summaries ordered by last use, newest first.

### Inspect one entry

```http
GET /api/data/era5/cache/{plan-key}
```

The response includes a deletion confirmation snapshot:

```json
{
  "plan_key": "<64-character SHA-256 plan key>",
  "dependent_job_ids": ["era5-..."]
}
```

### Delete one entry

```http
POST /api/data/era5/cache/{plan-key}/delete
Content-Type: application/json

{
  "confirm_plan_key": "<same plan key>",
  "dependent_job_ids": ["<exact IDs shown by the latest detail response>"]
}
```

Deletion succeeds only when all of the following are still true:

1. the path is a canonical direct child of the configured managed cache;
2. the entry is a real directory rather than a symlink;
3. no dependent download job is `QUEUED`, `RUNNING` or `CANCELLING`;
4. the confirmed plan key matches exactly;
5. the confirmed dependency IDs match the current persistent job set.

A stale browser view therefore cannot silently delete a plan after its dependencies changed. The UI refreshes and requires the user to review the new state.

## Coordination with downloads

The application uses `CacheCoordinatedEra5DownloadManager`, a small specialization of the persistent downloader manager. Download enqueue operations and cache deletion share a re-entrant cache-operation lock.

This provides two guarantees:

- if a job is enqueued first, deletion sees it and blocks while it is active;
- if deletion acquires the lock first, a new enqueue waits and then revalidates the prepared plan after deletion instead of recreating an incomplete cache directory.

The lock is local to the single-user Workbench process. Distributed or multi-host cache coordination remains outside the local MVP.

## Atomic deletion and failure recovery

The plan directory is first renamed to a fixed-root tombstone name:

```text
.era5-cache/.deleting-<plan-key>-<random-suffix>
```

Only after the atomic rename is the directory tree removed. If recursive removal fails, the service attempts to restore the original plan directory before reporting a safe error.

Symlinked plan directories are never followed or deleted by the managed UI.

## Audit trail

After a successful deletion the Workbench appends a minimal event to:

```text
.era5-cache/.audit/cache-events.jsonl
```

The event records:

- deletion timestamp;
- plan key;
- released byte count;
- IDs of persistent download-job records removed with the plan.

It does not contain credentials, host paths, request bodies or weather data.

## Current dependency scope

The cache manager currently detects persistent ERA5 download jobs stored below each plan. Direct associations with later WPS/WRF simulation jobs will be added when issue #46 persists `InputDataset` references. Until then, the UI clearly describes the affected download-job history and does not claim awareness of simulation dependencies that are not yet stored.

## Tests

The implementation is covered without network access or CDS credentials:

```bash
python3 workbench/server/tests/test_era5_cache_service.py -v
python3 workbench/server/tests/test_era5_cache_api.py
python3 workbench/server/tests/test_ui_public_asset_build.py
node --check workbench/web/era5-cache-management.js
```

Coverage includes safe metadata redaction, storage metrics, dependency display, active-job blocking, stale-confirmation rejection, successful deletion, audit creation, symlink refusal, HTTP status mapping and Vite public-asset integration.
