# WRF Workbench User Guide

This guide explains the local WRF Workbench through the Storm Xaver workflow. It
covers domain planning, real ERA5 acquisition, persistent simulation records and
the separate acceptance path for computed weather-map results.

## What the screenshots do — and do not — show

The guide uses three different visual categories. They must not be confused:

| Screenshot | Meaning | Contains computed weather? |
|---|---|---:|
| `xaver-01` to `xaver-06` | Search, configuration, planning, dry-run status and logs | No |
| `xaver-03b-map-domain-wizard.png` | Geographic basemap plus the selected WRF domain | No |
| `xaver-07-weather-map.png` | A rendered layer from real WRF output | Yes |

The normal documentation screenshot command generates only the first two
categories. A green **User Guide Screenshots** workflow therefore proves that the
UI flow and geographic planning map render correctly; it does **not** prove that
WRF produced a meteorological field.

For normal interactive use, the planning widget loads OpenStreetMap tiles. The
deterministic CI screenshot uses the explicit URL option
`?basemap=natural-earth` and a small local Natural Earth vector subset so that
country outlines remain visible without contacting an external tile service.
Both are geographic context only. Neither is weather data.

The computed result image `xaver-07-weather-map.png` is intentionally absent until
a complete real-data run has produced WRF-proven visualization artifacts and the
separate result-map test has verified that the selected field is spatially
varying and visibly rendered.

## Generate the regular UI screenshots

From the repository root:

```bash
sh ci/generate-user-guide-screenshots.sh
```

The command builds the modern UI, starts the real local Workbench server in the
browser integration environment and writes screenshots to:

```text
doc/user-guide/screenshots/
```

It captures:

```text
xaver-01-search.png
xaver-02-event-selected.png
xaver-03-domain-resolution.png
xaver-03b-map-domain-wizard.png
xaver-03c-era5-data-plan.png
xaver-04-preview-config.png
xaver-05-dry-run-status.png
xaver-06-logs.png
```

The command does not generate `xaver-07-weather-map.png`.

## 1. Start the local Workbench

From the repository root:

```bash
python3 wrf-chammer doctor
python3 wrf-chammer start
```

Open:

```text
http://127.0.0.1:8080/
```

The page contains system readiness, guided geographic planning, ERA5 data
controls, persistent simulation records and the original event/preset workflow.

![Xaver search screen](user-guide/screenshots/xaver-01-search.png)

## 2. Search and select Storm Xaver

The UI starts with `Xaver` as a useful reference query. Select the Xaver result to
load event metadata and curated presets from the server-side catalogue:

```http
GET /api/events?q=xaver
GET /api/events/xaver
```

![Xaver event selected](user-guide/screenshots/xaver-02-event-selected.png)

## 3. Select a simulation domain

There are two supported planning paths.

### 3.1 Guided geographic planning

The **Guided simulation planning** section provides an interactive geographic
map. Select **Draw simulation area** and drag a rectangle, or enter coordinates
with the keyboard-accessible numeric fields.

The Xaver reference values are:

```text
Bounds:     2.0–14.0° E, 51.0–58.0° N
Period:     2013-12-05 12:00 UTC to 2013-12-06 06:00 UTC
Profile:    balanced regional
Resolution: 9 km
```

Select **Plan domain and preview job**. The browser calls:

```http
POST /api/wizard/preview
```

The server derives:

- domain centre and physical extent;
- `e_we` and `e_sn`;
- grid spacing and recommended time step;
- output-frame count;
- estimated RAM, storage and wall-clock range;
- a schema-valid preview configuration.

The blue rectangle is the planned model domain. Country outlines, seas and place
labels are geographic context. No meteorological values are calculated in this
view.

![Guided Xaver map-domain plan with geographic context, grid and resource estimates](user-guide/screenshots/xaver-03b-map-domain-wizard.png)

Expert controls may override grid spacing, vertical levels and output interval.
All overrides are validated by the server. See
[`SIMULATION_WIZARD.md`](SIMULATION_WIZARD.md) for the assumptions.

### 3.2 Curated presets

The preset workflow remains useful for tested reference configurations:

```text
Domain:     northern-germany-27km
Resolution: quick-preview
Mode:       dry-run
```

![Xaver domain and resolution](user-guide/screenshots/xaver-03-domain-resolution.png)

## 4. Plan real ERA5 boundary data

A valid guided preview can be converted into a canonical ERA5 request plan.
Refresh the data status first. The panel reports:

- whether Copernicus CDS credentials are configured;
- whether the managed cache is writable;
- how many content-addressed plans exist;
- whether a guided preview is available.

Select **Plan real ERA5 data**. The browser sends:

```http
POST /api/data/era5/plan
Content-Type: application/json

{
  "source": "latest-wizard-preview",
  "interval_hours": 1,
  "margin_degrees": 1
}
```

For the reference period, the plan contains pressure-level and single-level
requests for 5 and 6 December 2013, with 19 hourly boundary times including both
endpoints. The UI shows request count, estimated size, cache coverage, stable plan
key and provenance. It must state:

