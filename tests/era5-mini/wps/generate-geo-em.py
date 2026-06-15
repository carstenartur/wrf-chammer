#!/usr/bin/env python3
"""Generate a synthetic geo_em.d01.nc fixture for the ERA5/WPS integration test.

The file is already committed as geo_em.d01.nc in this directory.
Re-run this script only if you need to regenerate it (e.g. after changing
the domain parameters in namelist.wps).

Requirements:
    pip install netCDF4 numpy

The generated file matches the domain in namelist.wps exactly:
  e_we=5, e_sn=5, map_proj=lat-lon, ref_lat=50.0, ref_lon=10.0, dx=0.5, dy=0.5

All global attributes required by WPS 4.6.0 metgrid.exe are included,
including WEST/SOUTH-NORTH_PATCH_START/END_STAG which were absent from the
previous WPS v3.9 fixture and caused the "Error while reading domain
time-independent attribute" error in metgrid.exe.

Note: Running geogrid.exe to produce a true fixture would require the WPS
GEOG static dataset (>1 GB). This synthetic file avoids that dependency while
providing all attributes metgrid.exe needs: correct projection (MAP_PROJ=6),
dimensions (5×5), cell-centre coordinates (XLAT_M/XLONG_M), and LANDMASK.
Static geophysical fields (terrain, vegetation, etc.) are set to plausible
placeholder values; they do not affect the ungrib→metgrid interpolation test.

IMPORTANT — Times variable:
  geo_em.d01.nc must contain Times = "0000-00-00_00:00:00" (WPS convention for
  time-independent static geogrid data).  Use np.frombuffer(b"...", dtype="S1")
  to write the character array correctly.  Do NOT use
  np.array(list(b"..."), dtype="S1"): in Python 3, list(bytes) yields integers,
  which numpy converts to their decimal string representations before truncating
  to one character — producing garbage like "4444..." instead of "0000...".
"""

from pathlib import Path

import netCDF4
import numpy as np

HERE = Path(__file__).parent.resolve()
OUT = HERE / "geo_em.d01.nc"

# --- Domain parameters (must match namelist.wps) ---
E_WE = 5        # WEST-EAST_GRID_DIMENSION   (e_we)
E_SN = 5        # SOUTH-NORTH_GRID_DIMENSION (e_sn)
DX = 0.5        # degrees (lat-lon projection)
DY = 0.5        # degrees
MAP_PROJ = 6    # 6 = cylindrical equidistant (lat-lon)
CEN_LAT = 50.0  # ref_lat
CEN_LON = 10.0  # ref_lon

# Derived sizes
WE_MASS = E_WE - 1   # unstaggered west_east
SN_MASS = E_SN - 1   # unstaggered south_north
WE_FULL = E_WE       # staggered west_east_stag
SN_FULL = E_SN       # staggered south_north_stag

# Cell-centre coordinates
lon0 = CEN_LON - (WE_MASS / 2.0) * DX + DX / 2.0
lat0 = CEN_LAT - (SN_MASS / 2.0) * DY + DY / 2.0
lons_m = np.array([lon0 + i * DX for i in range(WE_MASS)], dtype=np.float32)
lats_m = np.array([lat0 + j * DY for j in range(SN_MASS)], dtype=np.float32)

# Staggered (edge) coordinates
lons_s = np.array([lon0 - DX / 2.0 + i * DX for i in range(WE_FULL)], dtype=np.float32)
lats_s = np.array([lat0 - DY / 2.0 + j * DY for j in range(SN_FULL)], dtype=np.float32)

# 2-D coordinate arrays
XLAT_M = np.tile(lats_m[:, None], (1, WE_MASS)).astype(np.float32)[None]
XLONG_M = np.tile(lons_m[None, :], (SN_MASS, 1)).astype(np.float32)[None]
XLAT_U = np.tile(lats_m[:, None], (1, WE_FULL)).astype(np.float32)[None]
XLONG_U = np.tile(lons_s[None, :], (SN_MASS, 1)).astype(np.float32)[None]
XLAT_V = np.tile(lats_s[:, None], (1, WE_MASS)).astype(np.float32)[None]
XLONG_V = np.tile(lons_m[None, :], (SN_FULL, 1)).astype(np.float32)[None]
XLAT_C = np.tile(lats_s[:, None], (1, WE_FULL)).astype(np.float32)[None]
XLONG_C = np.tile(lons_s[None, :], (SN_FULL, 1)).astype(np.float32)[None]

