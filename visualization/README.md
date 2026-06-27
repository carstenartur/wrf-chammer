# WRF Visualization MVP

This directory contains the visualization layer for the WRF Workbench.
It converts WRF simulation output into web-friendly artifacts and provides a
minimal interactive browser viewer for time-based weather layers, point
inspection, and maximum-value products.

---

## Directory layout

```
visualization/
  README.md                  — this file
  postprocess/
    postprocess.py           — main postprocessing script (Python 3 stdlib)
    extract_point.py         — point timeseries extractor
    run.sh                   — shell wrapper for postprocess.py
    run-demo.sh              — demo mode (no WRF run needed)
    extract-point.sh         — shell wrapper for extract_point.py
  web/
    index.html               — browser viewer (vanilla JS, no CDN)
    serve.sh                 — simple HTTP server for local use
  examples/
    demo-fixture.json        — synthetic WRF-shaped fixture (Storm Xaver domain)
  tests/
    test_visualization.sh    — CI test suite (stdlib only, no extra deps)
```

The **postprocess/** directory is the server/postprocessing side.
The **web/** directory is the frontend.

---

## Quick start

### 1. Generate artifacts from the built-in demo fixture

No WRF run, no extra Python packages required:

```bash
./visualization/postprocess/run-demo.sh
```

This writes artifacts to `visualization/demo-output/`.

### 2. Open the browser viewer

```bash
./visualization/web/serve.sh
```

Then open http://localhost:8080 in any browser.

---

## Full pipeline (real WRF output)

```bash
# After a WRF run produces wrfout_* files:
./visualization/postprocess/run.sh \
    --input  workbench-runs/xaver-demo/outputs \
    --output workbench-runs/xaver-demo/visualizations

# Serve the viewer:
./visualization/web/serve.sh workbench-runs/xaver-demo/visualizations
```

**Dependencies** for real WRF NetCDF input:

```bash
pip install netcdf4 numpy
# or:
pip install scipy numpy
```

The demo/fixture mode uses only Python 3 stdlib and needs nothing extra.

---

## Input assumptions

### Real WRF output

- Files named `wrfout_d01_*` (or `wrfout_d*`) in the input directory
- NetCDF3 Classic or NetCDF4 format (WRF default)
- Required variables (missing variables produce a warning, not a failure):
  - `Times` — time coordinate (char array `YYYY-MM-DD_HH:MM:SS`)
  - `U10`, `V10` — 10 m wind components (m s⁻¹)
  - `T2` — 2 m temperature (K)
  - `PSFC` — surface pressure (Pa)
  - `RAINNC` — accumulated non-convective precipitation (mm)
  - `XLAT`, `XLONG` — grid latitude/longitude (degrees)
- Global attributes used: `CEN_LAT`, `CEN_LON`, `DX`, `DY`, `MAP_PROJ_CHAR`

### wrf-vis-fixture JSON

A small JSON file shaped like WRF output; see
`visualization/examples/demo-fixture.json` for the schema.
Used for testing and demo without a real WRF run.

---

## Output artifact format

```
<output-dir>/
  metadata.json        — job/domain/layer catalogue
  layers/
    wind10m.json       — 10 m wind speed, time-varying
    t2.json            — 2 m temperature, time-varying
    psfc.json          — surface pressure, time-varying
    rainnc.json        — accumulated precipitation, time-varying
    max_wind10m.json   — maximum wind speed (all time steps)
    max_t2.json        — maximum 2 m temperature
    max_psfc.json      — maximum surface pressure
    max_rainnc.json    — maximum accumulated precipitation
    xlat.json          — latitude grid (optional)
    xlong.json         — longitude grid (optional)
```

### metadata.json schema

```json
{
  "jobId": "xaver-demo",
  "domain": {
    "projection": "Lambert Conformal",
    "bounds": [west, south, east, north],
    "nx": 50,
    "ny": 50,
    "dx": 9000,
    "dy": 9000,
    "center_lat": 54.0,
    "center_lon": 9.0
  },
  "times": ["2013-12-05T00:00:00Z", "..."],
  "layers": [
    {
      "id": "wind10m",
      "label": "10 m wind speed",
      "unit": "m s-1",
      "type": "raster-time-series",
      "file": "layers/wind10m.json",
      "vmin": 0.0,
      "vmax": 25.0
    },
    {
      "id": "max_wind10m",
      "label": "Maximum 10 m wind speed",
      "unit": "m s-1",
      "type": "raster-max",
      "file": "layers/max_wind10m.json",
      "vmin": 5.0,
      "vmax": 25.0
    }
  ]
}
```

### Time-varying layer schema (`layers/*.json`)

```json
{
  "id": "wind10m",
  "label": "10 m wind speed",
  "unit": "m s-1",
  "type": "raster-time-series",
  "nx": 50,
  "ny": 50,
  "vmin": 0.0,
  "vmax": 25.0,
  "times": ["2013-12-05T00:00:00Z", "..."],
  "frames": [
    [[...], ...],
    ...
  ]
}
```

### Maximum-value product schema

```json
{
  "id": "max_wind10m",
  "type": "raster-max",
  "unit": "m s-1",
  "source_layer": "wind10m",
  "source_times": ["..."],
  "data": [[...], ...]
}
```

---

## Supported derived products

| Layer ID      | Description               | Source variables | Unit    |
|---------------|---------------------------|------------------|---------|
| `wind10m`     | 10 m wind speed           | `U10`, `V10`     | m s⁻¹  |
| `t2`          | 2 m temperature           | `T2`             | K       |
| `psfc`        | Surface pressure          | `PSFC`           | Pa      |
| `rainnc`      | Accumulated precipitation | `RAINNC`         | mm      |
| `max_wind10m` | Maximum wind speed        | (from `wind10m`) | m s⁻¹  |
| `max_t2`      | Maximum temperature       | (from `t2`)      | K       |
| `max_psfc`    | Maximum pressure          | (from `psfc`)    | Pa      |
| `max_rainnc`  | Maximum precipitation     | (from `rainnc`)  | mm      |

Wind speed is derived as: `wind10m = sqrt(U10² + V10²)`

Missing variables produce a warning to stderr; the remaining layers are
still produced.

---

## Point timeseries extraction

Extract a weather variable timeseries at a given latitude/longitude:

```bash
# Demo mode (no WRF output needed):
./visualization/postprocess/extract-point.sh \
    --demo --lat 54.0 --lon 9.0

# Real WRF output:
./visualization/postprocess/extract-point.sh \
    --input workbench-runs/xaver-demo/outputs \
    --lat 54.0 --lon 9.0

# CSV format:
./visualization/postprocess/extract-point.sh \
    --demo --lat 54.0 --lon 9.0 --format csv
```

The nearest grid point to the requested coordinates is found automatically.

**JSON output example:**

```json
{
  "requested_lat": 54.0,
  "requested_lon": 9.0,
  "grid_j": 2,
  "grid_i": 2,
  "grid_lat": 54.5,
  "grid_lon": 9.0,
  "timeseries": [
    {"time": "2013-12-05T00:00:00Z", "wind10m": 9.16, "t2": 278.3, "psfc": 99500.0, "rainnc": 0.0},
    ...
  ]
}
```

**CSV output example:**

```
time,wind10m,t2,psfc,rainnc
2013-12-05T00:00:00Z,9.16,278.3,99500.0,0.0
...
```

---

## Browser viewer

The viewer (`visualization/web/index.html`) is a self-contained static HTML
file using only vanilla JavaScript and the HTML5 Canvas API.

**Capabilities:**

- Loads `metadata.json` from the same server directory
- Shows the layer selector (sidebar)
- Renders the current layer as a colour-mapped grid (canvas)
- Layer and timestep selection
- Play/Pause animation
- Mouse hover — shows grid value and approximate coordinates in tooltip
- Click inspection — shows full timeseries for all loaded layers at the clicked point
- Maximum-value products displayed alongside time-varying layers

**No CDN, framework, or internet connection required.**

### Serve locally

```bash
./visualization/web/serve.sh [OUTPUT_DIR] [PORT]
# Default: visualization/demo-output, port 8080
```

---

## Known limitations

- The postprocessor reads the full layer JSON into memory; very large WRF
  domains may require chunked processing (planned for a later issue).
- The browser viewer renders data as a pixel grid — no real map projection or
  reprojection. Geographic distortion is present for large or high-latitude
  domains.
- Colour scales are fixed per layer type; no user-configurable stretch.
- The viewer uses in-memory caching; navigating back to a layer does not
  re-fetch it from disk, but the cache is not persisted between page reloads.
- Only NetCDF3 Classic and simple NetCDF4 layouts are supported; WRF output
  with `io_form_history = 11` (multiple files per time) has not been tested.
- No authentication or access control — intended for local/internal use only.

---

## Future extension path toward 3D and buildings

This MVP establishes the contract between postprocessing output and browser
viewer.  The following extension steps are planned for later issues:

### Terrain and 3D globe (CesiumJS)

The `metadata.json` and layer JSON files are designed to be consumed by
additional renderers.  A future 3D viewer would:

1. Replace `index.html` with a CesiumJS-based viewer.
2. Convert layer grids to [3D Tiles](https://cesium.com/open-maturity-model/)
   or GeoTIFF for terrain-draped rendering.
3. Obtain terrain height data (e.g. Copernicus DEM) to drape weather layers
   onto real topography.

### Cloud-optimised GeoTIFF (COG)

When numpy and GDAL/rasterio are available, the postprocessor can be extended
to write COG files instead of JSON grids.  The viewer would then use a
COG-aware tile source (e.g. [georaster-layer-for-leaflet](https://github.com/GeoTIFF/georaster-layer-for-leaflet)).

### Vertical cross sections

WRF output contains 3-D variables (e.g. `W`, `U`, `V` on model levels).
A future `extract_crosssection.py` could:

1. Interpolate onto a vertical pressure coordinate.
2. Export a 2-D slice (horizontal distance × height) as a JSON array.
3. Render in a second canvas panel in the viewer.

### Time-dependent wind volumes

Volume rendering of 3-D wind fields can be achieved with:

- A fragment shader in WebGL (Three.js or CesiumJS).
- Streamline or particle advection computed server-side and exported as a
  series of polylines.

### Building-scale integration

When building geometry (CityGML or OSM LoD2) is available:

1. 3D Tiles can encode building meshes for CesiumJS.
2. WRF output supplies the boundary conditions (inlet wind, temperature).
3. A CFD solver (OpenFOAM) can be wrapped around individual buildings using
   the WRF profile as forcing — this is outside the scope of the weather
   visualisation layer but the data contract (JSON timeseries per point) is
   already defined.

---

## Running tests

```bash
# All tests, stdlib only, no extra packages:
sh visualization/tests/test_visualization.sh
```

Or via the CI workflow (added at `.github/workflows/visualization-tests.yml`).

---

## Relation to the Workbench

The visualization layer consumes outputs from Workbench run directories:

```
workbench-runs/<job-id>/
  job.json
  status.json
  outputs/
    wrfout_d01_*          ← input for postprocess.py
  visualizations/         ← output from postprocess.py
    metadata.json
    layers/
```

The visualization does not depend on the Workbench internals; it only needs
the `outputs/` directory.  A standalone demo mode (`--demo`) works without
any Workbench run.
