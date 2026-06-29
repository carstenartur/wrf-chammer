# Workbench event catalogue and presets

The Workbench event catalogue is the bridge between a human request such as
"run Storm Xaver" and a concrete WRF job configuration.  It should hide as much
raw WRF grid complexity as possible while still producing transparent,
reproducible settings.

The catalogue is intentionally local JSON for now.  It can later be backed by a
larger event database, but the first web UI should be able to load these files
without network access.

## Files

```text
workbench/events/catalogue.json      # Named weather events
workbench/presets/domains.json       # Reusable WRF domain presets
workbench/presets/resolutions.json   # Runtime/resolution classes
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

## How the UI should use the catalogue

1. Load `catalogue.json`, `domains.json` and `resolutions.json`.
2. Build a search index from `id`, `name`, `aliases` and `event_type`.
3. When the user selects an event, load `period`, `default_domain`, `domains`,
   `default_resolution_preset` and `suggested_outputs`.
4. Populate the form using the selected domain preset.
5. Show alternative domain/resolution choices with warning levels.
6. Generate a Workbench job config by copying the selected preset values into
   the job `domain` object.
7. Validate the generated config with `workbench/validate.py` before enabling
   job execution.

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

`ci/test-event-catalogue.sh` validates:

- all required event fields
- aliases and searchability
- valid UTC periods with start before end
- default domain and listed domains exist
- resolution presets exist
- domain coordinates/dimensions/bounds are valid
- Xaver has multiple domain choices
- a generated Xaver Workbench config passes `workbench/validate.py`
