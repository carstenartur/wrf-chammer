# ERA5 data planning in the Workbench UI

The ERA5 data panel connects the guided simulation domain to the real-data input pipeline. It plans and prepares Copernicus ERA5 requests, but it deliberately does not start a network download yet.

## User flow

1. Start the Workbench and create a valid guided simulation preview.
2. In **Plan ERA5 boundary data**, refresh the data status.
3. Select **Plan real ERA5 data**.
4. Review request count, boundary time points, estimated download size, cache coverage and provenance.
5. Select **Prepare download files** to write the canonical plan and downloader configuration into the managed cache.

The button for ERA5 planning remains unavailable until the server has a valid guided simulation preview. This prevents the data request from drifting away from the domain and period shown in the simulation wizard.

## What the panel shows

The panel exposes only safe operational information:

- whether CDS credentials are configured;
- a remediation message when credentials are missing;
- the managed cache name, plan count and total size;
- whether a validated guided simulation preview is available;
- request and time-point counts;
- estimated download size;
- complete and partial cache coverage;
- ERA5 dataset names and the content-addressed plan key;
- the explicit statement `Artificial weather data: no`.

Credential values, home-directory paths and absolute external-cache paths are never returned to the browser.

## API

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

The server recomputes the canonical request plan. It does not trust a client-supplied cache key or target path.

The endpoint also accepts an explicit validated Workbench job or a period plus bounds for non-UI clients.

### Prepare files

```http
POST /api/data/era5/prepare
Content-Type: application/json

{
  "source": "latest-wizard-preview"
}
```

This writes:

```text
.era5-cache/<plan-key>/era5-plan.json
.era5-cache/<plan-key>/era5-download-config.json
```

The response includes:

```json
{
  "download_started": false
}
```

No CDS request is submitted by this operation.

### Latest wizard preview

```http
GET /api/wizard/latest
```

The local process retains the latest valid preview in memory. The API does not persist it as a hidden second job history. Persistent jobs and recovery belong to issue #45.

## Cache isolation

The default cache is `.era5-cache/` below the repository root. Tests and controlled deployments may set:

```text
WRF_CHAMMER_ERA5_CACHE_ROOT
```

A relative value is anchored at the repository root. An absolute configured path is accepted by the local server, but the browser only receives the label `configured-external-cache`.

## Security properties

- The local API still accepts loopback clients and loopback origins only.
- ERA5 targets are generated server-side.
- `ci/download-era5.py` rejects absolute targets, parent traversal, duplicate targets and symlink escapes.
- Prepared plan files are written atomically.
- The browser receives no CDS key or `.cdsapirc` content.
- Planning and preparing never start a download implicitly.

## Tests

Run the API test without network or CDS credentials:

```bash
python3 workbench/server/tests/test_era5_data_api.py
```

Run the browser test through the existing Playwright/Testcontainers path:

```bash
cd workbench/e2e
npm run screenshots:container
```

The browser test uses an isolated cache under `workbench-runs/`.

## Remaining work

Issue #44 still requires an explicit asynchronous download action with progress, cancellation, retry and cache cleanup. That action depends on the persistent worker and job state from issue #45. Real WPS/WRF execution remains part of issue #46.
