# ERA5 data planning and cache identity

The Workbench must know which real ERA5 fields are required before it starts a download. `workbench/era5_planner.py` creates that plan without downloading data and without generating substitute weather fields.

## Guarantees

The planner:

- requests Copernicus ERA5 reanalysis data only;
- produces the existing `ci/download-era5.py` configuration format;
- includes pressure-level and single-level fields required by the WPS path;
- splits requests by UTC day so individual cache entries remain manageable;
- assigns a stable SHA-256 identity to every request and to the complete plan;
- reports complete, partial, and missing cache entries;
- records that no artificial weather data were used.

It does **not** claim that a requested dataset has already been downloaded, that the CDS request will be accepted immediately, or that a WRF result is scientifically validated.

## Plan a job from the command line

From the repository root:

```bash
python3 -m workbench.era5_planner \
  --job workbench/examples/xaver-dry-run.json \
  --cache-root .era5-cache \
  --output workbench-runs/xaver-era5-plan.json \
  --download-config workbench-runs/xaver-era5-download.json
```

The complete plan contains:

- exact simulation period and hourly boundary time points;
- requested geographic bounds and the expanded CDS area;
- downloader-compatible request definitions;
- per-request and complete-plan cache keys;
- estimated download size;
- cache coverage and partial-download status;
- provenance metadata.

The separate download configuration can be passed directly to the existing downloader:

```bash
PLAN_KEY=$(python3 -c 'import json; print(json.load(open("workbench-runs/xaver-era5-plan.json"))["plan_key"])')

python3 ci/download-era5.py \
  --config workbench-runs/xaver-era5-download.json \
  --output-dir ".era5-cache/${PLAN_KEY}" \
  --manifest ".era5-cache/${PLAN_KEY}/era5-manifest.json"
```

## Cache layout

The cache is content-addressed by the complete request plan:

```text
.era5-cache/
└── <plan-key>/
    ├── files/
    │   ├── pressure_levels-YYYYMMDD-<request-key-prefix>.grib
    │   └── single_levels-YYYYMMDD-<request-key-prefix>.grib
    └── era5-manifest.json
```

A request is a cache hit only when its target exists and is non-empty. A matching `.part` file is reported as an incomplete download and is never treated as usable input.

## Xaver reference plan

For the documentation-oriented Xaver region:

```text
Bounds:   2.0–14.0° E, 51.0–58.0° N
Period:   2013-12-05 12:00 UTC to 2013-12-06 06:00 UTC
Margin:   1.0° around the selected domain
Cadence:  hourly ERA5 boundary data
```

The period crosses midnight, so the planner creates two pressure-level and two single-level requests. The first day contains 12 hourly times and the second day contains 7, including both simulation endpoints.

## Request identity

Each request key is the SHA-256 digest of canonical JSON containing:

- dataset name;
- exact CDS request body;
- planner format version.

The complete plan key is derived from the sorted request keys. Cache identity therefore does not depend on local paths or JSON whitespace.

## Tests

Run the planner, cache, and downloader-compatibility tests locally:

```bash
python3 ci/test_era5_planner.py
```

The tests use no network and no generated weather fields. Small non-empty files are used only to represent already downloaded GRIB cache entries while testing manifest and cache behaviour.

## Next integration steps

Issue #44 continues with:

- `POST /api/data/era5/plan` for the browser wizard;
- credential status without exposing secrets;
- explicit download confirmation;
- progress and cancellation through the persistent job system;
- cache inspection and cleanup in the GUI;
- provenance transfer into the final WRF job manifest.
