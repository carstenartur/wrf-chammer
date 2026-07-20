# Immutable real WPS/WRF run specifications

A real WPS/WRF execution must not derive scientific settings, input files or runtime versions from mutable browser state after the worker has started. This module introduces the immutable boundary required by issue #46.

## What is frozen

`PipelineSpecificationService` combines the latest server-validated guided job with one complete, verified ERA5 plan. The identity contains:

- job identifier, name, domain, period and planning metadata;
- a tested pipeline profile;
- deterministic `namelist.wps` and `namelist.input` contents plus SHA-256;
- every verified ERA5 input file with relative path, byte count, request name and SHA-256;
- ERA5 source, datasets, verification time and download-job identity;
- pinned WPS, WRF and postprocessing runtime references and `sha256` identities;
- repository source revision;
- WRF 4.7.1 and WPS 4.6.0 version declarations used by this fork;
- postprocessing and result-indexing profiles;
- the supported structured error categories;
- immutable contracts for every pipeline step.

The specification identity is canonical JSON. Its SHA-256 becomes the specification key and storage directory name.

## Pipeline step contracts

The initial standard path contains eight explicit steps:

1. verify the ERA5 input set;
2. run `geogrid.exe`;
3. run `ungrib.exe`;
4. run `metgrid.exe`;
5. run `real.exe`;
6. run `wrf.exe`;
7. postprocess WRF output;
8. index result artifacts.

Each contract defines:

- stable step ID and user-facing label;
- initial status `PENDING`;
- pinned runtime identity where applicable;
- logical inputs and outputs;
- progress metrics expected from the later worker implementation.

This prevents the executor from inventing its own implicit pipeline order or artifact names.

## Safe profiles

The first profiles are:

- `small-real-data-demo`;
- `quick-preview`;
- `balanced-regional`.

Profiles constrain maximum horizontal grid points, vertical levels, history interval, time-step factor, physics selections and postprocessing profile. A job exceeding the selected profile limit is rejected before a specification is written.

Namelist runtime fields are derived from the actual period, including non-whole-hour durations. The historical shell implementation hard-coded a six-hour run; the specification generator does not.

## Preconditions

A specification can be frozen only when:

- the guided preview is valid;
- the selected ERA5 plan exists and its current cache status is `complete`;
- `checksums.json` and `provenance.json` exist;
- every input file has a positive size and a valid SHA-256;
- provenance explicitly records `artificial_weather_data: false`;
- the job and ERA5 periods match;
- WPS, WRF and postprocessing runtimes have pinned `sha256` identities;
- the repository source revision is a pinned Git SHA.

Runtime identities are configured locally with:

```text
WRF_CHAMMER_WPS_RUNTIME_REFERENCE
WRF_CHAMMER_WPS_RUNTIME_IDENTITY
WRF_CHAMMER_WRF_RUNTIME_REFERENCE
WRF_CHAMMER_WRF_RUNTIME_IDENTITY
WRF_CHAMMER_POSTPROCESSING_RUNTIME_REFERENCE
WRF_CHAMMER_POSTPROCESSING_RUNTIME_IDENTITY
```

Identity values must use the form `sha256:<64 lowercase hex characters>`. Mutable tags may remain as human-readable references, but never serve as the immutable identity.

`WRF_CHAMMER_SOURCE_REVISION` may provide the source revision. Otherwise the service reads the current checkout revision with `git rev-parse HEAD`.

## Storage and integrity

Specifications are stored below:

```text
workbench-runs/specifications/<specification-key>/
  run-specification.json
  namelist.wps
  namelist.input
  identity.sha256
```

Creating the same identity twice returns the existing record and preserves its original creation timestamp. Existing records are verified on read:

- the identity must hash to the directory key;
- both namelists and the identity marker must exist;
- symlinked or malformed keys are not selected.

Execution state is deliberately not mixed into the immutable identity. The later persistent worker will reference the specification key from its mutable `SimulationJob`/`JobStep` records.

## Current scope

This pull request provides the deterministic core and persistence service. The next #46 slices will:

1. expose creation/readiness/listing through the application API and guided UI;
2. replace the monolithic real branch in `run-era5-wrf.sh` with step executors consuming this specification;
3. persist per-step state, progress, resource measurements and classified failures;
4. run the real Xaver micro-case from issue #37 through the same standard worker path.

No synthetic weather data or fake runtime identity is accepted by the production specification service.

## Tests

```bash
python3 workbench/server/tests/test_pipeline_specification.py -v
python3 workbench/server/tests/test_pipeline_specification_service.py -v
```

The tests cover deterministic namelists, non-whole-hour runtimes, eight step contracts, complete real-input enforcement, artificial-data rejection, runtime pinning, content-addressed idempotency, readiness diagnostics, tamper detection and missing-checksum rejection.
