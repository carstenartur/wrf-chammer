# Workbench web UI

This directory contains the first local browser UI for the WRF Workbench.

The UI is served by the local Workbench API server, not by a separate frontend
server.  This keeps the browser and API on the same origin and avoids duplicating
Workbench rules in JavaScript.

## Start

From the repository root:

```bash
python3 -m workbench.server.server --host 127.0.0.1 --port 8080
```

Then open:

```text
http://127.0.0.1:8080/
```

Equivalent route:

```text
http://127.0.0.1:8080/web/
```

## User flow

The UI implements the first event-to-simulation path:

1. Search an event, for example `Xaver`.
2. Select the event from the result list.
3. Inspect event defaults and suggested outputs.
4. Choose a domain and resolution preset.
5. Preview the generated Workbench job config through `POST /api/jobs/preview`.
6. Start a dry-run through `POST /api/jobs`.
7. Inspect status and logs through `GET /api/jobs/{id}` and `GET /api/jobs/{id}/logs`.

## Architecture rule

The browser UI must stay thin.

- Event search and alias resolution stay in `workbench.core.catalogue`.
- Job config generation stays in `workbench.core.catalogue.build_job_config`.
- Job validation stays in `workbench.validate`.
- Job execution stays in `workbench.server.server` / `workbench/run.sh`.
- The browser only renders state and calls local API endpoints.

## Domain preview

The first preview is intentionally approximate.  It renders a simple rectangle
from the selected domain dimensions and resolution class.  It is meant to show
that a selected event has a visible simulation area, not to implement exact WRF
map projection math.

## Tests

```bash
sh ci/test-workbench-web.sh
```

The test starts the local server, fetches the UI assets, checks for expected
controls and verifies that the API can produce a valid Xaver dry-run preview.
