#!/usr/bin/env python3
"""
WRF Point Timeseries Extractor

Extract a timeseries of weather variables at a given latitude/longitude
from WRF output files or a wrf-vis-fixture JSON file.

Usage:
    python3 extract_point.py --input <dir>  --lat 54.0 --lon 9.0
    python3 extract_point.py --fixture <file> --lat 54.0 --lon 9.0 [--format csv]
    python3 extract_point.py --demo          --lat 54.0 --lon 9.0

Output (default JSON):
    {
      "requested_lat": 54.0,
      "requested_lon": 9.0,
      "grid_j": 2,
      "grid_i": 2,
      "grid_lat": 54.0,
      "grid_lon": 9.0,
      "timeseries": [
        {"time": "2013-12-05T00:00:00Z", "wind10m": 9.1, "t2": 278.3, ...},
        ...
      ]
    }

Or CSV when --format csv:
    time,wind10m,t2,psfc,rainnc
    2013-12-05T00:00:00Z,9.1,...
"""

import argparse
import json
import sys
from pathlib import Path

# Reuse helpers from postprocess.py
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from postprocess import (  # noqa: E402
    load_fixture_json,
    load_wrf_netcdf,
    compute_layers,
    extract_point_timeseries,
    timeseries_to_csv,
)


def main():
    parser = argparse.ArgumentParser(
        description="Extract a point timeseries from WRF output or fixture data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    source = parser.add_mutually_exclusive_group(required=True)
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
        help="Use the built-in demo fixture",
    )

    parser.add_argument(
        "--lat", type=float, required=True, metavar="LAT",
        help="Latitude of the point (degrees north)",
    )
    parser.add_argument(
        "--lon", type=float, required=True, metavar="LON",
        help="Longitude of the point (degrees east)",
    )
    parser.add_argument(
        "--format", choices=["json", "csv"], default="json",
        dest="fmt",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Write output to FILE (default: stdout)",
    )

    args = parser.parse_args()

    try:
        if args.demo:
            data = load_fixture_json(None)
        elif args.fixture:
            data = load_fixture_json(args.fixture)
        else:
            data = load_wrf_netcdf(args.input)
    except (FileNotFoundError, ImportError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    layers = compute_layers(data)
    ts = extract_point_timeseries(data, layers, args.lat, args.lon)

    if args.fmt == "csv":
        output_text = timeseries_to_csv(ts)
    else:
        output_text = json.dumps(ts, indent=2)

    if args.output:
        Path(args.output).write_text(output_text, encoding="utf-8")
        print(f"Wrote to {args.output}", file=sys.stderr)
    else:
        print(output_text)


if __name__ == "__main__":
    main()
