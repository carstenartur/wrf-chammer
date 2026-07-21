# Real Xaver persistent acceptance

This manual acceptance is the first path that may establish a documented real meteorological result for Storm Xaver. It runs the same persistent job, store, worker and eight executor steps used by the Workbench. It has no fixture, generated-weather or dry-run fallback.

## What this acceptance proves

A successful report proves that one job completed:

```text
verified real ERA5 input
→ geogrid
→ ungrib
→ metgrid
→ real.exe
→ wrf.exe
→ WRF-input postprocessing
→ checksum result indexing
```

It also proves that:

- all input files still match their recorded SHA-256 and size;
- ERA5 provenance explicitly states `artificial_weather_data: false`;
- the WPS geography directory is real, non-empty and not a symbolic link;
- local WPS, WRF and postprocessing images match the pinned SHA-256 identities frozen into the immutable specification;
- every persistent pipeline step is `SUCCEEDED` in the expected order;
- exactly one result index is present;
- the result index references the same specification, Git revision and ERA5 plan;
- visualization metadata declares `provenance.mode = wrf` and lists originating `wrfout` files;
- every indexed product still matches its SHA-256 and byte size.

A successful technical acceptance does not by itself prove that every meteorological field is scientifically correct. Scientific plausibility, comparison with observations and interpretation remain separate review tasks.

## Prerequisites

The workflow requires a dedicated self-hosted GitHub Actions runner labelled:

```text
self-hosted
linux
x64
wrf-chammer-real
```

The runner must provide:

- Docker;
- Python 3 and Git;
- enough RAM, disk and execution time for the chosen domain;
- a complete, checksum-verified real ERA5 cache;
- a real WPS geography archive;
- permission to build and run the three local runtime images.

The workflow deliberately does not run on GitHub-hosted runners, pull requests or pushes.

## Prepare the ERA5 plan

Use the regular Workbench planning and download controls. The selected plan directory must contain:

```text
era5-plan.json
checksums.json
provenance.json
files/...
```

The plan must be `complete`; its start and end timestamps must equal the acceptance request. Extra canonical fields such as `interval_hours` and `time_points` are allowed.

## Run through GitHub Actions

Open **Actions → Real Xaver Persistent Acceptance → Run workflow** and provide:

- the 64-character ERA5 plan key;
- the absolute cache root on the runner;
- the absolute real `WPS_GEOG` directory;
- the UTC start/end timestamps;
- west, south, east and north bounds;
- the desired domain quality profile.

The workflow:

1. checks out the exact revision with a clean worktree;
2. verifies self-hosted prerequisites;
3. builds WPS, WRF and postprocessing images;
4. resolves and validates each local image ID;
5. creates a schema-valid planning preview with `dry-run` as the preview mode and `requested_execution_mode: era5-wrf` as the immutable real-run intent;
6. revalidates the complete ERA5 cache;
7. freezes a real immutable pipeline specification;
8. creates and queues a persistent simulation record;
9. runs the standard `SimulationWorker` with `pipeline_container_executor.py`;
10. verifies the final result index and every product;
11. writes JSON and Markdown evidence.

## Evidence

On success, the workflow uploads:

```text
report.json
report.md
job-id.txt
results/
visualizations/
```

The report includes:

- job and specification IDs;
- exact source revision;
- domain and period;
- ERA5 plan key;
- pinned runtime identities;
- all eight step states, attempts, timestamps and progress;
- resource measurements;
- WRF output provenance;
- every verified indexed product.

On failure, structured step result documents and local step logs are uploaded. A failure never creates an accepted report.

## Local invocation

The same command can be run directly on an appropriately prepared machine:

```bash
python3 ci/run-real-xaver-acceptance.py \
  --cache-root /srv/wrf-chammer/era5-cache \
  --plan-key <64-character-plan-key> \
  --geog-root /srv/wrf-chammer/WPS_GEOG \
  --start 2013-12-05T12:00:00Z \
  --end 2013-12-06T06:00:00Z \
  --west 2.0 --south 51.0 --east 13.0 --north 58.0 \
  --quality-profile balanced \
  --pipeline-profile small-real-data-demo
```

The environment must contain the three image reference/identity pairs documented by the workflow. The command normally requires a clean checkout so the report is reproducible.

## Weather-map screenshot

Only after this acceptance succeeds should a documentation weather map be generated from the accepted `visualizations/` directory:

```bash
sh ci/generate-real-data-weather-map-screenshot.sh \
  workbench-runs/simulations/<job-id>/visualizations \
  max_wind10m
```

That screenshot test independently requires WRF provenance, a spatially varying numeric layer and a visibly nonblank canvas. The OpenStreetMap/Natural-Earth planning map does not depend on this acceptance and remains a separate geographic selection view.

## Current status

Merging this workflow makes the real acceptance reproducible and reviewable. It does not mean that a real run has already succeeded. Issue #37 remains open until a self-hosted run produces accepted evidence and the reviewed real-weather screenshot.
