#!/usr/bin/env python3
"""Canonical ERA5 request planning for real WRF input data.

The planner produces the existing ``ci/download-era5.py`` configuration format.
It never creates weather values or placeholder GRIB content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PLANNER_VERSION = 1
ERA5_GRID_DEGREES = 0.25
DEFAULT_MARGIN_DEGREES = 1.0
MAX_PLAN_HOURS = 24 * 31

PRESSURE_LEVELS = [
    "1", "2", "3", "5", "7", "10", "20", "30", "50", "70", "100",
    "125", "150", "175", "200", "225", "250", "300", "350", "400",
    "450", "500", "550", "600", "650", "700", "750", "775", "800",
    "825", "850", "875", "900", "925", "950", "975", "1000",
]

PRESSURE_VARIABLES = [
    "geopotential",
    "relative_humidity",
    "temperature",
    "u_component_of_wind",
    "v_component_of_wind",
]

SINGLE_LEVEL_VARIABLES = [
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "2m_dewpoint_temperature",
    "2m_temperature",
    "land_sea_mask",
    "mean_sea_level_pressure",
    "sea_ice_cover",
    "sea_surface_temperature",
    "skin_temperature",
    "snow_depth",
    "soil_temperature_level_1",
    "soil_temperature_level_2",
    "soil_temperature_level_3",
    "soil_temperature_level_4",
    "surface_pressure",
    "volumetric_soil_water_layer_1",
    "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3",
    "volumetric_soil_water_layer_4",
]


class Era5PlanningError(ValueError):
    """Raised when a real-data request cannot be planned safely."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise Era5PlanningError([f"{field} must be a number"])
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise Era5PlanningError([f"{field} must be a number"]) from exc
    if not math.isfinite(result):
        raise Era5PlanningError([f"{field} must be finite"])
    return result


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise Era5PlanningError([f"{field} must be an ISO-8601 UTC timestamp"])
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise Era5PlanningError([f"{field} must be an ISO-8601 UTC timestamp"]) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed.minute or parsed.second or parsed.microsecond:
        raise Era5PlanningError([f"{field} must be aligned to a whole UTC hour"])
    return parsed


