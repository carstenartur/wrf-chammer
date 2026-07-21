#!/usr/bin/env python3
"""Generate a tiny synthetic WRF-like NetCDF file for compatibility tests only.

The numeric fields are deterministic test data. This file must never be presented
as a scientific simulation result or selected by the product application.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import netCDF4
import numpy as np


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic synthetic WRF-like NetCDF fixture"
    )
    parser.add_argument("output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    output = build_parser().parse_args(argv).output
    output.parent.mkdir(parents=True, exist_ok=True)
    times = ("2013-12-05_12:00:00", "2013-12-05_13:00:00")
    south_north = 3
    west_east = 4

    latitudes = np.linspace(52.0, 54.0, south_north, dtype="f4")[:, None]
    longitudes = np.linspace(7.0, 10.0, west_east, dtype="f4")[None, :]
    latitude_grid = np.broadcast_to(latitudes, (south_north, west_east))
    longitude_grid = np.broadcast_to(longitudes, (south_north, west_east))

    with netCDF4.Dataset(output, "w", format="NETCDF4_CLASSIC") as dataset:
        dataset.createDimension("Time", len(times))
        dataset.createDimension("DateStrLen", 19)
        dataset.createDimension("south_north", south_north)
        dataset.createDimension("west_east", west_east)

        dataset.TITLE = "SYNTHETIC WRF-LIKE COMPATIBILITY FIXTURE"
        dataset.START_DATE = times[0]
        dataset.SIMULATION_START_DATE = times[0]
        dataset.DX = 9000.0
        dataset.DY = 9000.0
        dataset.MAP_PROJ = 1
        dataset.MAP_PROJ_CHAR = "Lambert Conformal"
        dataset.CEN_LAT = 53.0
        dataset.CEN_LON = 8.5
        dataset.TRUELAT1 = 48.0
        dataset.TRUELAT2 = 58.0
        dataset.STAND_LON = 8.5

        times_variable = dataset.createVariable(
            "Times", "S1", ("Time", "DateStrLen")
        )
        # netCDF4.stringtochar has incompatible behaviour with some current
        # NumPy byte-scalar versions. A direct S1 matrix is stable across those
        # versions and exactly matches the WRF Times character layout.
        times_variable[:, :] = np.asarray(
            [list(timestamp) for timestamp in times], dtype="S1"
        )

        def variable(name: str, units: str):
            result = dataset.createVariable(
                name,
                "f4",
                ("Time", "south_north", "west_east"),
                zlib=True,
                complevel=1,
            )
            result.units = units
            result.coordinates = "XLONG XLAT"
            return result

        xlat = variable("XLAT", "degree_north")
        xlong = variable("XLONG", "degree_east")
        u10 = variable("U10", "m s-1")
        v10 = variable("V10", "m s-1")
        t2 = variable("T2", "K")
        psfc = variable("PSFC", "Pa")
        rainc = variable("RAINC", "mm")
        rainnc = variable("RAINNC", "mm")

        for index in range(len(times)):
            xlat[index] = latitude_grid
            xlong[index] = longitude_grid
            u10[index] = 5.0 + index + longitude_grid * 0.1
            v10[index] = 2.0 + index + latitude_grid * 0.05
            t2[index] = 273.15 + 4.0 + index + latitude_grid * 0.02
            psfc[index] = 100000.0 - latitude_grid * 12.0
            rainc[index] = index * 0.2 + longitude_grid * 0.01
            rainnc[index] = index * 0.4 + latitude_grid * 0.01

    print(f"Wrote synthetic compatibility fixture: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
