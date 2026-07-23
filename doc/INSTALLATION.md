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

## Install a real WRF/WPS runtime release

A normal installation does not compile WRF or WPS locally. Obtain the runtime release
manifest distributed with the same Workbench release, then run:

```bash
python3 wrf-chammer images pull \
  --manifest /path/to/release-manifest.json
```

The manifest binds the exact Workbench source revision to digest-pinned WPS, WRF, and
postprocessing images. The command pulls `reference@sha256` selectors, verifies the
registry digests, and atomically activates the release.

Inspect the active runtime:

```bash
python3 wrf-chammer images status
python3 wrf-chammer images status --json
```

`update-images` remains a compatibility alias:

```bash
python3 wrf-chammer update-images \
  --manifest /path/to/release-manifest.json
```

It now pulls a verified release and never starts a local compiler build.

The repository contains `runtime/release-manifest.example.json` only as a format
example. Its zero digests are deliberately unpublished and unusable. A real release
must provide successfully built and published image digests.

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
- active runtime release integrity,
- exact WPS, WRF, and postprocessing image digests,
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

The active runtime record is stored as `runtime-images.json`. It contains only release
metadata, image references, digests, inspected image IDs, and a canonical integrity
checksum; it does not contain registry credentials.

## Developer source builds

Building WRF and WPS from source remains possible in the WRF runtime source repository
using its Dockerfiles and documented build workflows. It is an explicit developer or
audit operation, not an automatic fallback of the installed Workbench.

After a local source build, developers must create an equivalent release/activation
record from the inspected image identities before real jobs can freeze those runtimes.
Mutable `latest` tags alone are not accepted as reproducible identity.

## Developer-compatible server command

The readiness-aware application can also be run in the foreground:

```bash
python3 -m workbench.server.application --host 127.0.0.1 --port 8080
```

The older `workbench.server.server` module remains available for compatibility,
but the application module is now the primary user-facing entrypoint.
