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

- no WPS build
- no bundled input datasets
- no full simulation run in CI or Docker verification
