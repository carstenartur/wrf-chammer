# Building WRF in Docker

This repository includes a Docker-based reproducible build that generates `real.exe` and `wrf.exe` from a clean clone.

## Prerequisites

- Docker

## Local build

```bash
docker build -t wrf-reproducible .
```

Optional Ubuntu base override:

```bash
docker build --build-arg UBUNTU_VERSION=22.04 -t wrf-reproducible .
```

The Docker build invokes `ci/build-wrf.sh` inside the builder stage so the same build logic can be reused by Docker and CI.

## Verification

```bash
docker run --rm wrf-reproducible /usr/local/bin/verify-wrf-runtime.sh
docker run --rm wrf-reproducible /usr/local/bin/smoke-test-wrf.sh
```

The verification checks:

- runtime dependencies via `ldd`
- that `real.exe` and `wrf.exe` can be started in the final runtime image
- a short `ideal.exe` + `wrf.exe` simulation and output file creation (`wrfinput_d01`, `wrfout_d01_*`)

### Smoke-test case placement strategy

The runtime image produced by `Dockerfile` only installs `/opt/wrf/test/em_real` from the default build (`WRF_CASE=EM_REAL`), so `/opt/wrf/test/em_quarter_ss` is not available there.

To keep the runtime image focused while still supporting an end-to-end smoke simulation, the builder stage performs a second CMake build with `WRF_CASE=EM_QUARTER_SS` into `/opt/wrf-smoke`, and the runtime stage copies only the required smoke artifacts:

- `/opt/wrf-smoke/bin/ideal`
- `/opt/wrf-smoke/bin/wrf`
- `/opt/wrf-smoke/test/em_quarter_ss`

The smoke script runs against `/opt/wrf-smoke/test/em_quarter_ss`, ensuring it uses only files that are explicitly present in the runtime image.

The runtime image installs the required shared-library packages for the linked binaries:

- `libgfortran5`
- `libgomp1`
- `libnetcdf19`
- `libnetcdff7`
- `libstdc++6`

## Expected artifacts

- `/opt/wrf/run/real.exe`
- `/opt/wrf/run/wrf.exe`

## Known limitations

- no bundled input datasets
- no full simulation run in CI or Docker verification

---

# Building WPS in Docker

This repository includes a dedicated `Dockerfile.wps` that builds WRF from the
**repository source** using the classic Makefile build (required by WPS), clones
WPS from the official upstream repository, and delivers a lean runtime image
containing the three WPS executables.

## Relationship between Dockerfile and Dockerfile.wps

| File | WRF build system | WRF source | WRF version | Produces |
|---|---|---|---|---|
| `Dockerfile` | CMake (`configure_new` / `compile_new`) | this repository | 4.7.1 | `real.exe`, `wrf.exe` in `/opt/wrf/run` |
| `Dockerfile.wps` | classic Makefile (`configure` / `compile`) | this repository | 4.7.1 | `geogrid.exe`, `ungrib.exe`, `metgrid.exe` in `/opt/wps` |

Both images always use the WRF source from **this repository** (WRF 4.7.1), not
an external release. Only WPS itself is cloned from the official upstream
repository because WPS is not part of this fork.

The two images are **independent** — each runs a full WRF build from source. They
cannot share a build artifact because WPS requires the classic Makefile build of
WRF (it links against `main/libwrflib.a` and reads `configure.wrf` to determine
compiler flags and build settings), while the WRF simulation image uses the
newer CMake build. The separate build is only needed for the different artifact
types; both use the exact same WRF source state.

## WRF / WPS version compatibility

| Component | Version | Source |
|---|---|---|
| WRF (this fork) | 4.7.1 | this repository |
| WPS | 4.6.0 | cloned from upstream (`v4.6.0`) |

**WPS version rationale:** WPS 4.6.0 is the latest available WPS release (no
WPS 4.7.x release exists at the time of this build). WPS 4.6.0 is compatible
with WRF 4.7.1; the WPS/WRF versioning follows the same major series and minor
releases are forward-compatible within the 4.x line.

The WPS release to use is set via the `WPS_VERSION` build argument (default:
`4.6.0`).

## Jasper (GRIB2 JPEG2000 support)

The `libjasper-dev` package was removed from Ubuntu 22.04's package
repositories. Jasper is therefore built from source inside the Docker builder
stage using CMake and installed to `/opt/jasper`. The jasper version is
controlled by the `JASPER_VERSION` build argument (default: `4.2.4`).

## Prerequisites

- Docker

## Local build

```bash
docker build -f Dockerfile.wps -t wps-reproducible .
```

Optional overrides:

```bash
docker build -f Dockerfile.wps \
  --build-arg UBUNTU_VERSION=22.04 \
  --build-arg WPS_VERSION=4.6.0 \
  --build-arg JASPER_VERSION=4.2.4 \
  -t wps-reproducible .
```

The builder stage runs `ci/build-wps.sh` inside the WPS source tree so the same
build logic can be reused by Docker and CI.

## Verification

```bash
docker run --rm wps-reproducible /usr/local/bin/verify-wps-runtime.sh
```

The verification checks:

- runtime dependencies via `ldd`
- that `geogrid.exe`, `ungrib.exe`, and `metgrid.exe` can be started in the final runtime image