```text
Artificial weather data: no
```

![Xaver ERA5 request plan with cache and provenance information](user-guide/screenshots/xaver-03c-era5-data-plan.png)

Select **Prepare download files** to atomically persist:

```text
.era5-cache/<plan-key>/era5-plan.json
.era5-cache/<plan-key>/request.json
.era5-cache/<plan-key>/era5-download-config.json
```

Preparation does not contact CDS and returns `download_started: false`.

## 5. Download and verify real ERA5 files

Select **Start real ERA5 download** only after reviewing the plan:

```http
POST /api/data/era5/downloads
```

The downloader is a persistent background process rather than an open HTTP
request. Its states are:

```text
QUEUED
RUNNING
CANCELLING
CANCELLED
FAILED
SUCCEEDED
```

The UI reports completed requests, current request, retry attempt and recent
structured events. A cancelled or failed job can reuse already verified cache
files.

After successful verification, the plan directory contains:

```text
checksums.json
provenance.json
files/...
downloads/<download-id>/era5-manifest.json
```

A complete cache can be verified without credentials. Missing files require a
local `CDSAPI_KEY` or `.cdsapirc`; secret values never belong in browser responses,
job JSON or command arguments.

See [`ERA5_DATA_UI.md`](ERA5_DATA_UI.md) and
[`ERA5_DATA_PLANNING.md`](ERA5_DATA_PLANNING.md).

## 6. Inspect the managed ERA5 cache

The global cache view lists each plan with:

- stored bytes and regular-file count;
- request coverage and partial entries;
- period and geographic bounds;
- source, datasets and checksum/provenance availability;
- creation, modification and last-use timestamps;
- dependent download jobs and active-state protection.

The browser reads:

```http
GET /api/data/era5/cache
GET /api/data/era5/cache/{plan-key}
```

Deletion is rejected while a dependent download is active. A confirmed deletion
includes the exact plan key and dependency snapshot:

```http
POST /api/data/era5/cache/{plan-key}/delete
Content-Type: application/json

{
  "confirm_plan_key": "<same plan key>",
  "dependent_job_ids": ["<exact IDs from the detail response>"]
}
```

See [`ERA5_CACHE_MANAGEMENT.md`](ERA5_CACHE_MANAGEMENT.md).

## 7. Preview and run the dry-run workflow

The curated workflow previews a generated job through:

```http
POST /api/jobs/preview
```

![Xaver generated config](user-guide/screenshots/xaver-04-preview-config.png)

Select **Start dry-run** or **Start planned dry-run**. A dry-run validates and
records intended workflow steps but does not download data or execute WPS/WRF.

![Xaver dry-run status](user-guide/screenshots/xaver-05-dry-run-status.png)

Logs are read through:

```http
GET /api/jobs/{id}/logs
```

![Xaver dry-run logs](user-guide/screenshots/xaver-06-logs.png)

These screenshots are intentionally not weather maps.

## 8. Persistent simulation records

A real immutable pipeline specification can be used to create a persistent
simulation record. Creation and execution intent remain separate:

```text
READY → QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED
```

The queue/history view can show frozen inputs, runtime identities, ordered steps,
events, artifacts and resource measurements. Queue state alone never implies that
WRF has executed.

## 9. Generate a computed weather-map screenshot

This step is separate from regular screenshot CI. It requires an existing real
visualization directory with:

```text
metadata.json
layers/
```

Run:

```bash
sh ci/generate-real-data-weather-map-screenshot.sh \
  workbench-runs/xaver-real/visualizations \
  max_wind10m
```

The command refuses to create documentation output unless:

- `metadata.json` declares `provenance.mode = "wrf"`;
- at least one originating `wrfout` file is recorded;
- the requested layer is a safe regular file;
- the layer contains a spatially varying numeric field;
- the browser canvas contains a sufficiently large, opaque and non-uniform render.

Only then is this file created:

```text
doc/user-guide/screenshots/xaver-07-weather-map.png
```

Commit it only after reviewing that it shows plausible meteorological structure.
A real-data screenshot is not a full scientific validation of the simulation.

At the time a checkout lacks `xaver-07-weather-map.png`, the honest interpretation
is: **no reviewed real WRF result image has been committed yet**. The other
screenshots remain useful documentation, but they do not substitute for that
result.

## 10. Updating screenshots

For a normal UI change:

1. Run `sh ci/generate-user-guide-screenshots.sh`.
2. Review every generated PNG, especially visible map geography.
3. Commit intentional screenshot changes with the UI and guide changes.

For a computed weather-result change:

1. Complete the real-data pipeline.
2. Verify checksums and WRF provenance.
3. Run the separate real-data screenshot command.
4. Review the rendered field for nonblank, nonregular meteorological structure.
5. Commit `xaver-07-weather-map.png` with its provenance and run documentation.

CI uploads regular screenshots as review artifacts. It does not modify pull
request branches and it does not manufacture a real weather result.
