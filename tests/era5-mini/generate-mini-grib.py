#!/usr/bin/env python3
"""Generate minimal ECMWF GRIB1 test files for the ERA5/WPS integration test.

These files are already committed as pressure.grib and surface.grib in this
directory.  Re-run this script only if you need to regenerate them (e.g. after
changing the domain or the variable set).

Requirements:
    pip install eccodes numpy

Output files (written to the directory containing this script):
    pressure.grib  – 10 GRIB1 messages at 500 hPa and 850 hPa
    surface.grib   –  6 GRIB1 messages for near-surface fields

Grid: 3×3 regular lat/lon, 2-degree spacing, 48–52 N, 8–12 E
Time: 2024-01-15 00:00 UTC, single time step
"""

from pathlib import Path
import numpy as np
import eccodes

HERE = Path(__file__).parent.resolve()

# ── Grid parameters ─────────────────────────────────────────────────────────
NI = 3        # longitude points
NJ = 3        # latitude points
LAT_FIRST = 52.0
LAT_LAST  = 48.0   # scanning north → south
LON_FIRST = 8.0
LON_LAST  = 12.0
DI = 2.0           # iDirectionIncrement (degrees)
DJ = 2.0           # jDirectionIncrement (degrees)

# ── Reference time ───────────────────────────────────────────────────────────
YEAR, MONTH, DAY, HOUR = 2024, 1, 15, 0


def _set_grid(sid):
    eccodes.codes_set(sid, "edition", 1)
    eccodes.codes_set(sid, "centre", 98)  # ECMWF
    eccodes.codes_set(sid, "dataDate", YEAR * 10000 + MONTH * 100 + DAY)
    eccodes.codes_set(sid, "dataTime", HOUR * 100)
    eccodes.codes_set(sid, "stepRange", "0")
    eccodes.codes_set(sid, "Ni", NI)
    eccodes.codes_set(sid, "Nj", NJ)
    eccodes.codes_set(sid, "latitudeOfFirstGridPointInDegrees", LAT_FIRST)
    eccodes.codes_set(sid, "latitudeOfLastGridPointInDegrees", LAT_LAST)
    eccodes.codes_set(sid, "longitudeOfFirstGridPointInDegrees", LON_FIRST)
    eccodes.codes_set(sid, "longitudeOfLastGridPointInDegrees", LON_LAST)
    eccodes.codes_set(sid, "iDirectionIncrementInDegrees", DI)
    eccodes.codes_set(sid, "jDirectionIncrementInDegrees", DJ)


def make_pl_msg(param_id, level_hpa, fill_value):
    """Create a single pressure-level GRIB1 message."""
    sid = eccodes.codes_grib_new_from_samples("regular_ll_pl_grib1")
    _set_grid(sid)
    eccodes.codes_set(sid, "paramId", param_id)
    eccodes.codes_set(sid, "typeOfLevel", "isobaricInhPa")
    eccodes.codes_set(sid, "level", level_hpa)
    eccodes.codes_set_values(sid, np.full(NI * NJ, fill_value).tolist())
    return sid


def make_sfc_msg(param_id, type_of_level, level_val, fill_value):
    """Create a single surface/near-surface GRIB1 message."""
    sid = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib1")
    _set_grid(sid)
    eccodes.codes_set(sid, "paramId", param_id)
    eccodes.codes_set(sid, "typeOfLevel", type_of_level)
    eccodes.codes_set(sid, "level", level_val)
    eccodes.codes_set_values(sid, np.full(NI * NJ, fill_value).tolist())
    return sid


def write_pressure_grib():
    """Write pressure.grib with pressure-level fields at 500 and 850 hPa."""
    # (paramId, fill_value)
    # 129 = z  (geopotential m²/s²)
    # 130 = t  (temperature K)
    # 131 = u  (u-wind m/s)
    # 132 = v  (v-wind m/s)
    # 133 = q  (specific humidity kg/kg)
    pl_params = [
        (129, 50000.0),
        (130,   270.0),
        (131,    10.0),
        (132,     5.0),
        (133,     0.001),
    ]
    out = HERE / "pressure.grib"
    with out.open("wb") as f:
        for level in [500, 850]:
            for param_id, fill_value in pl_params:
                sid = make_pl_msg(param_id, level, fill_value)
                eccodes.codes_write(sid, f)
                eccodes.codes_release(sid)
    print(f"Wrote {out} ({out.stat().st_size} bytes, 10 messages)")


def write_surface_grib():
    """Write surface.grib with near-surface fields."""
    # (paramId, typeOfLevel, level, fill_value)
    # 134 = sp  (surface pressure Pa)
    # 165 = 10u (10m u-wind m/s)
    # 166 = 10v (10m v-wind m/s)
    # 167 = 2t  (2m temperature K)
    # 168 = 2d  (2m dewpoint K)
    # 235 = skt (skin temperature K)
    sfc_params = [
        (134, "surface",          0, 101325.0),
        (165, "heightAboveGround", 10,     5.0),
        (166, "heightAboveGround", 10,     2.0),
        (167, "heightAboveGround",  2,   275.0),
        (168, "heightAboveGround",  2,   273.0),
        (235, "surface",           0,   280.0),
    ]
    out = HERE / "surface.grib"
    with out.open("wb") as f:
        for param_id, tol, level_val, fill_value in sfc_params:
            sid = make_sfc_msg(param_id, tol, level_val, fill_value)
            eccodes.codes_write(sid, f)
            eccodes.codes_release(sid)
    print(f"Wrote {out} ({out.stat().st_size} bytes, 6 messages)")


if __name__ == "__main__":
    write_pressure_grib()
    write_surface_grib()
    print("Done.")
