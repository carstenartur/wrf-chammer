# Workbench event catalogue and presets

The Workbench event catalogue is the bridge between a human request such as
"run Storm Xaver" and a concrete WRF job configuration.  It should hide as much
raw WRF grid complexity as possible while still producing transparent,
reproducible settings.

The catalogue is intentionally local JSON for now.  It can later be backed by a
larger event database, but the first web UI should be able to load these files
without network access.

## Architectural rule

Catalogue semantics live in `workbench/core/catalogue.py`.

The JSON files are data.  The core module is the single place for:

- loading catalogue and preset files
- validating cross references
- building the search index
- resolving event ids, names and aliases
- converting an event plus presets into a Workbench job config

Shell scripts, CI, the future local API and the browser UI should call this core
module instead of copying catalogue rules into their own code.  This keeps the
cognitive load low for contributors: UI work can focus on UI, runner work can
focus on execution, and catalogue work can stay in this directory.

## Files

```text
workbench/core/catalogue.py          # Shared catalogue logic and CLI
workbench/events/catalogue.json      # Named weather events
workbench/presets/domains.json       # Reusable WRF domain presets
workbench/presets/resolutions.json   # Runtime/resolution classes
ci/test-event-catalogue.sh           # Thin CI wrapper around the core module
```

## Event catalogue schema

Each event entry is keyed by its stable id and contains enough metadata for UI
search, default selection and Workbench config generation.

```json
{
  "id": "xaver",
  "name": "Storm Xaver",
  "aliases": ["Xaver", "Cyclone Xaver", "Sturm Xaver"],
  "event_type": "storm",
  "description": "Human-readable summary.",
  "period": {
    "start": "2013-12-05T00:00:00Z",
    "end": "2013-12-06T12:00:00Z"
  },
  "default_domain": "northern-germany-9km",
  "domains": ["northern-germany-27km", "northern-germany-9km"],
  "default_resolution_preset": "balanced-local",
  "resolution_presets": ["quick-preview", "balanced-local"],
  "suggested_outputs": ["wind10m", "pressure", "accumulated_precipitation"],
  "notes": "Optional implementation notes."
}
```

### Required event fields

| Field | Purpose |
|---|---|
| `id` | Stable machine id; must match the key in `events`. |
| `name` | User-facing name. |
| `aliases` | Search terms such as local-language names or common variants. |
| `event_type` | Broad searchable class, e.g. `storm` or `custom`. |
| `description` | Short user-facing explanation. |
| `period.start`, `period.end` | Default UTC event/simulation time window. |
| `default_domain` | Domain preset id selected first in the UI. |
| `domains` | Domain preset ids offered to the user. |
| `default_resolution_preset` | Runtime/resolution class selected first in the UI. |
| `resolution_presets` | Resolution/runtime choices offered to the user. |
| `suggested_outputs` | Products that should be selected by default. |

`start`, `end` and `domain` may still appear for backward compatibility with
older Workbench readers.  New code should prefer `period` and domain preset ids.

## Domain preset schema

Domain presets are reusable.  Events reference them by id rather than embedding
separate raw WRF parameters for every event.

```json
{
  "id": "northern-germany-9km",
  "label": "Northern Germany balanced local run, 9 km",
  "center_lat": 54.0,
  "center_lon": 9.0,
  "dx_km": 9,
  "dy_km": 9,
  "e_we": 80,
  "e_sn": 60,
  "bounds": [3.5, 51.5, 14.5, 56.5],
  "intended_use": "Default Storm Xaver domain for a balanced local run.",
  "runtime_class": "balanced-local",
  "warning_level": "medium",
  "tags": ["xaver", "northern-germany", "storm"]
}
```

### Domain fields

| Field | Purpose |
|---|---|
| `center_lat`, `center_lon` | WRF domain center. |
| `dx_km`, `dy_km` | Horizontal grid spacing. |
| `e_we`, `e_sn` | Grid dimensions. |
| `bounds` | Approximate `[west, south, east, north]` preview box for the UI. |
| `intended_use` | User-facing explanation of when to choose this domain. |
| `runtime_class` | Links the domain to a rough runtime class. |
| `warning_level` | UI warning level: `low`, `medium`, `high`, `critical`. |

The bounds are an approximate UI preview, not an authoritative WRF projection
calculation.

## Resolution/runtime presets

Resolution presets provide a user-friendly way to choose between a quick preview
and a more expensive run.

```json
{
  "id": "quick-preview",
  "label": "Quick preview",
  "description": "Coarse run to validate event, time window and domain.",
  "suggested_dx_km": 27,
  "suggested_dy_km": 27,
  "suggested_max_grid_cells": 3000,
  "runtime_class": "short",
  "warning_level": "low"
}
```

The values are deliberately approximate.  They are meant to prevent unrealistic
first choices, not to predict exact runtime.

## Core module usage

Validate all catalogue and preset files:

```bash
python3 -m workbench.core.catalogue validate
```

Search events by id, name, alias or type:

```bash
python3 -m workbench.core.catalogue search xaver
python3 -m workbench.core.catalogue search storm
```

Generate a Workbench job config from an event and optional presets:

```bash
python3 -m workbench.core.catalogue build-job xaver \
  --domain northern-germany-9km \
  --resolution balanced-local \
  --mode dry-run
```

Programmatic usage for the future local API:

```python
from workbench.core.catalogue import build_job_config, resolve_event, search_events

matches = search_events("xaver")
event = resolve_event("Xaver")
job_config = build_job_config("xaver", domain_id="northern-germany-9km")
```

## How the UI should use the catalogue

The UI should not reimplement catalogue rules.  It should call the local API, and
the local API should call `workbench.core.catalogue`.

Recommended flow:

1. API loads the catalogue using `load_catalogue()`.
2. API exposes `search_events(query)` results to the UI.
3. UI displays events, domain presets, warning levels and output products.
4. User selects a domain/resolution preset.
5. API calls `build_job_config(...)`.
6. API validates the generated config using the existing Workbench validator.
7. UI receives a ready-to-run Workbench job config.

For a static-only prototype, JavaScript may read the JSON files directly, but any
logic added there should be treated as temporary and migrated back into
`workbench.core` or the local API.

## Adding a new event

1. Add an event entry under `events` in `catalogue.json`.
2. Add at least one alias that a user is likely to search for.
3. Choose one or more existing domain presets, or add new domain presets first.
4. Set `default_domain` to a valid domain preset id.
5. Set `default_resolution_preset` to a valid resolution preset id.
6. Add suggested outputs.
7. Run:

```bash
sh ci/test-event-catalogue.sh
```

## Adding a new domain preset

1. Add it to `workbench/presets/domains.json`.
2. Use a stable id that includes region and approximate resolution.
3. Provide approximate bounds for UI preview.
4. Choose a warning level that reflects expected cost.
5. Reference it from at least one event if it should appear in the UI.
6. Run the catalogue validation test.

## Validation

`ci/test-event-catalogue.sh` is intentionally a thin wrapper around:

```bash
python3 -m workbench.core.catalogue validate
```

The core validation checks:

- all required event fields
- aliases and searchability
- valid UTC periods with start before end
- default domain and listed domains exist
- resolution presets exist
- domain coordinates/dimensions/bounds are valid
- Xaver has multiple domain choices
- a generated Xaver Workbench config passes `workbench/validate.py`
