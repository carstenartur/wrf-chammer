#!/usr/bin/env python3
"""
WRF Visualization Postprocessor

Converts WRF output files (or a synthetic JSON fixture) into web-friendly
visualization artifacts consumed by the browser viewer.

Usage:
    # Process a directory containing wrfout_* NetCDF files (requires netCDF4 + numpy):
    python3 postprocess.py --input workbench-runs/xaver-demo/outputs \\
                           --output workbench-runs/xaver-demo/visualizations

    # Demo mode using the built-in synthetic fixture (no extra dependencies):
    python3 postprocess.py --demo \\
                           --output /tmp/vis-demo

    # Use an explicit fixture JSON file:
    python3 postprocess.py --fixture visualization/examples/demo-fixture.json \\
                           --output /tmp/vis-out

Output layout:
    <output>/
        metadata.json          — job/domain/layer/time catalogue
        layers/
            wind10m.json       — 10 m wind speed (time-varying)
            t2.json            — 2 m temperature (time-varying)
            psfc.json          — surface pressure (time-varying)
            rainnc.json        — accumulated precipitation (time-varying)
            max_wind10m.json   — maximum wind speed product (single frame)

Supported input formats:
    1. wrfout_* NetCDF3/4 files — requires numpy + netCDF4 (or scipy)
    2. wrf-vis-fixture JSON     — pure Python 3 stdlib, no extra dependencies

Variables used (skipped with warning when absent):
    U10    — 10 m eastward wind component    (m s-1)
    V10    — 10 m northward wind component   (m s-1)
    T2     — 2 m temperature                 (K)
    PSFC   — surface pressure                (Pa)
    RAINNC — accumulated non-convective rain (mm)
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Optional heavy dependencies (only needed for real WRF NetCDF input)
# ---------------------------------------------------------------------------

import importlib.util


def _try_import_netcdf4():
    return importlib.util.find_spec("netCDF4") is not None


def _try_import_scipy_netcdf():
    return (
        importlib.util.find_spec("scipy") is not None
        and importlib.util.find_spec("scipy.io") is not None
    )


# ---------------------------------------------------------------------------
# Pure-Python math helpers (no numpy required)
# ---------------------------------------------------------------------------

def _sqrt2d(a, b):
    """Element-wise sqrt(a^2 + b^2) for 2-D lists."""
    return [
        [math.sqrt(a[j][i] ** 2 + b[j][i] ** 2) for i in range(len(a[j]))]
        for j in range(len(a))
    ]


def _sqrt3d(a, b):
    """Element-wise sqrt(a^2 + b^2) for 3-D lists (time, y, x)."""
    return [_sqrt2d(a[t], b[t]) for t in range(len(a))]


def _max2d(frames):
    """Return 2-D grid of maximum values across all time frames."""
    ny = len(frames[0])
    nx = len(frames[0][0])
    return [
        [max(frames[t][j][i] for t in range(len(frames))) for i in range(nx)]
        for j in range(ny)
    ]


def _flatten_2d(grid):
    """Return flat min/max of a 2-D list."""
    values = [v for row in grid for v in row]
    return min(values), max(values)


def _flatten_3d(frames):
    """Return flat min/max across all time frames."""
    values = [v for frame in frames for row in frame for v in row]
    return min(values), max(values)


def _round2d(grid, ndigits=4):
    return [[round(v, ndigits) for v in row] for row in grid]


def _round3d(frames, ndigits=4):
    return [_round2d(f, ndigits) for f in frames]


# ---------------------------------------------------------------------------
# Fixture / input readers
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent.parent / "examples" / "demo-fixture.json"


def load_fixture_json(path=None):
    """Load a wrf-vis-fixture JSON file and return a normalised data dict."""
    if path is None:
        path = _FIXTURE_PATH
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Fixture not found: {path}")
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if raw.get("format") != "wrf-vis-fixture":
        raise ValueError(f"Expected format 'wrf-vis-fixture', got: {raw.get('format')!r}")
    return raw


def load_wrf_netcdf(input_dir):
    """Load wrfout_* files from *input_dir* using netCDF4 or scipy.

    Returns a dict compatible with the fixture JSON format so that
    compute_layers() can handle both paths uniformly.
    """
    import glob

    input_dir = Path(input_dir)
    patterns = ["wrfout_d01_*", "wrfout_d*"]
    files = []
    for pat in patterns:
        files.extend(sorted(input_dir.glob(pat)))
    # De-duplicate preserving order
    seen = set()
    unique_files = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)
    files = unique_files

    if not files:
        raise FileNotFoundError(
            f"No wrfout_* files found in: {input_dir}"
        )

    print(f"Found {len(files)} WRF output file(s) in {input_dir}")

    # Prefer netCDF4, fall back to scipy
    if _try_import_netcdf4():
        return _load_wrf_netcdf4(files)
    elif _try_import_scipy_netcdf():
        return _load_wrf_scipy(files)
    else:
        raise ImportError(
            "Cannot read NetCDF files: neither 'netCDF4' nor 'scipy' is installed.\n"
            "Install with:  pip install netcdf4 numpy\n"
            "Or use --demo / --fixture for the JSON fixture mode."
        )


def _nc_var_to_list(var):
    """Convert a netCDF4 / scipy variable to a nested Python list."""
    import numpy as np
    return var[:].tolist()


def _load_wrf_netcdf4(files):
    """Read one or more wrfout files with netCDF4."""
    import netCDF4 as nc
    import numpy as np

    all_times = []
    all_vars = {}

    for fpath in files:
        print(f"  Reading {fpath.name} ...")
        ds = nc.Dataset(str(fpath))
        try:
            # Times
            times_raw = nc.chartostring(ds.variables["Times"][:])
            for t in times_raw:
                ts = t.strip().replace("_", "T") + "Z"
                all_times.append(ts)

            static_vars = {"XLAT", "XLONG"}
            for vname in ("U10", "V10", "T2", "PSFC", "RAINNC", "XLAT", "XLONG"):
                if vname not in ds.variables:
                    continue
                v = ds.variables[vname]
                arr = v[:].tolist()
                units = getattr(v, "units", "")
                if vname in static_vars:
                    # Take the first time slice if present
                    if isinstance(arr[0][0], list):
                        arr = arr[0]
                    all_vars.setdefault(vname, {"units": units, "data": arr})
                else:
                    if vname not in all_vars:
                        all_vars[vname] = {"units": units, "data": arr}
                    else:
                        all_vars[vname]["data"].extend(arr)

            # Domain metadata from first file
            domain_meta = {
                "CEN_LAT": float(getattr(ds, "CEN_LAT", 0)),
                "CEN_LON": float(getattr(ds, "CEN_LON", 0)),
                "DX": float(getattr(ds, "DX", 0)),
                "DY": float(getattr(ds, "DY", 0)),
                "MAP_PROJ_CHAR": getattr(ds, "MAP_PROJ_CHAR", ""),
            }
        finally:
            ds.close()

    xlat = all_vars.get("XLAT", {}).get("data", [])
    xlong = all_vars.get("XLONG", {}).get("data", [])
    ny = len(xlat) if xlat else 0
    nx = len(xlat[0]) if xlat and xlat[0] else 0

    # Compute geographic bounds
    flat_lat = [v for row in xlat for v in row] if xlat else [0, 0]
    flat_lon = [v for row in xlong for v in row] if xlong else [0, 0]
    bounds = [min(flat_lon), min(flat_lat), max(flat_lon), max(flat_lat)]

    return {
        "format": "wrf-vis-fixture",
        "version": "1.0",
        "job_id": "wrf-output",
        "domain": {
            "projection": domain_meta.get("MAP_PROJ_CHAR", "Lambert Conformal"),
            "center_lat": domain_meta.get("CEN_LAT", 0),
            "center_lon": domain_meta.get("CEN_LON", 0),
            "nx": nx,
            "ny": ny,
            "dx": domain_meta.get("DX", 0),
            "dy": domain_meta.get("DY", 0),
            "bounds": bounds,
        },
        "times": all_times,
        "variables": all_vars,
    }


def _load_wrf_scipy(files):
    """Read one or more wrfout files with scipy.io.netcdf."""
    from scipy.io import netcdf_file
    import numpy as np

    all_times = []
    all_vars = {}
    domain_meta = {}

    for fpath in files:
        print(f"  Reading {fpath.name} (scipy) ...")
        f = netcdf_file(str(fpath), "r", mmap=False)
        try:
            times_raw = f.variables["Times"][:]
            for t in times_raw:
                ts = t.tobytes().decode("ascii").strip().replace("_", "T") + "Z"
                all_times.append(ts)

            for attr in ("CEN_LAT", "CEN_LON", "DX", "DY", "MAP_PROJ_CHAR"):
                if hasattr(f, attr):
                    domain_meta[attr] = getattr(f, attr)

            static_vars = {"XLAT", "XLONG"}
            for vname in ("U10", "V10", "T2", "PSFC", "RAINNC", "XLAT", "XLONG"):
                if vname not in f.variables:
                    continue
                v = f.variables[vname]
                arr = v[:].tolist()
                units = getattr(v, "units", b"")
                if isinstance(units, bytes):
                    units = units.decode("ascii", errors="replace")
                if vname in static_vars:
                    if isinstance(arr[0][0], list):
                        arr = arr[0]
                    all_vars.setdefault(vname, {"units": units, "data": arr})
                else:
                    if vname not in all_vars:
                        all_vars[vname] = {"units": units, "data": arr}
                    else:
                        all_vars[vname]["data"].extend(arr)
        finally:
            f.close()

    xlat = all_vars.get("XLAT", {}).get("data", [])
    xlong = all_vars.get("XLONG", {}).get("data", [])
    ny = len(xlat) if xlat else 0
    nx = len(xlat[0]) if xlat and xlat[0] else 0
    flat_lat = [v for row in xlat for v in row] if xlat else [0, 0]
    flat_lon = [v for row in xlong for v in row] if xlong else [0, 0]
    bounds = [min(flat_lon), min(flat_lat), max(flat_lon), max(flat_lat)]

    cen_lat = domain_meta.get("CEN_LAT", 0)
    cen_lon = domain_meta.get("CEN_LON", 0)
    if hasattr(cen_lat, "item"):
        cen_lat = float(cen_lat.item())
    if hasattr(cen_lon, "item"):
        cen_lon = float(cen_lon.item())

    return {
        "format": "wrf-vis-fixture",
        "version": "1.0",
        "job_id": "wrf-output",
        "domain": {
            "projection": "Lambert Conformal",
            "center_lat": float(cen_lat),
            "center_lon": float(cen_lon),
            "nx": nx,
            "ny": ny,
            "dx": float(domain_meta.get("DX", 0)),
            "dy": float(domain_meta.get("DY", 0)),
            "bounds": bounds,
        },
        "times": all_times,
        "variables": all_vars,
    }


# ---------------------------------------------------------------------------
# Layer computation
# ---------------------------------------------------------------------------

LAYER_SPECS = [
    {
        "id": "wind10m",
        "label": "10 m wind speed",
        "unit": "m s-1",
        "type": "raster-time-series",
        "requires": ["U10", "V10"],
    },
    {
        "id": "t2",
        "label": "2 m temperature",
        "unit": "K",
        "type": "raster-time-series",
        "requires": ["T2"],
    },
    {
        "id": "psfc",
        "label": "Surface pressure",
        "unit": "Pa",
        "type": "raster-time-series",
        "requires": ["PSFC"],
    },
    {
        "id": "rainnc",
        "label": "Accumulated precipitation",
        "unit": "mm",
        "type": "raster-time-series",
        "requires": ["RAINNC"],
    },
]


def compute_layers(data):
    """Compute all available derived layers from the normalised data dict.

    Returns a dict mapping layer_id -> layer artifact dict.
    Layers for which required variables are absent are skipped with a warning.
    """
    variables = data.get("variables", {})
    times = data.get("times", [])
    layers = {}

    for spec in LAYER_SPECS:
        lid = spec["id"]
        required = spec["requires"]
        missing = [v for v in required if v not in variables]
        if missing:
            print(f"  [WARN] Skipping layer '{lid}': missing variables {missing}")
            continue

        if lid == "wind10m":
            u10 = variables["U10"]["data"]
            v10 = variables["V10"]["data"]
            frames = _round3d(_sqrt3d(u10, v10))
        else:
            vname = required[0]
            frames = _round3d(variables[vname]["data"])

        vmin, vmax = _flatten_3d(frames)
        layers[lid] = {
            "id": lid,
            "label": spec["label"],
            "unit": spec["unit"],
            "type": spec["type"],
            "nx": len(frames[0][0]) if frames and frames[0] else 0,
            "ny": len(frames[0]) if frames else 0,
            "vmin": round(vmin, 4),
            "vmax": round(vmax, 4),
            "times": times,
            "frames": frames,
        }
        print(f"  [OK]   Layer '{lid}': {len(times)} time steps, "
              f"range [{round(vmin, 2)}, {round(vmax, 2)}] {spec['unit']}")

    return layers


def compute_max_products(layers):
    """Generate maximum-value products for time-varying layers.

    Returns a dict mapping max_<layer_id> -> artifact dict.
    """
    maxlayers = {}
    for lid, layer in layers.items():
        frames = layer.get("frames", [])
        if not frames:
            continue
        max_grid = _round2d(_max2d(frames))
        vmin, vmax = _flatten_2d(max_grid)
        maxlayers[f"max_{lid}"] = {
            "id": f"max_{lid}",
            "label": f"Maximum {layer['label']}",
            "unit": layer["unit"],
            "type": "raster-max",
            "nx": layer["nx"],
            "ny": layer["ny"],
            "vmin": round(vmin, 4),
            "vmax": round(vmax, 4),
            "source_layer": lid,
            "source_times": layer["times"],
            "data": max_grid,
        }
        print(f"  [OK]   Max product 'max_{lid}': "
              f"range [{round(vmin, 2)}, {round(vmax, 2)}] {layer['unit']}")
    return maxlayers


# ---------------------------------------------------------------------------
# Artifact export
# ---------------------------------------------------------------------------

def export_metadata(output_dir, data, layers, max_layers):
    """Write metadata.json to *output_dir*."""
    output_dir = Path(output_dir)
    domain = data.get("domain", {})
    times = data.get("times", [])

    layer_catalogue = []
    for layer in layers.values():
        layer_catalogue.append({
            "id": layer["id"],
            "label": layer["label"],
            "unit": layer["unit"],
            "type": layer["type"],
            "file": f"layers/{layer['id']}.json",
            "vmin": layer["vmin"],
            "vmax": layer["vmax"],
        })
    for ml in max_layers.values():
        layer_catalogue.append({
            "id": ml["id"],
            "label": ml["label"],
            "unit": ml["unit"],
            "type": ml["type"],
            "file": f"layers/{ml['id']}.json",
            "vmin": ml["vmin"],
            "vmax": ml["vmax"],
        })

    metadata = {
        "jobId": data.get("job_id", "unknown"),
        "domain": {
            "projection": domain.get("projection", ""),
            "bounds": domain.get("bounds", [0, 0, 0, 0]),
            "nx": domain.get("nx", 0),
            "ny": domain.get("ny", 0),
            "dx": domain.get("dx", 0),
            "dy": domain.get("dy", 0),
            "center_lat": domain.get("center_lat", 0),
            "center_lon": domain.get("center_lon", 0),
        },
        "times": times,
        "layers": layer_catalogue,
    }

    out_path = output_dir / "metadata.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    print(f"  [OK]   Wrote {out_path}")
    return metadata


def export_layer_files(output_dir, layers, max_layers):
    """Write one JSON file per layer into <output_dir>/layers/."""
    layers_dir = Path(output_dir) / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    for layer in layers.values():
        out_path = layers_dir / f"{layer['id']}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(layer, f, separators=(",", ":"))
        print(f"  [OK]   Wrote {out_path}")

    for ml in max_layers.values():
        out_path = layers_dir / f"{ml['id']}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(ml, f, separators=(",", ":"))
        print(f"  [OK]   Wrote {out_path}")


def export_xlat_xlong(output_dir, data):
    """Write coordinate grids (XLAT/XLONG) as separate JSON files."""
    variables = data.get("variables", {})
    layers_dir = Path(output_dir) / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    for vname, fname in [("XLAT", "xlat.json"), ("XLONG", "xlong.json")]:
        if vname not in variables:
            continue
        out_path = layers_dir / fname
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(variables[vname]["data"], f, separators=(",", ":"))
        print(f"  [OK]   Wrote {out_path}")


# ---------------------------------------------------------------------------
# Point timeseries
# ---------------------------------------------------------------------------

def extract_point_timeseries(data, layers, lat, lon):
    """Find the nearest grid point to (lat, lon) and extract time series.

    Returns a dict with grid coordinates and per-variable time series.
    """
    variables = data.get("variables", {})
    xlat = variables.get("XLAT", {}).get("data")
    xlong = variables.get("XLONG", {}).get("data")

    if xlat and xlong:
        ny = len(xlat)
        nx = len(xlat[0])
        best_dist = float("inf")
        best_j, best_i = 0, 0
        for j in range(ny):
            for i in range(nx):
                d = (xlat[j][i] - lat) ** 2 + (xlong[j][i] - lon) ** 2
                if d < best_dist:
                    best_dist = d
                    best_j, best_i = j, i
        grid_lat = xlat[best_j][best_i]
        grid_lon = xlong[best_j][best_i]
    else:
        # Fall back to centre of domain
        domain = data.get("domain", {})
        ny = domain.get("ny", 1)
        nx = domain.get("nx", 1)
        best_j, best_i = ny // 2, nx // 2
        grid_lat = lat
        grid_lon = lon

    times = data.get("times", [])
    ts_rows = []
    for t_idx, t in enumerate(times):
        row = {"time": t}
        for lid, layer in layers.items():
            if layer.get("type") == "raster-time-series":
                frames = layer.get("frames", [])
                if t_idx < len(frames):
                    row[lid] = frames[t_idx][best_j][best_i]
        ts_rows.append(row)

    return {
        "requested_lat": lat,
        "requested_lon": lon,
        "grid_j": best_j,
        "grid_i": best_i,
        "grid_lat": grid_lat,
        "grid_lon": grid_lon,
        "timeseries": ts_rows,
    }


def timeseries_to_csv(ts_data):
    """Convert extract_point_timeseries result to CSV string."""
    rows = ts_data.get("timeseries", [])
    if not rows:
        return ""
    headers = list(rows[0].keys())
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(str(row.get(h, "")) for h in headers))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def run_postprocess(input_dir=None, fixture_path=None, output_dir=None, demo=False):
    """Run the full postprocessing pipeline."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "layers").mkdir(exist_ok=True)

    print("=== WRF Visualization Postprocessor ===")
    print()

    # Load input data
    if demo or fixture_path:
        path = fixture_path if fixture_path else None
        print(f"Loading fixture: {path or _FIXTURE_PATH}")
        data = load_fixture_json(path)
    elif input_dir:
        print(f"Loading WRF output from: {input_dir}")
        data = load_wrf_netcdf(input_dir)
    else:
        raise ValueError("Specify --input, --fixture, or --demo")

    domain = data.get("domain", {})
    times = data.get("times", [])
    print(f"Domain: {domain.get('nx', '?')} x {domain.get('ny', '?')} grid points, "
          f"{len(times)} time step(s)")
    print()

    # Compute derived layers
    print("Computing layers ...")
    layers = compute_layers(data)
    if not layers:
        print("  [WARN] No layers could be computed — check that required variables are present.")

    print()
    print("Computing maximum-value products ...")
    max_layers = compute_max_products(layers)

    print()
    print("Exporting artifacts ...")
    export_xlat_xlong(output_dir, data)
    export_layer_files(output_dir, layers, max_layers)
    metadata = export_metadata(output_dir, data, layers, max_layers)

    print()
    print(f"Done. Output written to: {output_dir}")
    print(f"  metadata.json : {len(metadata['layers'])} layer entries, "
          f"{len(times)} time steps")

    return output_dir


