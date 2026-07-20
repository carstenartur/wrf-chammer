# Explicit Copernicus CDS credential validation

The Workbench distinguishes two different statements:

1. credentials appear to be configured locally;
2. the configured credentials can actually complete a real Copernicus Climate Data Store request.

The first is a local readiness check. The second requires an explicit network operation and is never triggered automatically.

## User flow

The **Test Copernicus CDS access** panel shows whether `CDSAPI_KEY` or a local `.cdsapirc` is present and the result of the most recent explicit test.

Select **Run real credential test** to start a separate process. The browser receives `202 Accepted` immediately and polls the classified persistent state.

Possible terminal results are:

- `VALID`: a tiny real ERA5 response was downloaded, checked and deleted;
- `INVALID`: the provider rejected the credentials or required dataset terms were not accepted;
- `FAILED`: the request could not prove credential validity because the client package, network or service was unavailable;
- `CANCELLED`: the Workbench stopped while validation was active.

A timeout or CDS outage is not misreported as an invalid key.

## Minimal real request

The validator requests one ERA5 single-level field:

```text
Dataset:  reanalysis-era5-single-levels
Variable: 2 m temperature
Time:     2013-12-05 12:00 UTC
Area:     52.00 N, 7.00 E to 51.75 N, 7.25 E
Format:   GRIB
```

The response is written inside an operating-system temporary directory. The validator verifies that it is non-empty, computes SHA-256 and then leaves the temporary context, which removes the response. Only byte count, checksum and the fixed non-secret request summary are persisted.

The request uses real ERA5 data and records `artificial_weather_data: false`. It is not a simulation input and is not added to the content-addressed simulation cache.

## API

### Read status

```http
GET /api/data/era5/credentials/validation
```

This endpoint performs no network request. It returns:

- whether credentials are locally configured;
- the latest validation state, if any;
- classified code and summary;
- safe request/response metadata;
- whether a validation process is still running.

### Start validation

```http
POST /api/data/era5/credentials/validate
Content-Type: application/json

{}
```

The operation returns `202 Accepted`. A second start while validation is active returns `409 Conflict`.

## Persistence and recovery

Classified state is stored under:

```text
.era5-cache/.credential-validation/<validation-id>/state.json
.era5-cache/.credential-validation/latest.json
```

The browser never receives these paths. If the application restarts while a validation is active, the state becomes `FAILED` with `validation_interrupted`; no false success or invalid-credential result is inferred.

The default process timeout is 300 seconds and can be configured locally with:

```text
WRF_CHAMMER_CDS_VALIDATION_TIMEOUT
```

Values are constrained to 1–900 seconds.

## Secret-handling guarantees

- The key is read only by `cdsapi` through its normal local environment/configuration mechanism.
- No credential value appears in the subprocess command.
- The validator returns only fixed classified messages, never raw provider exceptions.
- Provider stdout and stderr are discarded during client creation and retrieval.
- API responses contain no environment values, `.cdsapirc` content, absolute paths or raw errors.
- The temporary GRIB response is not retained.

## Offline tests

CI does not contact Copernicus. It injects fake `cdsapi` modules and fake validator processes to prove:

- the exact minimal request shape;
- successful checksum metadata and immediate non-retention;
- classification of authentication failures without raw secret text;
- persistent valid status;
- duplicate-start rejection;
- timeout handling;
- restart recovery;
- API redaction and Vite/browser integration.

Run locally:

```bash
python3 workbench/server/tests/test_cds_credential_validator.py -v
python3 workbench/server/tests/test_era5_credential_service.py -v
python3 workbench/server/tests/test_era5_credential_api.py
node --check workbench/web/era5-credential-validation.js
```
