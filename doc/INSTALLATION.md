# WRF Workbench installation and local startup

The Workbench has a single lifecycle command for the local API and browser UI.
It uses only Python's standard library for startup and readiness checks. Docker is
required only when a real WPS/WRF runtime is needed.

## Supported local environment

Initial support targets a current Linux system with:

- Python 3.10 or newer,
- enough writable disk space for `workbench-runs/`,
- Docker Engine or Docker Desktop for real WPS/WRF jobs,
- CDS credentials for downloading real ERA5 data.

The readiness screen reports missing optional components instead of preventing a
simple dry-run UI startup.

## Start

From the repository root:

```bash
python3 wrf-chammer start
```

The command starts the local application in the background and waits until the
health endpoint responds. Open:

```text
http://127.0.0.1:8080/
```

On a checkout where the executable bit is preserved, the shorter equivalent is:

```bash
./wrf-chammer start
```

Useful options:

```bash
python3 wrf-chammer start --port 8090
python3 wrf-chammer start --open
python3 wrf-chammer start --json
```

## Check readiness

```bash
python3 wrf-chammer doctor
python3 wrf-chammer doctor --json
```

The checks cover:

- Python version,
- CPU count,
- total RAM,
- free disk space,
- writable Workbench run directory,
- Docker CLI and daemon,
- local WRF and WPS images,
- ERA5/CDS credential presence.

Each check has one of these states:

```text
ready
warning
error
```

Warnings do not prevent the dry-run workflow. Errors indicate that a real WPS/WRF
job cannot safely run in the current environment.

The same information is available from:

```http
GET /api/readiness
```

and is displayed at the top of the browser UI.

## Status, logs, and stop

```bash
python3 wrf-chammer status
python3 wrf-chammer logs
python3 wrf-chammer logs --lines 250
python3 wrf-chammer stop
```

Runtime state and the local server log are stored under:

```text
workbench-runs/.runtime/
```

## Build or refresh runtime images

```bash
python3 wrf-chammer update-images
```

This builds:

```text
wrf-reproducible:latest
wps-reproducible:latest
```

The images are currently built locally. Publishing pinned release images is a
later part of issue #42; real jobs must record the image identifier used.

## Developer-compatible server command

The readiness-aware application can also be run in the foreground:

```bash
python3 -m workbench.server.application --host 127.0.0.1 --port 8080
```

The older `workbench.server.server` module remains available for compatibility,
but the application module is now the primary user-facing entrypoint.