def main():
    parser = argparse.ArgumentParser(
        description="WRF Visualization Postprocessor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--input", "-i", metavar="DIR",
        help="Directory containing wrfout_* NetCDF files",
    )
    source.add_argument(
        "--fixture", "-f", metavar="FILE",
        help="Path to a wrf-vis-fixture JSON file",
    )
    source.add_argument(
        "--demo", action="store_true",
        help="Use the built-in demo fixture (no WRF output needed)",
    )
    parser.add_argument(
        "--output", "-o", metavar="DIR", default="visualizations",
        help="Output directory for visualization artifacts (default: %(default)s)",
    )
    parser.add_argument(
        "--point", nargs=2, metavar=("LAT", "LON"), type=float,
        help="Also extract a point timeseries at the given lat/lon",
    )
    parser.add_argument(
        "--point-format", choices=["json", "csv"], default="json",
        help="Output format for --point (default: %(default)s)",
    )

    args = parser.parse_args()

    if not args.input and not args.fixture and not args.demo:
        parser.print_help()
        sys.exit(1)

    try:
        output_dir = run_postprocess(
            input_dir=args.input,
            fixture_path=args.fixture,
            output_dir=args.output,
            demo=args.demo,
        )
    except (FileNotFoundError, ImportError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.point:
        lat, lon = args.point
        print()
        print(f"Extracting point timeseries at lat={lat}, lon={lon} ...")
        # Reload data for point extraction
        if args.demo or args.fixture:
            data = load_fixture_json(args.fixture)
        else:
            data = load_wrf_netcdf(args.input)

        # Reload layers from exported files for consistent results
        layers = compute_layers(data)
        ts = extract_point_timeseries(data, layers, lat, lon)

        ts_dir = output_dir / "timeseries"
        ts_dir.mkdir(exist_ok=True)
        fname = f"point_{lat}_{lon}"

        if args.point_format == "csv":
            out_path = ts_dir / f"{fname}.csv"
            with out_path.open("w", encoding="utf-8") as f:
                f.write(timeseries_to_csv(ts))
        else:
            out_path = ts_dir / f"{fname}.json"
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(ts, f, indent=2)

        print(f"  Point timeseries written to: {out_path}")


if __name__ == "__main__":
    main()
