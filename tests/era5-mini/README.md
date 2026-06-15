# ERA5 Mini Test Dataset

Minimal ECMWF GRIB1 files used by the ERA5/WPS integration test
(`ci/test-era5-wps-integration.sh`).

## Contents

| File | Description |
|---|---|
| `pressure.grib` | 10 GRIB1 messages at 500 and 850 hPa (z, t, u, v, q) |
| `surface.grib` | 6 GRIB1 messages for near-surface fields (sp, u10, v10, t2, d2, skt) |
| `generate-mini-grib.py` | Python script that produced these GRIB files |
| `wps/namelist.wps` | Minimal WPS namelist matching this dataset |
| `wps/geo_em.d01.nc` | Synthetic WPS geogrid output fixture consumed by `metgrid.exe` |
| `wps/generate-geo-em.py` | Python script that produced `geo_em.d01.nc` |
| `wps/expected.json` | Expected outputs from the integration test |

## Grid

| Property | Value |
|---|---|
| Projection | Regular lat/lon |
| Grid size | 3 × 3 |
| Spacing | 2° |
| Latitude range | 48 N – 52 N |
| Longitude range | 8 E – 12 E |
| Time | 2024-01-15 00:00 UTC (single step) |

## WPS domain (`wps/geo_em.d01.nc`)

| Property | Value |
|---|---|
| Domain | 5×5 staggered (4×4 mass-point cells) |
| Projection | Cylindrical equidistant (lat-lon, MAP_PROJ=6) |
| Spacing | 0.5° in lat and lon |
| Centre | 50 N, 10 E |
| Mass-point extent | ~49.25–50.75 N, 9.25–10.75 E |

`geo_em.d01.nc` is a **synthetic** minimal fixture — it is **not** produced by
running the full `geogrid.exe` with a WPS_GEOG static dataset (which would
require > 1 GB of data).  The file contains all WPS 4.6.0 metadata attributes
and placeholder values for static fields (LANDMASK=1, HGT_M=0, LU_INDEX=1).
This is sufficient to let `metgrid.exe` perform the GRIB → intermediate →
`met_em` interpolation; it does **not** represent a meteorologically valid
domain.

The `Times` variable is deliberately `0000-00-00_00:00:00`, which is the WPS
convention for time-independent (static) geogrid data.

To regenerate `geo_em.d01.nc` after changing domain parameters:

```bash
pip install netCDF4 numpy
python3 tests/era5-mini/wps/generate-geo-em.py
```

## Regenerating the GRIB files

If you need to regenerate these files (e.g. after changing the domain):

```bash
pip install eccodes numpy
python3 tests/era5-mini/generate-mini-grib.py
```

## What the integration test proves

Running `ungrib.exe` and `metgrid.exe` against these files proves that:

1. WPS can decode ECMWF GRIB1 data using the `Vtable.ERA-interim.pl` table.
2. `ungrib.exe` produces WPS intermediate files (`PLEV:*` and `SFC:*`).
3. `metgrid.exe` produces valid `met_em.d01.*` NetCDF files.
4. The core meteorological fields (`TT`, `UU`, `VV`, `GHT`, `PSFC`) are present
   and readable with `ncdump -h`.

The test **does not** validate numerically correct meteorological values; it
only ensures the GRIB → ungrib → metgrid → `met_em` pipeline executes without
error.  No internet access, no CDS credentials, and no large datasets are
required.