A full preprocessing run is not expected in this verification step.

The runtime image includes jasper shared libraries (copied from the builder
stage) and the following shared-library packages:

- jasper (built from source, installed to `/opt/jasper`)
- `libgfortran5`
- `libgomp1`
- `libnetcdf19`
- `libnetcdff7`
- `libpng16-16`
- `libstdc++6`
- `zlib1g`

## Expected artifacts

- `/opt/wps/geogrid.exe`
- `/opt/wps/ungrib.exe`
- `/opt/wps/metgrid.exe`

## Known limitations

- no bundled GEOG static data or meteorological input files
- no full preprocessing run in CI or Docker verification

---

# ERA5 download and WPS preprocessing pipeline

This repository includes an additive ERA5 pipeline container that reuses the
`Dockerfile.wps` runtime image, downloads ERA5 GRIB files with the Copernicus
CDS API, and can run `geogrid.exe`, `ungrib.exe`, and `metgrid.exe` in a user
supplied WPS working directory.

## Inputs

- Copernicus CDS credentials via `CDSAPI_KEY` (and optional `CDSAPI_URL`)
- a JSON download description such as `ci/era5/era5-example-config.json`
- a WPS working directory containing `namelist.wps`
- optional `WPS_GEOG` data when `geogrid.exe` should run

An editable starter namelist is provided at `ci/era5/namelist.wps.example`.
The example config uses two ungrib prefixes:

- `PLEV` for `reanalysis-era5-pressure-levels`
- `SFC` for `reanalysis-era5-single-levels`

## Build the ERA5 pipeline image

Build the WPS runtime first, then the ERA5 image that layers CDS tooling and
WPS tables on top:

```bash
docker build -f Dockerfile.wps -t wps-reproducible .
docker build -f Dockerfile.era5 --build-arg WPS_IMAGE=wps-reproducible -t era5-pipeline .
```

## Download only

Use a bind-mounted cache directory so repeated runs reuse existing GRIB files:

```bash
mkdir -p .era5-cache/example
docker run --rm \
  -e CDSAPI_KEY="$CDSAPI_KEY" \
  -e ERA5_CONFIG=/workspace/ci/era5/era5-example-config.json \
  -e ERA5_OUTPUT_DIR=/workspace/.era5-cache/example \
  -e ERA5_MANIFEST=/workspace/.era5-cache/example/era5-manifest.json \
  -e RUN_PREPROCESS=0 \
  -e RUN_VERIFY=0 \
  -v "$PWD:/workspace" \
  era5-pipeline
```

If the target GRIB files already exist and are non-empty, the download step is
skipped and the manifest is refreshed in place.

## Full WPS preprocessing

Prepare a WPS work directory with a `namelist.wps` derived from
`ci/era5/namelist.wps.example`, then run:

```bash
mkdir -p .wps-work
cp ci/era5/namelist.wps.example .wps-work/namelist.wps
docker run --rm \
  -e CDSAPI_KEY="$CDSAPI_KEY" \
  -e ERA5_CONFIG=/workspace/ci/era5/era5-example-config.json \
  -e ERA5_OUTPUT_DIR=/workspace/.era5-cache/example \
  -e ERA5_MANIFEST=/workspace/.era5-cache/example/era5-manifest.json \
  -e WPS_WORKDIR=/workspace/.wps-work \
  -e RUN_GEOGRID=1 \
  -e RUN_METGRID=1 \
  -v "$PWD:/workspace" \
  era5-pipeline
```

The pipeline will:

1. download or reuse cached ERA5 GRIB files
2. link the bundled WPS `Vtable.ERA-interim.pl`, `GEOGRID.TBL`, and `METGRID.TBL`
3. run `ungrib.exe` once per configured ERA5 prefix
4. rewrite `fg_name` in `namelist.wps` to match the downloaded prefixes
5. run `metgrid.exe` and verify that `met_em.d*` files were produced

### Vtable decision for ERA5

The ERA5 workflow intentionally links `Vtable.ERA-interim.pl`. This is the
supported table in upstream WPS assets for ERA5-style pressure/surface inputs
and keeps the preprocessing path aligned with the established WPS ERA guidance
without carrying a custom table variant in this repository.

The runtime image does not bundle `WPS_GEOG`, so `geogrid.exe` requires a
working `geog_data_path` in `namelist.wps`.

## GitHub Actions workflow

CI and manual download responsibilities are intentionally split:

- `.github/workflows/era5-offline-dry-run.yml` runs in CI and validates ERA5
  scripts in offline mode (JSON config validation, manifest generation, and
  cached dummy GRIB detection) without CDS credentials or network download.
- `.github/workflows/docker-era5-pipeline.yml` is manual and performs the real
  CDS-backed download flow.

The manual `.github/workflows/docker-era5-pipeline.yml` workflow:

- restores a cache directory for downloaded ERA5 files
- builds `wps-reproducible` and `era5-pipeline`
- downloads ERA5 data using `CDSAPI_KEY`
- uploads the generated manifest as a workflow artifact

The workflow defaults to download-only mode because `ubuntu-latest` does not
ship the large `WPS_GEOG` dataset needed for a full `geogrid.exe` run.
