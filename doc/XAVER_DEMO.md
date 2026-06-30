# Storm Xaver end-to-end demo

This document is the repeatable acceptance scenario for the WRF Workbench.

For a screenshot-based walkthrough of the browser flow, see:

```text
doc/USER_GUIDE.md
```

It connects the user-facing workflow:

```text
Search for Xaver
→ inspect default period/domain/resolution
→ preview a Workbench job
→ start a dry-run
→ inspect status and logs
→ run the cached ERA5-WRF pipeline path
→ open visualization metadata
```

The demo is intentionally split into levels so that new contributors can verify
the product flow even without CDS credentials or a local WPS/WRF installation.

## Prerequisites

Required for Levels 1 and 2:

- Python 3
- a local checkout of this repository

Optional for Level 3:

- CDS credentials for ERA5 download
- local WPS binaries
- local WRF binaries
- WRF geog data

## Level 1 — UI/API dry-run demo

Start the local server:

```bash
python3 -m workbench.server.server --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080/
```

Then perform the user flow:

1. Search for `Xaver`.
2. Select the `xaver` event.
3. Confirm that the UI shows the event period and suggested outputs.
4. Select a domain preset such as `northern-germany-27km`.
5. Select a resolution preset such as `quick-preview`.
6. Click `Preview job config`.
7. Confirm that the generated config is valid.
8. Click `Start dry-run`.
9. Confirm that job status and logs appear below the form.

Equivalent API flow:

```bash
curl 'http://127.0.0.1:8080/api/events?q=xaver'
curl 'http://127.0.0.1:8080/api/events/xaver'
```

The UI uses the same API endpoints; it does not implement separate event or WRF
configuration rules in JavaScript.

## Level 2 — cached ERA5-WRF pipeline demo

This path proves the Workbench orchestration, namelist generation, ERA5 cache
handling, status/log layout and visualization handoff.

It does not claim to run a meteorologically valid WRF simulation in CI.  The
actual WPS/WRF execution path is Level 3.

Run the automated scenario:

```bash
sh ci/test-xaver-demo.sh
```

The test performs all of the following:

- starts the local Workbench API server;
- fetches the web UI from `/`;
- searches and selects Xaver through the API;
- generates a dry-run job through `/api/jobs/preview`;
- starts the dry-run through `/api/jobs`;
- verifies status and logs;
- prepares a cached ERA5 input under a temporary run directory;
- runs `era5-wrf` mode;
- verifies `namelist.wps` and `namelist.input` exist;
- verifies `outputs/pipeline-metadata.json` exists;
- verifies `visualizations/metadata.json` exists;
- verifies wind layers such as `wind10m` and `max_wind10m` are exposed.

Expected output structure:

```text
<run-dir>/
  job.json
  status.json
  logs/workbench.log
  namelists/namelist.wps
  namelists/namelist.input
  outputs/era5-manifest.json
  outputs/pipeline-metadata.json
  outputs/wrf/cached-wrf-visualization-fixture.json
  visualizations/metadata.json
  visualizations/layers/
```

## Level 3 — manual real WPS/WRF run

The manual path is documented in:

```text
doc/ERA5_WRF_PIPELINE.md
```

Use it when CDS credentials, WPS, WRF and geog data are available locally.

A successful manual real run should produce at least one:

```text
wrfout_d01_*
```

under:

```text
<run-dir>/outputs/wrf/
```

and should produce visualization artifacts under:

```text
<run-dir>/visualizations/
```

## Acceptance checklist

- [x] Xaver can be found through the local API.
- [x] Xaver can be selected in the local web UI.
- [x] Event defaults populate the job preview path.
- [x] Dry-run preview creates a valid Workbench config.
- [x] Dry-run can be started through the API path.
- [x] Status and logs can be fetched after the run.
- [x] Cached ERA5-WRF path creates WPS and WRF namelists.
- [x] Cached ERA5-WRF path creates pipeline metadata.
- [x] Cached ERA5-WRF path creates visualization metadata.
- [x] Manual real WPS/WRF path is documented.

## Scope boundaries

The demo is an acceptance scenario, not a scientific validation of Storm Xaver.
It verifies that the Workbench product path is wired together.  Scientific
validation needs a real-data Level 3 run, inspection of `wrfout_d01_*`, and
comparison against observations or reanalysis fields.
