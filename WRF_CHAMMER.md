# WRF Chammer fork overlay

This repository is a fork of the upstream WRF model repository with an additional, local **WRF Workbench** layer.

The goal of this file is to keep fork-specific documentation separate from upstream WRF files, so future merges from `wrf-model/WRF` are easier to review.

## WRF Workbench

The Workbench is a local, reproducible layer for turning weather events into WRF jobs with an event catalogue, local API, browser UI, ERA5/WPS/WRF pipeline modes, status/log output and visualization artifacts.

## Current maturity

The Workbench is useful as a reproducible local workflow prototype and demo, but it is **not yet a turnkey meteorological analysis product** for non-technical end users. In particular, a user cannot yet click through the UI and obtain maximum wind speed at an arbitrary height and point such as "50 m above Bonn Hauptbahnhof during Storm Xaver" without additional model setup and post-processing.

Current verified scope:

- search/select Storm Xaver from the local event catalogue;
- generate a dry-run job through the local UI/API;
- execute a dry-run and inspect status/logs;
- run a cached ERA5-WRF pipeline path that creates WPS/WRF namelists, metadata and visualization artifacts;
- generate user-guide screenshots from the real browser flow.

Important gaps for the Bonn Hauptbahnhof / 50 m use case:

- custom point/location input is not yet exposed in the UI;
- custom event/domain creation is not yet exposed in the UI;
- the browser workflow currently demonstrates dry-run, not a full real-data WRF run;
- visualization currently focuses on available WRF products such as 10 m wind and maximum 10 m wind, not arbitrary-height interpolation to 50 m;
- a scientifically defensible point result needs documented grid resolution, vertical interpolation, model configuration and uncertainty caveats.

## Documentation entry points

| Purpose | Link |
|---|---|
| User guide with generated screenshots | [doc/USER_GUIDE.md](doc/USER_GUIDE.md) |
| Storm Xaver end-to-end demo | [doc/XAVER_DEMO.md](doc/XAVER_DEMO.md) |
| ERA5 to WRF pipeline guide | [doc/ERA5_WRF_PIPELINE.md](doc/ERA5_WRF_PIPELINE.md) |
| Workbench README | [workbench/README.md](workbench/README.md) |
| Workbench architecture | [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md) |
| Screenshot artifact workflow | [User Guide Screenshots](https://github.com/carstenartur/wrf-chammer/actions/workflows/user-guide-screenshots.yml) |
| Workbench CI workflow | [Workbench MVP Tests](https://github.com/carstenartur/wrf-chammer/actions/workflows/workbench-tests.yml) |

## Local Workbench UI/API

Start the local Workbench UI and API from the repository root:

```sh
python3 -m workbench.server.server --host 127.0.0.1 --port 8080
```

Then open:

```text
http://127.0.0.1:8080/
```

Generate the user-guide screenshots locally:

```sh
sh ci/generate-user-guide-screenshots.sh
```

CI uploads generated screenshots as the `xaver-user-guide-screenshots` artifact.

## Docker Compose stack

The repository also contains a Docker Compose based development stack. This is a separate stack from the lightweight local Workbench UI/API command above:

```sh
docker compose up --build
```

The REST API for that stack is available at `http://localhost:8080/v1/` once the `backend` service is healthy. The MinIO console is available at `http://localhost:9001/`.

## Upstream merge policy

To reduce conflicts with `wrf-model/WRF`, fork-specific functionality should live in additive paths whenever possible:

```text
WRF_CHAMMER.md
workbench/
visualization/
ci/era5/
doc/USER_GUIDE.md
doc/XAVER_DEMO.md
doc/ERA5_WRF_PIPELINE.md
.github/workflows/wrf-chammer-*.yml
```

Avoid modifying upstream-owned scientific model files unless the fork intentionally carries a model-code patch.
