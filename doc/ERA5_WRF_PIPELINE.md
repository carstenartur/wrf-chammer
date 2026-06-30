# ERA5 to WRF Workbench pipeline

This document describes the first `era5-wrf` Workbench mode.

The goal is to make the full workflow explicit and testable:

```text
Workbench config
→ ERA5 request/cache check
→ WPS namelist
→ WRF namelist
→ WPS / WRF execution path
→ wrfout or cached fixture artifact
→ visualization postprocessing
```

## Cacheable CI path

The default path is designed for CI and local smoke testing without CDS
credentials, Docker images or NCAR data downloads.

```bash
sh workbench/run.sh workbench/examples/xaver-era5-wrf.json
```

For CI, pre-seed the expected ERA5 target under the run directory before running
the Workbench job.  The test does this with:

```text
<run-dir>/outputs/era5/dummy-era5.grib
```

The mode then:

1. verifies that the expected cached ERA5 target exists;
2. runs the existing ERA5 download script in cached mode;
3. writes `namelists/namelist.wps`;
4. writes `namelists/namelist.input`;
5. writes `outputs/era5-manifest.json`;
6. writes `outputs/pipeline-metadata.json`;
7. runs visualization postprocessing from the built-in fixture;
8. writes `visualizations/metadata.json`.

This path proves the Workbench orchestration, namelist generation, cache checks,
metadata layout and visualization handoff without pretending that a real WRF
simulation was executed.

## Manual real WPS/WRF path

To execute real WPS and WRF binaries, set:

```bash
export WORKBENCH_ERA5_WRF_REAL=1
export WORKBENCH_ERA5_WRF_ALLOW_DOWNLOAD=1   # only when CDS credentials are available
export WPS_DIR=/path/to/WPS
export WRF_DIR=/path/to/WRF/run
```

`WPS_DIR` must contain executable:

```text
geogrid.exe
ungrib.exe
metgrid.exe
```

`WRF_DIR` must contain executable:

```text
real.exe
wrf.exe
```

Then run:

```bash
sh workbench/run.sh workbench/examples/xaver-era5-wrf.json
```

In real mode, the script checks for all required executables, links cached or
downloaded ERA5 GRIB inputs into a WPS work directory, runs WPS, runs
`real.exe`, runs `wrf.exe`, copies `wrfout_d01_*` into the Workbench output
folder and invokes visualization postprocessing on the WRF output directory.

## Outputs

A successful cached run creates:

```text
<run-dir>/
  job.json
  status.json
  logs/workbench.log
  namelists/namelist.wps
  namelists/namelist.input
  outputs/era5-manifest.json
  outputs/pipeline-metadata.json
  outputs/wps/namelist.wps
  outputs/wrf/namelist.input
  outputs/wrf/cached-wrf-visualization-fixture.json
  visualizations/metadata.json
```

A successful real run additionally copies real `wrfout_d01_*` files into:

```text
<run-dir>/outputs/wrf/
```

## Tests

```bash
sh ci/test-era5-wrf-pipeline.sh
```

The test verifies:

- the example config validates;
- cached ERA5-WRF mode succeeds;
- WPS namelist contains expected dates, grid size and spacing;
- WRF namelist contains expected dates and grid size;
- pipeline metadata is written;
- visualization metadata is written;
- missing cached ERA5 input fails clearly.
