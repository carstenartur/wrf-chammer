# Building WRF in Docker

This repository includes a Docker-based reproducible build that generates `real.exe` and `wrf.exe` from a clean clone.

## Prerequisites

- Docker

## Build from a clean clone

```bash
git clone https://github.com/carstenartur/wrf-chammer.git
cd wrf-chammer
docker build -t wrf-reproducible .
```

The Docker build itself compiles WRF and verifies the generated binaries:

- `/opt/wrf/run/real.exe`
- `/opt/wrf/run/wrf.exe`

## Verify binaries in the image

```bash
docker run --rm wrf-reproducible ls -l /opt/wrf/run/real.exe /opt/wrf/run/wrf.exe
```
