# CI Architecture

This document describes the continuous integration and delivery pipelines for
this fork of WRF. Fork CI runs on GitHub-hosted runners and does not require
NCAR infrastructure unless a maintainer explicitly starts the optional HPC
regression workflow.

GitHub Actions is an invocation layer, not the test implementation. Build and
integration logic remains in Dockerfiles and repository scripts so it can be
run from any ordinary checkout.

---

## Principles

1. **Local first** — every automated test has a documented repository command.
2. **Change-aware** — expensive WRF/WPS compilations run only when relevant
   runtime, model, build, integration fixture, or invocation files change.
3. **Fast feedback first** — Workbench, visualization, shell, and offline tests
   report independently from long model compilations.
4. **No obsolete heavy work** — each expensive workflow has a per-ref
   concurrency group with `cancel-in-progress: true`.
5. **Real-data tests are explicit** — credentialed or long-running real-data
   workflows remain manual or separately scheduled.

---

## Workflow overview

| Workflow | File | Automatic scope | Local equivalent |
|---|---|---|---|
| WRF Chammer ShellCheck | `wrf-chammer-shellcheck.yml` | Relevant shell files | `shellcheck ...` as listed in the workflow |
| Docker Reproducible Build | `docker-build.yml` | WRF/runtime-relevant changes | `docker build -t wrf-reproducible .` plus `ci/smoke-test-wrf.sh` |
| Docker WPS Reproducible Build | `docker-wps-build.yml` | WRF/WPS/runtime-relevant changes | `docker build -f Dockerfile.wps -t wps-reproducible .` |
| ERA5 Offline Dry Run | `era5-offline-dry-run.yml` | ERA5/Workbench changes | `sh ci/test-era5-offline.sh` |
| ERA5/WPS Integration Test | `wps-integration-test.yml` | WRF/WPS/ERA5 integration changes | `sh ci/test-era5-wps-integration.sh` with the documented images |
| Workbench MVP Tests | `workbench-tests.yml` | Workbench changes | Commands in the workflow, all under `ci/` |
| Visualization MVP Tests | `visualization-tests.yml` | Visualization changes | `sh visualization/tests/test_visualization.sh` |
| User Guide Screenshots | `user-guide-screenshots.yml` | UI/server/e2e/doc changes | `sh ci/generate-user-guide-screenshots.sh` |
| ERA5 Download Pipeline | `docker-era5-pipeline.yml` | Manual only | `sh ci/run-era5-pipeline.sh` with CDS credentials |
| HPC Regression Tests | `hpc-regression.yml` | Manual / maintainer label | NCAR-specific reusable workflow |

---

## Fast automatic checks

Fast checks should normally complete before the heavy model builds and remain
useful even when no Docker daemon or external credentials are available.

### WRF Chammer ShellCheck

Validates fork-owned shell scripts. ShellCheck invocation remains visible in the
workflow so the same command can be run locally.

### Workbench MVP Tests

Runs catalogue, validation, lifecycle CLI, API, web UI, ERA5 pipeline, and Xaver
demo tests through scripts under `ci/`. These are repository tests and do not
depend on GitHub-specific APIs.

### Visualization MVP Tests

Runs:

```bash
sh visualization/tests/test_visualization.sh
```

### ERA5 Offline Dry Run

Runs:

```bash
sh ci/test-era5-offline.sh
```

It validates configuration, manifests, and dummy-fixture rejection without CDS
credentials or a live data download.

### User Guide Screenshots

Runs the browser flow in Testcontainers and uploads generated screenshots and UI
assets for review. The workflow is read-only and does not modify pull-request
branches.

---

## Change-aware heavy checks

The following checks compile WRF or WPS and may take a substantial amount of
time on a cold runner. They use `paths-ignore` only for well-defined fork-owned
areas that cannot affect their runtime path, such as:

```text
doc/**
workbench/ui/**
workbench/web/**
workbench/e2e/**
workbench/server/**
visualization/**
```

Individual planner/readiness files and their tests are also ignored. Changes to
WRF source, build configuration, Dockerfiles, WPS/ERA5 integration files,
Workbench runtime scripts, or the heavy workflow files themselves still trigger
the relevant build.

A pull request that changes both ignored UI files and a relevant runtime file
still runs the heavy workflow.

### Docker Reproducible Build

Local build and verification:

```bash
docker build --tag wrf-reproducible .
docker run --rm wrf-reproducible /usr/local/bin/verify-wrf-runtime.sh
docker run --rm wrf-reproducible /usr/local/bin/smoke-test-wrf.sh
sh workbench/run.sh workbench/examples/wrf-smoke.json
```

The workflow uploads concise build diagnostics on failure.

### Docker WPS Reproducible Build

Local build and verification:

```bash
docker build -f Dockerfile.wps --tag wps-reproducible .
docker run --rm wps-reproducible /usr/local/bin/verify-wps-runtime.sh
```

### ERA5/WPS Integration Test

The workflow builds `wps-reproducible` and `era5-pipeline`, then runs:

```bash
ERA5_IMAGE=era5-pipeline:latest \
WORKDIR=/tmp/wps-integration-workdir \
sh ci/test-era5-wps-integration.sh
```

The WPS execution itself uses the bundled mini GRIB input and runs without
network access. Network access is needed while building the image to obtain the
pinned upstream WPS source and packages.

---

## Concurrency policy

Each heavy workflow groups runs by `github.ref` and cancels an older in-progress
run when a newer commit arrives on the same pull request or branch.

This does not weaken validation: only the newest commit is relevant for merge.
It prevents several obsolete WRF/WPS compilations from consuming runners in
parallel during review iterations.

---

## Optional and manual workflows

### ERA5 Download Pipeline

**Trigger:** `workflow_dispatch`

Requires:

- `CDSAPI_KEY`
- optional `CDSAPI_URL`

The equivalent repository pipeline is documented in `doc/ERA5_WRF_PIPELINE.md`.
A manual workflow is used because live CDS traffic is credentialed, potentially
large, and not appropriate for every pull request.

### HPC Regression Tests

The upstream-style HPC suite requires the `derecho` self-hosted runner, NCAR
accounting, and GLADE storage. It is intentionally separate from the portable
fork tests.

---

## Trigger validation

Changes to workflow selection should be verified with two controlled pull
requests or branches:

1. **UI/docs-only change**
   - Workbench/UI/screenshot checks run.
   - Docker WRF, Docker WPS, and ERA5/WPS integration do not start.
2. **Runtime-relevant change**
   - the corresponding heavy workflows start.
   - a second commit cancels obsolete runs for the same ref.

`workflow_dispatch` remains available for explicit verification even when path
filters would skip an automatic run.

---

## Secrets reference

| Secret | Used by | Required | Description |
|---|---|---|---|
| `CDSAPI_KEY` | `docker-era5-pipeline.yml` | Yes | Copernicus CDS API key |
| `CDSAPI_URL` | `docker-era5-pipeline.yml` | No | Override CDS endpoint URL |

No secrets are required for the portable automatic fork tests.

---

## Adding a workflow

1. Put test logic in a repository script, container, or test module first.
2. Add a thin workflow under `.github/workflows/`.
3. Document the exact local equivalent.
4. Use path filters appropriate to the cost and dependencies of the job.
5. Add per-ref concurrency for expensive jobs.
6. Keep real-data, credentialed, or HPC-only tests explicitly separated.
7. Update this document and the workflow overview table.
