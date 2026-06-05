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
```

The verification checks:

- runtime dependencies via `ldd`
- that `real.exe` and `wrf.exe` can be started in the final runtime image

A full simulation run is not expected in this verification step.

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

This repository includes a dedicated `Dockerfile.wps` that clones the official WPS and a
compatible WRF release from GitHub, compiles them inside the builder stage, and produces a
lean runtime image containing only the three WPS executables.

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
  --build-arg WRF_VERSION=4.5.2 \
  --build-arg WPS_VERSION=4.5 \
  -t wps-reproducible .
```

The builder stage runs `ci/build-wps.sh` inside the WPS source tree so the same build logic
can be reused by Docker and CI.

## Verification

```bash
docker run --rm wps-reproducible /usr/local/bin/verify-wps-runtime.sh
```

The verification checks:

- runtime dependencies via `ldd`
- that `geogrid.exe`, `ungrib.exe`, and `metgrid.exe` can be started in the final runtime image

A full preprocessing run is not expected in this verification step.

The runtime image installs the required shared-library packages for the linked binaries:

- `libjasper4`
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
