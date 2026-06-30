# WRF Workbench

The Workbench is the user-facing layer that wraps the existing WRF/WPS Docker
pipeline components behind a single, reproducible entrypoint.  It turns a JSON
configuration file into a reproducible job run with structured output and status
tracking.

---

## Quick start

```bash
# 1. Clone the repository
 git clone https://github.com/carstenartur/wrf-chammer.git
 cd wrf-chammer

# 2. Start the local API and web UI
python3 -m workbench.server.server --host 127.0.0.1 --port 8080
```

Then open:

```text
http://127.0.0.1:8080/
```

For the complete Storm Xaver acceptance walkthrough, see:

```text
doc/XAVER_DEMO.md
```

---

## Prerequisites

| Mode | Requirement |
|---|---|
| `dry-run` | Python 3 (stdlib only) |
| `era5-offline` | Python 3 (stdlib only) |
| `era5-download-only` | Python 3, CDS credentials if data is not cached |
| `era5-wrf` | Python 3 for cached mode; WPS/WRF binaries for manual real mode |
| `wrf-smoke` | Docker, `wrf-reproducible:latest` image built locally |

Build the WRF image for `wrf-smoke` mode:

```bash
docker build -f Dockerfile -t wrf-reproducible:latest .
```

---

## Supported modes

| Mode | Description |
|---|---|
| `dry-run` | Validates the config and prints the planned pipeline steps. Nothing is executed. |
| `wrf-smoke` | Runs the WRF idealized quarter-circle mountain smoke test via `wrf-reproducible:latest`. |
| `era5-offline` | Runs ERA5 download-script and WPS-preparation offline checks using pre-seeded dummy data. |
| `era5-download-only` | Invokes the ERA5 download/cache pipeline. |
| `era5-wrf` | Builds the ERA5 → WPS → WRF pipeline path, with a cacheable CI path and a documented manual real-run path. |

---

## Job configuration format

Each job is described by a JSON file.  The full schema is in
[`config/schema.json`](config/schema.json).

### Minimal required fields

```json
{
  "id": "xaver-dry-run",
  "mode": "dry-run",
  "name": "Storm Xaver Demo",
  "period": {
    "start": "2013-12-05T00:00:00Z",
    "end":   "2013-12-06T00:00:00Z"
  },
  "domain": {
    "label":      "northern-germany",
    "center_lat": 54.0,
    "center_lon": 9.0,
    "dx_km":      9,
    "dy_km":      9,
    "e_we":       50,
    "e_sn":       50
  },
  "inputs": {
    "source": "era5"
  },
  "outputs": {
    "directory": "workbench-runs/xaver-dry-run"
  }
}
```

### Field reference

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | ✓ | Unique job identifier (lowercase letters, digits, hyphens) |
| `mode` | string | ✓ | One of: `dry-run`, `wrf-smoke`, `era5-offline`, `era5-download-only`, `era5-wrf` |
| `name` | string | ✓ | Human-readable job name |
| `period.start` | string | ✓ | Simulation start (ISO 8601 UTC, e.g. `2013-12-05T00:00:00Z`) |
| `period.end` | string | ✓ | Simulation end (ISO 8601 UTC, must be after `start`) |
| `domain.label` | string | ✓ | Human-readable domain name |
| `domain.center_lat` | number | ✓ | Domain center latitude in degrees north (-90 to 90) |
| `domain.center_lon` | number | ✓ | Domain center longitude in degrees east (-180 to 180) |
| `domain.dx_km` | number | ✓ | Grid spacing west-east (km, > 0) |
| `domain.dy_km` | number | ✓ | Grid spacing south-north (km, > 0) |
| `domain.e_we` | integer | ✓ | Grid points west-east (>= 3) |
| `domain.e_sn` | integer | ✓ | Grid points south-north (>= 3) |
| `inputs.source` | string | ✓ | `era5` for real-data runs, `none` for idealized runs |
| `inputs.era5.config` | string | for `era5-download-only` and `era5-wrf` | ERA5 request/cache config |
| `outputs.directory` | string | ✓ | Run directory path (relative paths are anchored at the repository root) |

---

## Example configurations

