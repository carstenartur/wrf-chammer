# CI Architecture

This document describes the continuous integration and delivery pipelines for
this fork of WRF.  The fork CI runs entirely on GitHub-hosted runners and does
not require access to NCAR infrastructure.

---

## Overview

| Workflow | File | Trigger | Runner | Purpose |
|---|---|---|---|---|
| Fork CI | `ci.yml` | push / PR | `ubuntu-latest` | Shell script validation |
| Docker Reproducible Build | `docker-build.yml` | push / PR | `ubuntu-latest` | WRF Docker image + smoke test |
| Docker WPS Reproducible Build | `docker-wps-build.yml` | push / PR | `ubuntu-latest` | WPS Docker image |
| ERA5 Offline Dry Run | `era5-offline-dry-run.yml` | push / PR | `ubuntu-latest` | ERA5 script validation (no credentials) |
| ERA5/WPS Integration Test | `wps-integration-test.yml` | push / PR | `ubuntu-latest` | End-to-end WPS pipeline test |
| Workbench MVP Tests | `workbench-tests.yml` | push / PR | `ubuntu-latest` | Workbench script tests |
| Visualization MVP Tests | `visualization-tests.yml` | push / PR | `ubuntu-latest` | Visualization script tests |
| ERA5 Download Pipeline | `docker-era5-pipeline.yml` | manual only | `ubuntu-latest` | Live ERA5 download (requires `CDSAPI_KEY`) |
| HPC Regression Tests | `hpc-regression.yml` | manual / label | self-hosted (NCAR) | Upstream compilation regression suite |

---

## Default fork CI (runs on every push and pull request)

The following workflows run automatically on every push to `master`/`develop`
and on every pull request opened, updated, or reopened.  They use only
GitHub-hosted runners and do not require any secrets.

### Fork CI (`ci.yml`)

**Trigger**: push to `master`/`develop`; pull_request (opened, synchronize, reopened); workflow_dispatch

