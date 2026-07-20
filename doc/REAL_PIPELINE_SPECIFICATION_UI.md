# Freezing immutable real WPS/WRF specifications in the GUI

The Workbench does not start a real simulation directly from mutable browser state. Before execution becomes available, the user freezes one server-validated job, one verified ERA5 input set and pinned runtime identities into an immutable, content-addressed specification.

## Prerequisites

The **Freeze a real WPS/WRF run specification** panel becomes actionable only when:

- the guided simulation preview is valid;
- a content-addressed ERA5 plan has 100% cache coverage;
- `checksums.json` and `provenance.json` are available;
- provenance records `artificial_weather_data: false`;
- WPS, WRF and postprocessing runtime identities use pinned `sha256` values;
- a repository source revision is available.

The panel never treats a mutable image tag such as `latest` as an identity. Tags remain human-readable references; the SHA-256 identity is what enters the immutable record.

## API

### Read readiness and profiles

```http
GET /api/pipeline/specifications/readiness
```

The response includes:

- runtime references and identities;
- source revision;
- supported execution profiles and their grid limits;
- validation errors;
- whether a current guided preview exists.

This operation does not create files or start processes.

### List existing specifications

```http
GET /api/pipeline/specifications
```

The list contains only safe summaries: specification key, creation time, job ID, profile, ERA5 plan key and whether execution has started.

### Freeze or reuse a specification

```http
POST /api/pipeline/specifications
Content-Type: application/json

{
  "plan_key": "<complete verified ERA5 plan key>",
  "profile": "small-real-data-demo"
}
```

The server ignores client-supplied job JSON and namelists. It uses the latest server-validated wizard preview and loads the verified ERA5 files, checksums and provenance itself.

The response contains the complete immutable specification. Repeating the same request with unchanged inputs returns the same specification key and original creation timestamp.

### Read one specification

```http
GET /api/pipeline/specifications/{specification-key}
```

The identity hash and mandatory namelist artifacts are verified on every read.

## Browser workflow

1. Complete guided map planning.
2. Download and verify the required ERA5 input.
3. Ensure WPS, WRF and postprocessing runtime identities are pinned locally.
4. Open **Freeze a real WPS/WRF run specification**.
5. Select an eligible verified ERA5 plan.
6. Select a constrained pipeline profile.
7. Select **Freeze immutable specification**.
8. Review the content-addressed key and the eight frozen step contracts.

The result explicitly shows:

```text
Execution started: no
```

No container, `geogrid.exe`, `ungrib.exe`, `metgrid.exe`, `real.exe`, `wrf.exe` or postprocessing command is started by this operation.

## Security and reproducibility

- Browser requests contain only a plan key and profile ID.
- Absolute host paths are absent from responses.
- Credentials are never part of the specification.
- Runtime identities, source revision, namelist content and verified input checksums are included in the identity hash.
- Specification directories are content-addressed and may not be symlinks.
- Existing records are reused rather than overwritten.
- Mutable execution state will live in later `SimulationJob` and `JobStep` records referencing this immutable key.

## Tests

```bash
PYTHONPATH=. python3 workbench/server/tests/test_pipeline_specification_api.py
python3 workbench/server/tests/test_ui_public_asset_build.py
node --check workbench/web/real-pipeline-specification.js
```

The API test exercises the complete offline flow: guided preview, prepared and checksummed ERA5 input, runtime readiness, specification creation, idempotent reuse, listing, retrieval, invalid-profile rejection and response-path redaction.