| File | Mode | Event |
|---|---|---|
| [`examples/xaver-dry-run.json`](examples/xaver-dry-run.json) | `dry-run` | Storm Xaver (2013-12-05) |
| [`examples/xaver-era5-wrf.json`](examples/xaver-era5-wrf.json) | `era5-wrf` | Storm Xaver cached/real pipeline path |
| [`examples/kyrill-dry-run.json`](examples/kyrill-dry-run.json) | `dry-run` | Storm Kyrill (2007-01-18) |
| [`examples/wrf-smoke.json`](examples/wrf-smoke.json) | `wrf-smoke` | WRF idealized quarter-circle test |
| [`examples/era5-offline.json`](examples/era5-offline.json) | `era5-offline` | ERA5 offline validation |
| [`examples/era5-download-only.json`](examples/era5-download-only.json) | `era5-download-only` | ERA5 download/cache check |

---

## Event catalogue

Pre-defined weather events with suggested domains and outputs are in
[`events/catalogue.json`](events/catalogue.json).

Current catalogue entries:

| Key | Event | Period |
|---|---|---|
| `xaver` | Storm Xaver | 2013-12-05 - 2013-12-06 |
| `kyrill` | Storm Kyrill | 2007-01-18 - 2007-01-19 |
| `custom-template` | Custom simulation template | User-defined |

---

## Output directory layout

After a successful run the following structure is created under
`outputs.directory`:

```text
workbench-runs/<job-id>/
  job.json
  status.json
  logs/workbench.log
  outputs/
  visualizations/        # when postprocessing ran
```

### `status.json` fields

| Field | Description |
|---|---|
| `job_id` | Job identifier from the config |
| `mode` | Execution mode |
| `status` | `running` -> `succeeded` or `failed` |
| `start_time` | ISO 8601 UTC timestamp when the run started |
| `end_time` | ISO 8601 UTC timestamp when the run finished |
| `exit_code` | Exit code of the mode script (0 = success) |
| `error` | Error description if the job failed, otherwise `null` |

---

## Running with Docker Compose

A minimal `docker-compose.yml` is provided at the repository root for running the
Workbench inside a container:

```bash
# Dry-run
docker compose run --rm workbench workbench/examples/xaver-dry-run.json

# ERA5 offline validation
docker compose run --rm workbench workbench/examples/era5-offline.json
```

Run directories are written to `./workbench-runs/` on the host.

---

## Validation

The `validate.py` script can be used standalone to check a config without
executing it:

```bash
python3 workbench/validate.py workbench/examples/xaver-dry-run.json
```

It exits with status 0 on success and prints all validation errors on failure.

---

## Relation to existing repository components

| Component | Location | Role in Workbench |
|---|---|---|
| Local API / UI server | `workbench/server/server.py` | Serves the local API and web UI |
| Web UI | `workbench/web/` | Event-to-simulation browser flow |
| WRF smoke-test script | `ci/smoke-test-wrf.sh` | Called by `wrf-smoke` mode |
| ERA5 offline test suite | `ci/test-era5-offline.sh` | Called by `era5-offline` mode |
| ERA5 download script | `ci/download-era5.py` | Called by `era5-download-only` and `era5-wrf` modes |
| ERA5-WRF pipeline doc | `doc/ERA5_WRF_PIPELINE.md` | Manual real-run path and cached CI path |
| Xaver demo doc | `doc/XAVER_DEMO.md` | End-to-end acceptance scenario |
| WRF Docker image | `Dockerfile` | Used by `wrf-smoke` mode |
| WPS Docker image | `Dockerfile.wps` | Used by WPS-related build/test paths |
| Architecture design | `doc/ARCHITECTURE.md` | Describes the long-term platform |

---

## Known limitations

- The current MVP uses shell scripts for orchestration. A queue system and
  persistent backend are described in `doc/ARCHITECTURE.md` but not yet fully
  implemented.
- The `wrf-smoke` mode requires the `wrf-reproducible:latest` Docker image to be
  built locally; it is not published to a registry.
- The default `era5-wrf` CI path is cacheable/offline and verifies orchestration,
  namelists, metadata and visualization handoff. Full scientific validation
  requires a real manual WPS/WRF run and comparison against observations or
  reference data.
- The Workbench does not depend on NCAR/derecho or any HPC infrastructure.
