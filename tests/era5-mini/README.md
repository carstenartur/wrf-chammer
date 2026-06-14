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

No internet access, no CDS credentials, and no large datasets are required.