# Domain corner coordinates (16 values: SW, SE, NE, NW for 4 staggerings)
SW_lat = float(lats_s[0])
NE_lat = float(lats_s[-1])
SW_lon = float(lons_s[0])
NE_lon = float(lons_s[-1])
corner_lats = np.array([SW_lat, SW_lat, NE_lat, NE_lat] * 4, dtype=np.float32)
corner_lons = np.array([SW_lon, NE_lon, NE_lon, SW_lon] * 4, dtype=np.float32)

# --- Build NetCDF file ---
ds = netCDF4.Dataset(str(OUT), "w", format="NETCDF4")

# Dimensions
ds.createDimension("Time", None)  # unlimited
ds.createDimension("south_north", SN_MASS)
ds.createDimension("west_east", WE_MASS)
ds.createDimension("south_north_stag", SN_FULL)
ds.createDimension("west_east_stag", WE_FULL)
ds.createDimension("month", 12)
ds.createDimension("land_cat", 21)
ds.createDimension("soil_cat", 16)
ds.createDimension("string19", 19)

# Global attributes — all entries that WPS 4.6.0 input_module.F reads
ds.TITLE = "OUTPUT FROM GEOGRID V4.6.0"
ds.setncattr("SIMULATION_START_DATE", "2024-01-15_00:00:00")
ds.setncattr("WEST-EAST_GRID_DIMENSION", np.int32(E_WE))
ds.setncattr("SOUTH-NORTH_GRID_DIMENSION", np.int32(E_SN))
ds.setncattr("BOTTOM-TOP_GRID_DIMENSION", np.int32(0))
ds.setncattr("WEST-EAST_PATCH_START_UNSTAG", np.int32(1))
ds.setncattr("WEST-EAST_PATCH_END_UNSTAG", np.int32(WE_MASS))
ds.setncattr("WEST-EAST_PATCH_START_STAG", np.int32(1))
ds.setncattr("WEST-EAST_PATCH_END_STAG", np.int32(WE_FULL))
ds.setncattr("SOUTH-NORTH_PATCH_START_UNSTAG", np.int32(1))
ds.setncattr("SOUTH-NORTH_PATCH_END_UNSTAG", np.int32(SN_MASS))
ds.setncattr("SOUTH-NORTH_PATCH_START_STAG", np.int32(1))
ds.setncattr("SOUTH-NORTH_PATCH_END_STAG", np.int32(SN_FULL))
ds.GRIDTYPE = "C"
ds.setncattr("DX", np.float32(DX))
ds.setncattr("DY", np.float32(DY))
ds.setncattr("DYN_OPT", np.int32(2))
ds.setncattr("CEN_LAT", np.float32(CEN_LAT))
ds.setncattr("CEN_LON", np.float32(CEN_LON))
ds.setncattr("TRUELAT1", np.float32(0.0))
ds.setncattr("TRUELAT2", np.float32(0.0))
ds.setncattr("MOAD_CEN_LAT", np.float32(CEN_LAT))
ds.setncattr("STAND_LON", np.float32(CEN_LON))
ds.setncattr("POLE_LAT", np.float32(90.0))
ds.setncattr("POLE_LON", np.float32(0.0))
ds.setncattr("corner_lats", corner_lats)
ds.setncattr("corner_lons", corner_lons)
ds.setncattr("MAP_PROJ", np.int32(MAP_PROJ))
ds.MMINLU = "MODIFIED_IGBP_MODIS_NOAH"
ds.setncattr("NUM_LAND_CAT", np.int32(21))
ds.setncattr("ISWATER", np.int32(17))
ds.setncattr("ISLAKE", np.int32(-1))
ds.setncattr("ISICE", np.int32(15))
ds.setncattr("ISURBAN", np.int32(13))
ds.setncattr("ISOILWATER", np.int32(14))
ds.setncattr("grid_id", np.int32(1))
ds.setncattr("parent_id", np.int32(1))
ds.setncattr("i_parent_start", np.int32(1))
ds.setncattr("j_parent_start", np.int32(1))
ds.setncattr("i_parent_end", np.int32(E_WE))
ds.setncattr("j_parent_end", np.int32(E_SN))
ds.setncattr("parent_grid_ratio", np.int32(1))
ds.setncattr("sr_x", np.int32(1))
ds.setncattr("sr_y", np.int32(1))