def _explicit_bounds(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        west = _number(value.get("west"), "bounds.west")
        south = _number(value.get("south"), "bounds.south")
        east = _number(value.get("east"), "bounds.east")
        north = _number(value.get("north"), "bounds.north")
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        west = _number(value[0], "bounds.west")
        south = _number(value[1], "bounds.south")
        east = _number(value[2], "bounds.east")
        north = _number(value[3], "bounds.north")
    else:
        raise Era5PlanningError(["bounds must be an object or [west, south, east, north]"])

    errors: list[str] = []
    if not -180 <= west < east <= 180:
        errors.append("longitude bounds must satisfy -180 <= west < east <= 180")
    if not -90 <= south < north <= 90:
        errors.append("latitude bounds must satisfy -90 <= south < north <= 90")
    if errors:
        raise Era5PlanningError(errors)
    return {"west": west, "south": south, "east": east, "north": north}


def _domain_bounds(domain: dict[str, Any]) -> dict[str, float]:
    center_lat = _number(domain.get("center_lat"), "domain.center_lat")
    center_lon = _number(domain.get("center_lon"), "domain.center_lon")
    dx_km = _number(domain.get("dx_km"), "domain.dx_km")
    dy_km = _number(domain.get("dy_km"), "domain.dy_km")
    e_we = int(_number(domain.get("e_we"), "domain.e_we"))
    e_sn = int(_number(domain.get("e_sn"), "domain.e_sn"))
    if e_we < 3 or e_sn < 3 or dx_km <= 0 or dy_km <= 0:
        raise Era5PlanningError(["domain grid dimensions and spacing must be positive"])

    width_km = (e_we - 1) * dx_km
    height_km = (e_sn - 1) * dy_km
    latitude_half_span = height_km / 2 / 111.195
    longitude_scale = max(0.05, math.cos(math.radians(center_lat)))
    longitude_half_span = width_km / 2 / (111.195 * longitude_scale)
    return _explicit_bounds({
        "west": center_lon - longitude_half_span,
        "south": center_lat - latitude_half_span,
        "east": center_lon + longitude_half_span,
        "north": center_lat + latitude_half_span,
    })


def bounds_from_job(job: dict[str, Any]) -> dict[str, float]:
    """Resolve exact map bounds when available, otherwise approximate the grid extent."""
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    if isinstance(metadata.get("domain_bounds"), (dict, list, tuple)):
        return _explicit_bounds(metadata["domain_bounds"])
    domain = job.get("domain")
    if not isinstance(domain, dict):
        raise Era5PlanningError(["job.domain must be an object"])
    return _domain_bounds(domain)


def _expanded_cds_area(bounds: dict[str, float], margin: float) -> list[float]:
    # CDS expects north, west, south, east.
    return [
        round(min(90.0, bounds["north"] + margin), 4),
        round(max(-180.0, bounds["west"] - margin), 4),
        round(max(-90.0, bounds["south"] - margin), 4),
        round(min(180.0, bounds["east"] + margin), 4),
    ]


def _hourly_timestamps(start: datetime, end: datetime, interval_hours: int) -> list[datetime]:
    if interval_hours < 1 or interval_hours > 24:
        raise Era5PlanningError(["interval_hours must be between 1 and 24"])
    if end <= start:
        raise Era5PlanningError(["period.end must be after period.start"])
    hours = (end - start).total_seconds() / 3600
    if hours > MAX_PLAN_HOURS:
        raise Era5PlanningError([f"ERA5 plan must not exceed {MAX_PLAN_HOURS} hours"])
    if hours % interval_hours:
        raise Era5PlanningError(["period length must be divisible by interval_hours"])

    timestamps: list[datetime] = []
    current = start
    while current <= end:
        timestamps.append(current)
        current += timedelta(hours=interval_hours)
    return timestamps


def _request_definition(
    *,
    dataset: str,
    variables: list[str],
    day: datetime,
    times: list[str],
    area: list[float],
    prefix: str,
    kind: str,
) -> tuple[str, dict[str, Any]]:
    request: dict[str, Any] = {
        "product_type": "reanalysis",
        "format": "grib",
        "variable": variables,
        "year": [day.strftime("%Y")],
        "month": [day.strftime("%m")],
        "day": [day.strftime("%d")],
        "time": times,
        "area": area,
    }
    if kind == "pressure_levels":
        request["pressure_level"] = PRESSURE_LEVELS

    identity = {"dataset": dataset, "request": request, "planner_version": PLANNER_VERSION}
    request_key = _sha256(identity)
    date_text = day.strftime("%Y%m%d")
    target = f"files/{kind}-{date_text}-{request_key[:12]}.grib"
    name = f"{kind}_{date_text}"
    return name, {
        "dataset": dataset,
        "target": target,
        "ungrib_prefix": prefix,
        "request": request,
        "request_key": request_key,
    }


def _estimate_request_bytes(request_def: dict[str, Any]) -> int:
    request = request_def["request"]
    north, west, south, east = request["area"]
    latitude_points = max(1, math.ceil((north - south) / ERA5_GRID_DEGREES) + 1)
    longitude_points = max(1, math.ceil((east - west) / ERA5_GRID_DEGREES) + 1)
    times = len(request["time"])
    variables = len(request["variable"])
    levels = len(request.get("pressure_level", ["surface"]))
    raw_bytes = latitude_points * longitude_points * times * variables * levels * 4
    # GRIB packing varies by field; this is intentionally a broad planning estimate.
    return max(1_000_000, int(raw_bytes * 0.45))


def build_era5_plan(
    *,
    period: dict[str, Any],
    bounds: dict[str, Any] | list[Any],
    cache_root: Path | None = None,
    interval_hours: int = 1,
    margin_degrees: float = DEFAULT_MARGIN_DEGREES,
) -> dict[str, Any]:
    """Build a canonical, downloader-compatible ERA5 request plan."""
    if not isinstance(period, dict):
        raise Era5PlanningError(["period must be an object"])
    start = _timestamp(period.get("start"), "period.start")
    end = _timestamp(period.get("end"), "period.end")
    resolved_bounds = _explicit_bounds(bounds)
    margin = _number(margin_degrees, "margin_degrees")
    if margin < 0 or margin > 10:
        raise Era5PlanningError(["margin_degrees must be between 0 and 10"])

    timestamps = _hourly_timestamps(start, end, interval_hours)
    grouped: dict[str, list[datetime]] = defaultdict(list)
    for timestamp in timestamps:
        grouped[timestamp.strftime("%Y-%m-%d")].append(timestamp)

    area = _expanded_cds_area(resolved_bounds, margin)
    requests: dict[str, dict[str, Any]] = {}
    for day_text in sorted(grouped):
        day = grouped[day_text][0]
        times = [timestamp.strftime("%H:%M") for timestamp in grouped[day_text]]
        pressure_name, pressure = _request_definition(
            dataset="reanalysis-era5-pressure-levels",
            variables=PRESSURE_VARIABLES,
            day=day,
            times=times,
            area=area,
            prefix="PLEV",
            kind="pressure_levels",
        )
        single_name, single = _request_definition(
            dataset="reanalysis-era5-single-levels",
            variables=SINGLE_LEVEL_VARIABLES,
            day=day,
            times=times,
            area=area,
            prefix="SFC",
            kind="single_levels",
        )
        requests[pressure_name] = pressure
        requests[single_name] = single

    plan_identity = {
        "planner_version": PLANNER_VERSION,
        "request_keys": [requests[name]["request_key"] for name in sorted(requests)],
    }
    plan_key = _sha256(plan_identity)
    estimated_bytes = sum(_estimate_request_bytes(request) for request in requests.values())

    cache_entries: list[dict[str, Any]] = []
    hits = 0
    partials = 0
    root = cache_root.resolve() if cache_root is not None else None
    plan_dir = root / plan_key if root is not None else None
    for name in sorted(requests):
        definition = requests[name]
        target_path = plan_dir / definition["target"] if plan_dir is not None else None
        present = bool(target_path and target_path.is_file() and target_path.stat().st_size > 0)
        partial_path = Path(f"{target_path}.part") if target_path is not None else None
        partial = bool(partial_path and partial_path.exists())
        hits += int(present)
        partials += int(partial and not present)
        cache_entries.append({
            "name": name,
            "request_key": definition["request_key"],
            "target": definition["target"],
            "present": present,
            "partial": partial,
            "size_bytes": target_path.stat().st_size if present and target_path is not None else 0,
        })

    if hits == len(requests):
        cache_status = "complete"
    elif hits or partials:
        cache_status = "partial"
    else:
        cache_status = "missing"

    downloader_requests = {
        name: {key: value for key, value in definition.items() if key != "request_key"}
        for name, definition in requests.items()
    }
    return {
        "ok": True,
        "planner_version": PLANNER_VERSION,
        "plan_key": plan_key,
        "period": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "interval_hours": interval_hours,
            "time_points": len(timestamps),
        },
        "domain": {
            "bounds": resolved_bounds,
            "cds_area": area,
            "margin_degrees": margin,
        },
        "download_config": {"requests": downloader_requests},
        "requests": [
            {
                "name": name,
                "dataset": requests[name]["dataset"],
                "target": requests[name]["target"],
                "ungrib_prefix": requests[name]["ungrib_prefix"],
                "request_key": requests[name]["request_key"],
                "estimated_size_bytes": _estimate_request_bytes(requests[name]),
            }
            for name in sorted(requests)
        ],
        "estimated_download": {
            "bytes": estimated_bytes,
            "gigabytes": math.ceil(estimated_bytes / 100_000_000) / 10,
            "note": "Planning estimate based on ERA5 grid points, fields and GRIB packing; not a quota guarantee.",
        },
        "cache": {
            "root": str(root) if root is not None else None,
            "plan_directory": str(plan_dir) if plan_dir is not None else None,
            "status": cache_status,
            "hits": hits,
            "partial_entries": partials,
            "total": len(requests),
            "coverage_percent": round(hits * 100 / len(requests), 1),
            "entries": cache_entries,
        },
        "provenance": {
            "source": "Copernicus Climate Data Store ERA5 reanalysis",
            "datasets": ["reanalysis-era5-pressure-levels", "reanalysis-era5-single-levels"],
            "request_identity": "SHA-256 of canonical dataset/request JSON",
            "artificial_weather_data": False,
        },
    }


