# Local Workbench API server

This directory contains the local HTTP API between the future browser UI and the
existing Workbench runner.

The server is intentionally small and dependency-free.  It uses Python 3 stdlib,
`workbench.core.catalogue` for event/preset logic, `workbench.validate` for job
validation and `workbench/run.sh` for execution.

## Security model

This is a local development server.

- Default bind address: `127.0.0.1`
- No authentication yet
- Executes local Workbench scripts
- Not intended for public internet exposure

Do not bind it to `0.0.0.0` on an untrusted network.

## Start the server

From the repository root:

```bash
python3 -m workbench.server.server --host 127.0.0.1 --port 8080
```

Health check:

```bash
curl http://127.0.0.1:8080/api/health
```

## API endpoints

### Events

```http
GET /api/events
GET /api/events?q=xaver
GET /api/events/xaver
```

The event endpoints are backed by `workbench.core.catalogue`; they do not parse
the catalogue independently.

### Validate a job config

```http
POST /api/jobs/validate
Content-Type: application/json

{
  "config": {
    "id": "xaver-preview",
    "mode": "dry-run",
    "name": "Storm Xaver Preview",
    "period": {"start": "2013-12-05T00:00:00Z", "end": "2013-12-05T06:00:00Z"},
    "domain": {
      "label": "northern-germany-9km",
      "center_lat": 54.0,
      "center_lon": 9.0,
      "dx_km": 9,
      "dy_km": 9,
      "e_we": 20,
      "e_sn": 20
    },
    "inputs": {"source": "era5"},
    "outputs": {"directory": "workbench-runs/xaver-preview"}
  }
}
```

Valid configs return HTTP 200 with `valid: true`.  Invalid configs return HTTP
422 with a structured `errors` array.

### Create/start a job

```http
POST /api/jobs
Content-Type: application/json

{
  "config": {"...": "Workbench job config"},
  "start": true
}
```

The server writes the submitted config to:

```text
<outputs.directory>/api-config.json
```

Then it runs:

```text
sh workbench/run.sh <outputs.directory>/api-config.json
```

The first implementation runs jobs synchronously.  That is sufficient for short
`dry-run` API flows and CI smoke tests.  A future asynchronous runner can keep
the same API shape and replace only the execution backend.

### Job status and logs

```http
GET /api/jobs/{id}
GET /api/jobs/{id}/logs
GET /api/jobs/{id}/outputs
GET /api/jobs/{id}/visualization
```

The API stores a small local job index under:

```text
workbench-runs/.api-index/
```

This lets the API resolve jobs even when their output directory is absolute or
outside the default `workbench-runs/<id>` pattern.

### Cancellation placeholder

```http
POST /api/jobs/{id}/cancel
```

Cancellation intentionally returns HTTP 501 for now:

```json
{
  "ok": false,
  "error": {
    "code": "cancel_not_implemented",
    "message": "Job cancellation is not implemented yet for the local synchronous runner."
  }
}
```

## Relationship to the Web UI

The browser UI should call this API rather than reading and interpreting all
Workbench internals directly.

Recommended flow:

1. UI searches events through `GET /api/events?q=...`.
2. UI shows event details and presets from `GET /api/events/{id}`.
3. UI asks the API or local core to generate/preview a config.
4. UI sends the generated config to `POST /api/jobs/validate`.
5. UI starts a dry-run or real pipeline with `POST /api/jobs`.
6. UI polls `GET /api/jobs/{id}` and fetches logs/output metadata.

## Tests

```bash
sh ci/test-workbench-server.sh
```

The test starts the server on a random local port, exercises events,
validation, dry-run execution, status, logs, outputs, visualization metadata and
the cancellation placeholder.  It requires no Docker, CDS credentials or HPC
infrastructure.
