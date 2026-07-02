#!/usr/bin/env python3
"""Generate a high-resolution WRF-shaped fixture for documentation screenshots.

The small checked-in demo fixture is intentionally fast.  This generator creates a
larger synthetic Storm-Xaver-like field on demand so documentation screenshots
show coherent weather-map structures without requiring real WRF output or heavy
Python dependencies.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def _grid(nx: int, ny: int, west: float, south: float, east: float, north: float):
    xlat = []
    xlong = []
    for j in range(ny):
        y = j / max(ny - 1, 1)
        lat = south + (north - south) * y
        lat_row = []
        lon_row = []
        for i in range(nx):
            x = i / max(nx - 1, 1)
            lon = west + (east - west) * x
            lat_row.append(round(lat, 5))
            lon_row.append(round(lon, 5))
        xlat.append(lat_row)
        xlong.append(lon_row)
    return xlat, xlong


def _gauss(x: float, y: float, cx: float, cy: float, sx: float, sy: float) -> float:
    return math.exp(-(((x - cx) / sx) ** 2 + ((y - cy) / sy) ** 2))


def _make_fields(nx: int, ny: int, nt: int):
    u10 = []
    v10 = []
    t2 = []
    psfc = []
    rainnc = []

    for t in range(nt):
        phase = t / max(nt - 1, 1)
        u_frame = []
        v_frame = []
        t_frame = []
        p_frame = []
        r_frame = []
        low_x = 0.28 + 0.38 * phase
        low_y = 0.72 - 0.14 * phase
        front_x = 0.18 + 0.52 * phase

        for j in range(ny):
            y = j / max(ny - 1, 1)
            u_row = []
            v_row = []
            t_row = []
            p_row = []
            r_row = []
            for i in range(nx):
                x = i / max(nx - 1, 1)
                dx = x - low_x
                dy = y - low_y
                radius2 = dx * dx + dy * dy + 0.008
                swirl = 1.0 / radius2
                jet = _gauss(x, y, front_x, 0.55, 0.10, 0.25)
                coastal_band = _gauss(x, y, 0.55, 0.82, 0.45, 0.10)
                lee_wave = math.sin(32.0 * x + 9.0 * y + 2.2 * t) * math.cos(14.0 * y - 0.7 * t)

                u = 9.0 + 0.045 * swirl * (-dy) + 13.0 * jet + 3.0 * coastal_band + 1.8 * lee_wave
                v = 2.0 + 0.040 * swirl * dx + 6.5 * jet + 1.0 * math.sin(18.0 * y + t)
                pressure_low = 4200.0 * _gauss(x, y, low_x, low_y, 0.20, 0.16)
                pressure_wave = 260.0 * math.sin(10.0 * x + 3.0 * t) * math.cos(7.0 * y)
                temp_front = 5.5 * math.tanh((x - front_x) * 10.0)
                rain_band = max(0.0, 18.0 * jet + 5.0 * coastal_band + 2.0 * lee_wave)

                u_row.append(round(u, 4))
                v_row.append(round(v, 4))
                t_row.append(round(280.0 + temp_front - 3.0 * y + 1.2 * math.sin(8.0 * x + t), 4))
                p_row.append(round(101400.0 - pressure_low + pressure_wave, 4))
                r_row.append(round(max(0.0, phase * rain_band), 4))

            u_frame.append(u_row)
            v_frame.append(v_row)
            t_frame.append(t_row)
            p_frame.append(p_row)
            r_frame.append(r_row)

        u10.append(u_frame)
        v10.append(v_frame)
        t2.append(t_frame)
        psfc.append(p_frame)
        rainnc.append(r_frame)

    return u10, v10, t2, psfc, rainnc


def build_fixture(nx: int, ny: int, nt: int) -> dict:
    west, south, east, north = 4.8, 50.5, 13.8, 56.3
    xlat, xlong = _grid(nx, ny, west, south, east, north)
    u10, v10, t2, psfc, rainnc = _make_fields(nx, ny, nt)
    times = [f"2013-12-05T{hour:02d}:00:00Z" for hour in range(0, nt * 3, 3)]
    return {
        "format": "wrf-vis-fixture",
        "version": "1.0",
        "job_id": "xaver-hires-doc-map",
        "domain": {
            "projection": "Lambert Conformal",
            "center_lat": 53.4,
            "center_lon": 9.2,
            "nx": nx,
            "ny": ny,
            "dx": 3000,
            "dy": 3000,
            "bounds": [west, south, east, north],
        },
        "times": times,
        "variables": {
            "U10": {"units": "m s-1", "data": u10},
            "V10": {"units": "m s-1", "data": v10},
            "T2": {"units": "K", "data": t2},
            "PSFC": {"units": "Pa", "data": psfc},
            "RAINNC": {"units": "mm", "data": rainnc},
            "XLAT": {"units": "degrees_north", "data": xlat},
            "XLONG": {"units": "degrees_east", "data": xlong},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a high-resolution WRF visualization fixture")
    parser.add_argument("--output", required=True, help="Fixture JSON output path")
    parser.add_argument("--nx", type=int, default=220, help="Number of west-east grid points")
    parser.add_argument("--ny", type=int, default=150, help="Number of south-north grid points")
    parser.add_argument("--nt", type=int, default=8, help="Number of time steps")
    args = parser.parse_args()

    fixture = build_fixture(args.nx, args.ny, args.nt)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote high-resolution fixture: {out} ({args.nx}x{args.ny}, {args.nt} times)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