def build_era5_plan_from_job(
    job: dict[str, Any],
    *,
    cache_root: Path | None = None,
    interval_hours: int = 1,
    margin_degrees: float = DEFAULT_MARGIN_DEGREES,
) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise Era5PlanningError(["job must be a JSON object"])
    period = job.get("period")
    if not isinstance(period, dict):
        raise Era5PlanningError(["job.period must be an object"])
    return build_era5_plan(
        period=period,
        bounds=bounds_from_job(job),
        cache_root=cache_root,
        interval_hours=interval_hours,
        margin_degrees=margin_degrees,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan real ERA5 requests for a Workbench job")
    parser.add_argument("--job", required=True, type=Path, help="Workbench job JSON")
    parser.add_argument("--output", type=Path, help="Write the complete plan to this JSON file")
    parser.add_argument("--download-config", type=Path, help="Write downloader-compatible requests JSON")
    parser.add_argument("--cache-root", type=Path, default=Path(".era5-cache"))
    parser.add_argument("--interval-hours", type=int, default=1)
    parser.add_argument("--margin-degrees", type=float, default=DEFAULT_MARGIN_DEGREES)
    args = parser.parse_args(argv)

    job = json.loads(args.job.read_text(encoding="utf-8"))
    plan = build_era5_plan_from_job(
        job,
        cache_root=args.cache_root,
        interval_hours=args.interval_hours,
        margin_degrees=args.margin_degrees,
    )
    rendered = json.dumps(plan, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    if args.download_config:
        args.download_config.parent.mkdir(parents=True, exist_ok=True)
        args.download_config.write_text(json.dumps(plan["download_config"], indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