**Jobs**:
- `shellcheck` — Runs [ShellCheck](https://www.shellcheck.net/) with
  `--severity=error` on all fork CI shell scripts under `ci/`, `workbench/`,
  and `visualization/`.  Catches shell scripting errors before they reach
  runtime.

**Expected runtime**: < 1 minute

---

### Docker Reproducible Build (`docker-build.yml`)

**Trigger**: push to `master`/`develop`; pull_request; workflow_dispatch

**Jobs**:
- Builds the `wrf-reproducible` Docker image from `Dockerfile`.
- Verifies runtime dependencies inside the container.
- Runs the WRF idealized quarter-circle mountain smoke test.
- Runs the WRF smoke test via the Workbench runner.

**Diagnostics**: Smoke test stdout/stderr is captured to `/tmp/smoke-test-*.log`
and uploaded as a workflow artifact on failure.

**Expected runtime**: up to 120 minutes on first run (compiles WRF from source);
significantly faster with the Docker layer cache warm.

---

### Docker WPS Reproducible Build (`docker-wps-build.yml`)

**Trigger**: push to `master`/`develop`; pull_request; workflow_dispatch

**Jobs**:
- Builds `wps-reproducible` from `Dockerfile.wps`.
- Verifies WPS runtime dependencies inside the container.

**Expected runtime**: up to 180 minutes on first run (compiles WRF + WPS from
source); significantly faster with Docker layer cache.

---

### ERA5 Offline Dry Run (`era5-offline-dry-run.yml`)

**Trigger**: push to `master`/`develop`; pull_request; workflow_dispatch

**Jobs**:
- Runs `ci/test-era5-offline.sh` which validates ERA5 configuration files,
  manifest generation, and dummy-GRIB detection without network access or CDS
  credentials.

**Expected runtime**: < 10 minutes

---

### ERA5/WPS Integration Test (`wps-integration-test.yml`)

**Trigger**: push to `master`/`develop`; pull_request; workflow_dispatch

**Jobs**:
- Builds `wps-reproducible` (with Docker layer cache).
- Builds `era5-pipeline` on top of `wps-reproducible`.
- Runs `ci/test-era5-wps-integration.sh` which executes `ungrib.exe` and
  `metgrid.exe` inside the container with `--network=none`, then verifies the
  generated `met_em` output.

**Diagnostics**: WPS working directory is tarred and uploaded as a workflow
artifact on failure.

**Expected runtime**: up to 120 minutes on first run; much faster with cache.

---

### Workbench MVP Tests (`workbench-tests.yml`)

**Trigger**: push to `master`/`develop`; pull_request; workflow_dispatch

**Jobs**:
- Runs `ci/test-workbench.sh` which exercises all Workbench modes using local
  dummy data (no Docker or CDS credentials required).

**Expected runtime**: < 15 minutes

---

### Visualization MVP Tests (`visualization-tests.yml`)

**Trigger**: push to `master`/`develop`; pull_request; workflow_dispatch

**Jobs**:
- Runs `visualization/tests/test_visualization.sh` which validates
  postprocessing scripts and web-viewer output using Python stdlib only.

**Expected runtime**: < 10 minutes

---

## Optional / manual workflows

### ERA5 Download Pipeline (`docker-era5-pipeline.yml`)

**Trigger**: workflow_dispatch only

**Purpose**: Live ERA5 data download using the Copernicus CDS API.

**Required secrets**:
- `CDSAPI_KEY` — Copernicus CDS API key.  Request one at
  <https://cds.climate.copernicus.eu/>.
- `CDSAPI_URL` *(optional)* — Override the default CDS endpoint.

**Inputs** (provided at dispatch time):
- `cache_namespace` — Cache key namespace for downloaded ERA5 files.
- `config_path` — Path (in the repo) to the ERA5 JSON request file.

**Expected runtime**: up to 3 hours depending on CDS queue and request size.

---

### HPC Regression Tests (`hpc-regression.yml`)

**Trigger**: workflow_dispatch; pull_request with `compile-tests` or
`all-tests` label applied by a maintainer with access to NCAR runners.

**Purpose**: Upstream NCAR HPC compilation and regression test suite.  Requires
self-hosted runners and NCAR-specific infrastructure not available to external
contributors.

**Required infrastructure**:
- `derecho` self-hosted runner registered with this repository.
- GLADE archive storage (`/glade/work/…`).
- NCAR HPC account (e.g. `NMMM0012`).

**Expected runtime**: up to 5 days (HPC queue-dependent).

---

## Secrets reference

| Secret | Used by | Required | Description |
|---|---|---|---|
| `CDSAPI_KEY` | `docker-era5-pipeline.yml` | Yes | Copernicus CDS API key |
| `CDSAPI_URL` | `docker-era5-pipeline.yml` | No | Override CDS endpoint URL |

No secrets are required to run the default fork CI.

---

## Relationship between workflows

```
push / pull_request
  ├── ci.yml                    (shellcheck)
  ├── docker-build.yml          (WRF image + smoke test)
  ├── docker-wps-build.yml      (WPS image)
  ├── era5-offline-dry-run.yml  (ERA5 config validation)
  ├── wps-integration-test.yml  (end-to-end WPS pipeline)
  ├── workbench-tests.yml       (workbench scripts)
  └── visualization-tests.yml  (visualization scripts)

workflow_dispatch
  └── docker-era5-pipeline.yml  (live ERA5 download)

workflow_dispatch / PR label (compile-tests | all-tests)
  └── hpc-regression.yml        (NCAR HPC compilation tests)
        └── test_workflow.yml   (reusable HPC runner logic)
```

---

## Adding a new workflow

1. Place it under `.github/workflows/`.
2. Use `runs-on: ubuntu-latest` unless NCAR infrastructure is explicitly needed.
3. Add it to the table at the top of this document.
4. If it requires secrets, add them to the secrets reference table.
