# Result viewer geography, point distance and exports

The integrated per-job viewer adds map orientation and reproducible exports without changing or synthesizing the underlying WRF values.

## North-up rendering

WRF's horizontal `j` index increases toward the north. The original canvas placed row zero at the top and therefore displayed the north-south axis inverted. The integrated tool renderer maps:

```text
canvas top    → highest j / northern model row
canvas bottom → j = 0 / southern model row
```

Mouse selection, point coordinates, geography and exports use the same orientation.

## Geographic context

The result canvas includes:

- longitude/latitude graticule,
- country/coastline outlines,
- geographic labels,
- a toggle that can hide or show the context layer.

The first bundled Natural Earth 1:110m subset covers the documented North Sea/Xaver area: Belgium, Netherlands, Germany, Denmark and southern Sweden. Natural Earth data is public domain. Domains outside this area still receive a coordinate graticule, but a complete worldwide administrative basemap remains follow-up work.

Geographic context is a display overlay only. It is not weather input and does not alter result values.

## Point inspection and distance

A map click stores:

- clicked latitude/longitude,
- nearest model-grid `i`/`j`,
- grid-point latitude/longitude,
- great-circle distance from click to grid point,
- coordinate source (`model-coordinate-layer` or linear bounds fallback).

The distance is shown explicitly so the interface does not imply more spatial precision than the model grid provides.

## Exports

### PNG

Exports the currently visible map including:

- active WRF layer,
- selected time or explicit period-maximum label,
- unit and value range,
- borders/graticule when enabled,
- job ID,
- statement that the image is a model result and not an observation.

### Point CSV

After selecting a grid point, CSV contains one row per active-layer time step with:

- job and layer IDs,
- label and unit,
- simulation time,
- grid indices,
- grid and clicked coordinates,
- click-to-grid distance,
- coordinate source,
- value,
- explicit `model_result=true` and `observation=false` markers.

For a temporal-maximum layer, the time is `maximum-over-simulation-period` rather than an invented instant.

### GeoJSON

Exports the current layer/time as point features at model grid points. Each feature contains job, layer, unit, time, grid indices, coordinate source and value. The export is limited to 100,000 points to avoid accidentally freezing the browser or generating misleadingly huge files.

### Run configuration and provenance

Downloads one JSON document containing:

- the checksum-verified result manifest,
- WRF output provenance,
- immutable run specification,
- repository revision,
- ERA5 plan,
- pinned WPS/WRF/postprocessing runtime identities.

## Security boundary

The map tools are a fixed same-origin Workbench asset. They do not load OpenStreetMap, CDNs or remote scripts. Weather layers remain available only through the checksum-indexed result service introduced by PR #79.