def _add_f32(name, dims, data, units, description, stagger):
    """Create a float32 variable with WRF I/O API metadata attributes."""
    v = ds.createVariable(name, "f4", dims, fill_value=np.float32(1e20))
    v.FieldType = np.int32(104)
    v.MemoryOrder = "XY "
    v.units = units
    v.description = description
    v.stagger = stagger
    v.sr_x = np.int32(1)
    v.sr_y = np.int32(1)
    v[...] = data


# Times variable (character array, one per time step).
# WPS convention: geo_em files use "0000-00-00_00:00:00" to mark
# time-independent (static) geogrid data.
# Use np.frombuffer so each byte is stored as its actual ASCII character.
# Do NOT use np.array(list(b"..."), dtype="S1"): in Python 3 list(bytes)
# yields integers, which are then converted to their decimal string
# representations and truncated to one character, producing garbage values.
tv = ds.createVariable("Times", "S1", ("Time", "string19"))
tv[0, :] = np.frombuffer(b"0000-00-00_00:00:00", dtype="S1")

# Lat/lon coordinate arrays
_add_f32("XLAT_M",  ("Time", "south_north", "west_east"),            XLAT_M,  "degrees latitude",  "Latitude on mass grid",        "M")
_add_f32("XLONG_M", ("Time", "south_north", "west_east"),            XLONG_M, "degrees longitude", "Longitude on mass grid",       "M")
_add_f32("XLAT_U",  ("Time", "south_north", "west_east_stag"),       XLAT_U,  "degrees latitude",  "Latitude on U grid",           "U")
_add_f32("XLONG_U", ("Time", "south_north", "west_east_stag"),       XLONG_U, "degrees longitude", "Longitude on U grid",          "U")
_add_f32("XLAT_V",  ("Time", "south_north_stag", "west_east"),       XLAT_V,  "degrees latitude",  "Latitude on V grid",           "V")
_add_f32("XLONG_V", ("Time", "south_north_stag", "west_east"),       XLONG_V, "degrees longitude", "Longitude on V grid",          "V")
_add_f32("XLAT_C",  ("Time", "south_north_stag", "west_east_stag"),  XLAT_C,  "degrees latitude",  "Latitude on cell corners",     "")
_add_f32("XLONG_C", ("Time", "south_north_stag", "west_east_stag"),  XLONG_C, "degrees longitude", "Longitude on cell corners",    "")

# Static fields (placeholder values; not used in the ungrib→metgrid test)
_add_f32("LANDMASK", ("Time", "south_north", "west_east"),
         np.ones((1, SN_MASS, WE_MASS), np.float32),
         "none", "Landmask : 1=land, 0=water", "M")
_add_f32("HGT_M", ("Time", "south_north", "west_east"),
         np.zeros((1, SN_MASS, WE_MASS), np.float32),
         "meters MSL", "Topography height", "M")
_add_f32("LU_INDEX", ("Time", "south_north", "west_east"),
         np.ones((1, SN_MASS, WE_MASS), np.float32),
         "category", "Dominant land use category", "M")

# Map scale factors (= 1 everywhere for cylindrical equidistant)
for _nm, _desc in [
    ("MAPFAC_M",  "Map scale factor on cross points"),
    ("MAPFAC_MX", "Map scale factor on cross points - x comp"),
    ("MAPFAC_MY", "Map scale factor on cross points - y comp"),
]:
    _add_f32(_nm, ("Time", "south_north", "west_east"),
             np.ones((1, SN_MASS, WE_MASS), np.float32), "m/m", _desc, "M")

ds.close()
print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
