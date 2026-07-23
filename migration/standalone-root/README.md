# WRF Chammer Workbench

A local, reproducibility-focused workbench for planning, running, observing, inspecting, and reproducing real WRF simulations.

> This repository contains the WRF Chammer product. WRF and WPS are scientific runtime dependencies and are not vendored into the product source tree.

## Current status

The Workbench provides:

- guided event, period, domain, resolution, and output planning;
- ERA5 credential checks, download planning, verified cache management, and provenance;
- immutable WPS/WRF run specifications with resolved namelists;
- persistent simulation jobs, queueing, worker recovery, cancellation, retry, and resource preflight;
- structured WPS, `real.exe`, `wrf.exe`, postprocessing, and result-indexing steps;
- reconnectable event streaming and persistent history;
- an integrated result viewer with time navigation, point queries, and exports;
- versioned run manifests, checksums, resource reports, and exact reproduction lineage.

The complete real Xaver reference run is still an acceptance milestone. Test fixtures and dry runs are not presented as scientific results.

## Intended installation model

Normal users install the Workbench and pull versioned, digest-pinned runtime images from GHCR. They do not compile WRF, WPS, NetCDF, or compiler toolchains locally.

The source-build path remains available for developers and audits, but it is not the default installation experience.

## Start the development checkout

Requirements for the current development path:

- Python 3.10 or newer;
- Docker Engine or Docker Desktop for real WPS/WRF execution;
- sufficient RAM and disk space;
- CDS credentials for real ERA5 downloads.

```bash
python3 wrf-chammer doctor
python3 wrf-chammer start --open
```

The Workbench is then available at `http://127.0.0.1:8080/`.

## Repository responsibilities

This product repository owns:

- CLI, API, GUI, and worker processes;
- ERA5 data management;
- simulation persistence and orchestration;
- postprocessing and visualization integration;
- setup, update, rollback, and release manifests;
- product documentation and acceptance tests.

It does not own the WRF scientific source tree. Runtime source revisions and image digests are recorded explicitly in release and run provenance.

## Documentation

- `doc/INSTALLATION.md` — current local startup and readiness path
- `doc/USER_GUIDE.md` — guided planning and execution flow
- `doc/ARCHITECTURE.md` — system architecture where available
- `doc/SIMULATION_RUN_MANIFEST.md` — reproducibility and resource evidence
- `doc/EXACT_SIMULATION_REPRODUCTION.md` — exact reproduction semantics

## Reproducibility boundary

A reproducible run records at least:

- immutable run specification;
- exact WPS and WRF namelists;
- ERA5 request and file checksums;
- WRF, WPS, postprocessing, and Workbench versions;
- runtime image digests;
- step outcomes, timestamps, resources, and artifact checksums.

Human-readable tags are useful labels, but immutable digests are the technical identity.

## Relationship to WRF

WRF Chammer Workbench is an independent product built around the Weather Research and Forecasting model. It is not the official WRF project.

During migration, runtime images may still be built from a pinned commit of the existing `carstenartur/wrf-chammer` fork. A separate upstream-diff audit determines whether a permanent minimal WRF fork is required or whether an official pinned WRF revision is sufficient.

## License

The extracted repository initially retains the source license file for continuity. Before the first standalone release, the license and third-party notices must be reviewed for the product code and all packaged runtime components.
