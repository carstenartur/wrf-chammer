# WRF Workbench User Guide

This guide walks through the local Workbench UI using screenshots generated from
the Storm Xaver browser flow.

Generate or refresh the regular UI screenshots with:

```bash
sh ci/generate-user-guide-screenshots.sh
```

The same script runs in CI, regenerates the checked-in UI images and also uploads
review artifacts. The screenshots are stored in:

```text
doc/user-guide/screenshots/
```

## 1. Start the local Workbench

From the repository root:

```bash
python3 -m workbench.server.server --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080/
```

The start screen shows the event search, preset selection panel, job preview
panel and status/log panel.

![Xaver search screen](user-guide/screenshots/xaver-01-search.png)

## 2. Search and select Storm Xaver

The UI starts with `Xaver` as a useful demo query. Click `Select xaver` to load
its event details from the Workbench event catalogue.

The browser does not parse event files directly; it calls:

```http
GET /api/events?q=xaver
GET /api/events/xaver
```

![Xaver event selected](user-guide/screenshots/xaver-02-event-selected.png)

## 3. Choose domain and resolution presets

Select a domain and a resolution preset. The first UI version shows an
approximate rectangular domain preview so users can see that the selected event
has a concrete simulation area.

For the Xaver demo, the screenshot flow uses:

```text
Domain:     northern-germany-27km
Resolution: quick-preview
Mode:       dry-run
```

![Xaver domain and resolution](user-guide/screenshots/xaver-03-domain-resolution.png)

## 4. Preview the generated job config

Click `Preview job config`.

The UI calls:

```http
POST /api/jobs/preview
```

The server delegates event lookup and job config generation to Workbench core
logic. The browser only renders the returned config and validation status.

![Xaver generated config](user-guide/screenshots/xaver-04-preview-config.png)

## 5. Start the dry-run

Click `Start dry-run`.

The UI calls:

```http
POST /api/jobs
```

The local server executes the Workbench runner in a server-managed run
directory. When the run completes, the status panel shows the job id, status,
run directory, logs and output count.

![Xaver dry-run status](user-guide/screenshots/xaver-05-dry-run-status.png)

## 6. Inspect logs

The UI fetches logs through:

```http
GET /api/jobs/{id}/logs
```

For the dry-run path, the logs show the planned WRF workflow steps without
starting containers or downloading data.

![Xaver dry-run logs](user-guide/screenshots/xaver-06-logs.png)

## 7. Inspect real computed weather-map results

Weather-map documentation screenshots must be generated from real WRF
visualization artifacts. The guide does not use artificial fields as stand-ins
for result maps.

After a real run has produced visualization artifacts containing `metadata.json`
and `layers/`, capture the weather-map screenshot with:

```bash
sh ci/generate-real-data-weather-map-screenshot.sh \
  workbench-runs/xaver-real/visualizations \
  max_wind10m
```

This creates:

```text
doc/user-guide/screenshots/xaver-07-weather-map.png
```

Commit that PNG only if it was generated from real visualization artifacts. If no
real-data screenshot is committed yet, this guide intentionally omits the weather
map image.

## 8. Run the cached ERA5-WRF acceptance path

The browser UI currently drives the dry-run path. The cacheable ERA5-WRF path is
covered by the Xaver acceptance script:

```bash
sh ci/test-xaver-demo.sh
```

That test verifies that the same Xaver scenario can create WPS/WRF namelists,
pipeline metadata and visualization metadata using cached ERA5 input.

For details, see:

```text
doc/XAVER_DEMO.md
doc/ERA5_WRF_PIPELINE.md
```

## Updating screenshots

When the UI changes intentionally:

1. Run `sh ci/generate-user-guide-screenshots.sh` locally.
2. Review the PNG files in `doc/user-guide/screenshots/`.
3. Commit the updated UI screenshots together with the UI/user-guide change.

When a real-data visualization changes intentionally, run the separate real-data
weather-map screenshot script and commit `xaver-07-weather-map.png` only if it
comes from real visualization artifacts.

CI also uploads generated regular UI screenshots as artifacts so reviewers can
compare the current browser output before merging changed images.
