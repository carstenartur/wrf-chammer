# Guided simulation wizard

The guided simulation wizard is the user-facing path for describing a WRF job without editing JSON or WRF/WPS namelists.

This first implementation slice focuses on domain planning and transparent resource estimates. It does not yet execute a real ERA5/WPS/WRF simulation.

## Start the Workbench

```bash
python3 wrf-chammer start
```

Open:

```text
http://127.0.0.1:8080/
```

The page contains a **Guided simulation planning** section below the existing event workflow.

## Xaver reference selection

The default values cover an interesting Storm Xaver region:

```text
West:   2.0° E
South: 51.0° N
East:  14.0° E
North: 58.0° N
Start: 2013-12-05 12:00 UTC
End:   2013-12-06 06:00 UTC
Profile: balanced regional (9 km)
```

The OpenStreetMap preview provides real geographic context. The map is not used to invent weather data; it only helps choose the simulation domain.

## Server-side planning

The browser sends geographic bounds and a quality profile to:

```http
POST /api/domain/plan
```

or requests a complete validated dry-run preview through:

```http
POST /api/wizard/preview
```

The server derives:

- domain center,
- physical width and height,
- WRF grid spacing,
- `e_we` and `e_sn`,
- a conservative integration time step,
- horizontal and three-dimensional grid sizes,
- estimated ERA5 input size,
- estimated WRF output and working storage,
- minimum and recommended RAM,
- an approximate wall-clock range.

Grid dimensions are aligned so that `e_we - 1` and `e_sn - 1` are divisible by six. This improves compatibility with common WRF process decompositions.

## Quality profiles

| Profile | Grid spacing | Intended use |
|---|---:|---|
| `quick-preview` | 27 km | Cheap large-scale overview |
| `balanced` | 9 km | Regional synoptic structures such as Xaver |
| `detailed` | 3 km | Smaller domains on capable machines |

The expert controls can override grid spacing, vertical levels and output interval. The server validates all overrides.

## Estimates are not guarantees

The resource values are planning estimates based on grid size, simulation duration, output cadence and a reference eight-core CPU. They must later be calibrated using measured real runs from issues #37, #46 and #48.

The UI explicitly keeps these statements visible:

- scientific suitability still depends on physics and boundary conditions,
- a model result is not an observation,
- the estimate does not guarantee runtime or memory consumption,
- finer resolution can become expensive very quickly.

## Current limitation

The wizard currently creates and can execute a validated **dry-run** configuration. Real ERA5 acquisition, persistent workers and real WPS/WRF execution are implemented in the following roadmap issues:

- #44 ERA5 data acquisition and cache,
- #45 persistent job orchestration,
- #46 real WPS/WRF execution,
- #47 integrated result maps.
