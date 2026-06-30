# Local Workbench API server

This directory contains the local HTTP API and the same-origin web UI server for
the existing Workbench runner.

The server is intentionally small and dependency-free.  It uses Python 3 stdlib,
`workbench.core.catalogue` for event/preset logic, `workbench.validate` for job
validation, `workbench/run.sh` for execution and fixed routes for the static UI
in `workbench/web`.

## Security model

This is a local development server.

- Default bind address: `127.0.0.1`
- Only loopback clients are accepted
- No dynamic CORS origin reflection; the web UI is served from the same local origin
- No authentication yet
- Executes local Workbench scripts
- Not intended for public internet exposure

Do not bind it to `0.0.0.0` on an untrusted network.

## Path and execution hardening

API-created jobs do not run in user-supplied filesystem paths.

Even if a submitted or previewed job config contains `outputs.directory`, the
server replaces it for execution with a server-generated directory:

```text
workbench-runs/api-runs/<server-generated-token>/
```

User-provided job ids are used only as validated logical ids and dictionary
keys; they are not used as filenames.  Logs and output listings are read only
from server-managed run directories, do not follow symlinks and do not expose
absolute per-file paths in the JSON response.

## Start the server and UI

From the repository root:

```bash
python3 -m workbench.server.server --host 127.0.0.1 --port 8080
```

Open the web UI:

```text
http://127.0.0.1:8080/
```

Equivalent route:

```text
http://127.0.0.1:8080/web/
```

Health check:

```bash
curl http://127.0.0.1:8080/api/health
```

## UI routes

The server exposes only fixed static UI routes:

```http
GET /
GET /web/
GET /web/index.html
GET /web/app.js
GET /web/styles.css
```

The browser UI calls the same local API endpoints described below.

## API endpoints

### Events

```http
GET /api/events
GET /api/events?q=xaver
GET /api/events/xaver
```

The event endpoints are backed by `workbench.core.catalogue`; they do not parse
the catalogue independently.

### Preview a job config from an event

```http
POST /api/jobs/preview
Content-Type: application/json

{
  "event": "Xaver",
  "domain": "northern-germany-9km",
  "resolution": "balanced-local",
  "mode": "dry-run",
  "job_id": "xaver-preview",
  "output_directory": "workbench-runs/xaver-preview"
}
```

This endpoint calls `workbench.core.catalogue.build_job_config(...)` and returns
a Workbench job config plus validation result.  It is the preferred bridge for
the web UI because the UI does not need to duplicate catalogue or WRF job
configuration rules.

`output_directory` in preview responses is informational only.  `POST /api/jobs`
will replace the execution directory with a server-managed API run directory.

### Validate a job config

```http
POST /api/jobs/validate
Content-Type: application/json

{
  "config": {"...": "Workbench job config"}
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

### Cancellation placeholder

```http
POST /api/jobs/{id}/cancel
```

Cancellation intentionally returns HTTP 501 for now.

## Relationship to the Web UI

The browser UI should call this API rather than reading and interpreting all
Workbench internals directly.

Recommended flow:

1. UI searches events through `GET /api/events?q=...`.
2. UI shows event details and presets from `GET /api/events/{id}`.
3. UI asks the API to generate/preview a config through `POST /api/jobs/preview`.
4. UI starts a dry-run or real pipeline with `POST /api/jobs`.
5. UI polls `GET /api/jobs/{id}` and fetches logs/output metadata.

## Tests

```bash
sh ci/test-workbench-server.sh
sh ci/test-workbench-web.sh
```

The API test starts the server on a random local port and exercises event lookup,
preview, validation, dry-run execution, status, logs, outputs, visualization
metadata, server-managed run paths and the cancellation placeholder.

The web UI test starts the same local server, fetches the static UI assets and
verifies that the API can produce a valid Xaver dry-run preview from the UI path.
