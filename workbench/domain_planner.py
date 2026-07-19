#!/usr/bin/env python3
"""Server-side WRF domain planning and transparent resource estimates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

EARTH_RADIUS_KM = 6371.0088
MAX_HORIZONTAL_GRID_POINTS = 400_000
MAX_SIMULATION_HOURS = 240

QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "quick-preview": {
        "label": "Quick preview",
        "grid_spacing_km": 27.0,
        "vertical_levels": 30,
        "output_interval_minutes": 180,
        "output_variables": 24,
        "description": "Coarse regional overview with low compute and storage requirements.",
    },
    "balanced": {
        "label": "Balanced regional",
        "grid_spacing_km": 9.0,
        "vertical_levels": 35,
        "output_interval_minutes": 60,
        "output_variables": 40,
        "description": "Regional weather structures with moderate compute requirements.",
    },
    "detailed": {
        "label": "Detailed regional",
        "grid_spacing_km": 3.0,
        "vertical_levels": 45,
        "output_interval_minutes": 30,
        "output_variables": 50,
        "description": "Fine regional structure; intended for small domains and capable machines.",
    },
}


class DomainPlanningError(ValueError):
    """Raised when a domain request cannot be planned safely."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Bounds:
    west: float
    south: float
    east: float
    north: float

    @property
    def center_lat(self) -> float:
        return (self.south + self.north) / 2.0

    @property
    def center_lon(self) -> float:
        return (self.west + self.east) / 2.0

    def as_dict(self) -> dict[str, float]:
        return {
            "west": round(self.west, 6),
            "south": round(self.south, 6),
            "east": round(self.east, 6),
            "north": round(self.north, 6),
        }


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise DomainPlanningError([f"{field} must be a number"])
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DomainPlanningError([f"{field} must be a number"]) from exc
    if not math.isfinite(result):
        raise DomainPlanningError([f"{field} must be finite"])
    return result


def _parse_bounds(value: Any) -> Bounds:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        west, south, east, north = value
    elif isinstance(value, dict):
        west = value.get("west")
        south = value.get("south")
        east = value.get("east")
        north = value.get("north")
    else:
        raise DomainPlanningError(["bounds must be [west, south, east, north] or an object"])

    bounds = Bounds(
        west=_number(west, "bounds.west"),
        south=_number(south, "bounds.south"),
        east=_number(east, "bounds.east"),
        north=_number(north, "bounds.north"),
    )
    errors: list[str] = []
    if not -180 <= bounds.west <= 180 or not -180 <= bounds.east <= 180:
        errors.append("longitude bounds must be between -180 and 180 degrees")
    if not -90 <= bounds.south <= 90 or not -90 <= bounds.north <= 90:
        errors.append("latitude bounds must be between -90 and 90 degrees")
    if bounds.west >= bounds.east:
        errors.append("bounds.west must be smaller than bounds.east; dateline-crossing domains are not supported yet")
    if bounds.south >= bounds.north:
        errors.append("bounds.south must be smaller than bounds.north")
    if bounds.east - bounds.west < 0.1 or bounds.north - bounds.south < 0.1:
        errors.append("domain must span at least 0.1 degrees in both directions")
    if errors:
        raise DomainPlanningError(errors)
    return bounds


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DomainPlanningError([f"{field} must be an ISO-8601 timestamp"])
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DomainPlanningError([f"{field} must be an ISO-8601 timestamp"] ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _rounded_grid_points(distance_km: float, spacing_km: float) -> int:
    # WRF domains are easier to decompose when e_* - 1 is divisible by six.
    cells = max(12, math.ceil(distance_km / spacing_km))
    aligned_cells = math.ceil(cells / 6) * 6
    return aligned_cells + 1


def _distance_km(bounds: Bounds) -> tuple[float, float]:
    mean_lat_radians = math.radians(bounds.center_lat)
    width = EARTH_RADIUS_KM * math.cos(mean_lat_radians) * math.radians(bounds.east - bounds.west)
    height = EARTH_RADIUS_KM * math.radians(bounds.north - bounds.south)
    return abs(width), abs(height)


def _round_up(value: float, digits: int = 1) -> float:
    factor = 10**digits
    return math.ceil(value * factor) / factor


def _resource_estimates(
    *,
    horizontal_points: int,
    vertical_levels: int,
    simulation_hours: float,
    time_step_seconds: int,
    output_interval_minutes: int,
    output_variables: int,
    width_km: float,
    height_km: float,
) -> dict[str, Any]:
    integration_steps = max(1, math.ceil(simulation_hours * 3600 / time_step_seconds))
    output_frames = max(2, math.floor(simulation_hours * 60 / output_interval_minutes) + 1)
    three_d_points = horizontal_points * vertical_levels

    # These estimates are deliberately conservative and are labelled as estimates.
    working_bytes = three_d_points * 96 * 8 * 3.2
    minimum_ram_gb = max(1.5, working_bytes / 1_000_000_000)
    recommended_ram_gb = max(3.0, minimum_ram_gb * 1.8)

    raw_output_bytes = three_d_points * output_variables * 4 * output_frames
    wrf_output_gb = max(0.1, raw_output_bytes * 0.65 / 1_000_000_000)

    domain_area_million_km2 = width_km * height_km / 1_000_000
    era5_input_gb = max(0.15, domain_area_million_km2 * simulation_hours * 0.55)
    work_units = three_d_points * integration_steps
    baseline_minutes = max(2.0, work_units / 25_000_000)
    low_minutes = max(2, math.floor(baseline_minutes * 0.7))
    high_minutes = max(low_minutes + 1, math.ceil(baseline_minutes * 2.1))

    if high_minutes <= 15:
        runtime_class = "minutes"
    elif high_minutes <= 60:
        runtime_class = "under-an-hour"
    elif high_minutes <= 240:
        runtime_class = "hours"
    else:
        runtime_class = "long-running"

    return {
        "horizontal_grid_points": horizontal_points,
        "three_dimensional_grid_points": three_d_points,
        "integration_steps": integration_steps,
        "output_frames": output_frames,
        "estimated_ram_gb": {
            "minimum": _round_up(minimum_ram_gb),
            "recommended": _round_up(recommended_ram_gb),
        },
        "estimated_storage_gb": {
            "era5_input": _round_up(era5_input_gb),
            "wrf_output": _round_up(wrf_output_gb),
            "working_total": _round_up(era5_input_gb + wrf_output_gb * 1.8),
        },
        "estimated_wall_clock_minutes": {
            "lower": low_minutes,
            "upper": high_minutes,
            "runtime_class": runtime_class,
            "reference": "rough estimate for an 8-core contemporary CPU; calibrate with measured runs",
        },
    }


def plan_domain(request: dict[str, Any]) -> dict[str, Any]:
    """Validate a user-facing request and derive a WRF-compatible domain plan."""
    if not isinstance(request, dict):
        raise DomainPlanningError(["request must be a JSON object"])

    bounds = _parse_bounds(request.get("bounds"))
    profile_id = str(request.get("quality_profile") or "balanced")
    if profile_id not in QUALITY_PROFILES:
        raise DomainPlanningError([f"unknown quality_profile: {profile_id!r}"])
    profile = dict(QUALITY_PROFILES[profile_id])

    expert = request.get("expert") if isinstance(request.get("expert"), dict) else {}
    spacing = _number(expert.get("grid_spacing_km", profile["grid_spacing_km"]), "grid_spacing_km")
    vertical_levels = int(_number(expert.get("vertical_levels", profile["vertical_levels"]), "vertical_levels"))
    output_interval = int(_number(expert.get("output_interval_minutes", profile["output_interval_minutes"]), "output_interval_minutes"))

    errors: list[str] = []
    warnings: list[str] = []
    if not 1.0 <= spacing <= 100.0:
        errors.append("grid spacing must be between 1 and 100 km")
    if not 20 <= vertical_levels <= 100:
        errors.append("vertical_levels must be between 20 and 100")
    if not 10 <= output_interval <= 360:
        errors.append("output_interval_minutes must be between 10 and 360")

    period = request.get("period")
    if not isinstance(period, dict):
        errors.append("period must contain start and end timestamps")
        start = end = datetime.now(timezone.utc)
    else:
        try:
            start = _parse_datetime(period.get("start"), "period.start")
            end = _parse_datetime(period.get("end"), "period.end")
        except DomainPlanningError as exc:
            errors.extend(exc.errors)
            start = end = datetime.now(timezone.utc)

    simulation_hours = (end - start).total_seconds() / 3600
    if simulation_hours <= 0:
        errors.append("period.end must be after period.start")
    elif simulation_hours > MAX_SIMULATION_HOURS:
        errors.append(f"simulation period must not exceed {MAX_SIMULATION_HOURS} hours")
    elif simulation_hours > 72:
        warnings.append("Long simulation periods can require substantial download, storage and compute time.")

    if errors:
        raise DomainPlanningError(errors)

    width_km, height_km = _distance_km(bounds)
    e_we = _rounded_grid_points(width_km, spacing)
    e_sn = _rounded_grid_points(height_km, spacing)
    horizontal_points = e_we * e_sn
    if horizontal_points > MAX_HORIZONTAL_GRID_POINTS:
        raise DomainPlanningError([
            f"planned domain has {horizontal_points:,} horizontal grid points; reduce the area or use a coarser profile"
        ])
    if horizontal_points > 100_000:
        warnings.append("This is a large local domain; verify available RAM and disk space before starting.")
    if spacing <= 3 and max(width_km, height_km) > 600:
        warnings.append("A 3 km grid over this extent is expensive; consider a smaller domain or nesting later.")

    time_step_seconds = max(6, min(360, int(math.floor(spacing * 6))))
    resources = _resource_estimates(
        horizontal_points=horizontal_points,
        vertical_levels=vertical_levels,
        simulation_hours=simulation_hours,
        time_step_seconds=time_step_seconds,
        output_interval_minutes=output_interval,
        output_variables=int(profile["output_variables"]),
        width_km=width_km,
        height_km=height_km,
    )

    return {
        "ok": True,
        "valid": True,
        "warnings": warnings,
        "quality_profile": {
            "id": profile_id,
            "label": profile["label"],
            "description": profile["description"],
        },
        "period": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "simulation_hours": round(simulation_hours, 2),
            "output_interval_minutes": output_interval,
        },
        "domain": {
            "label": str(request.get("label") or "custom-map-domain"),
            "bounds": bounds.as_dict(),
            "center_lat": round(bounds.center_lat, 6),
            "center_lon": round(bounds.center_lon, 6),
            "width_km": round(width_km, 1),
            "height_km": round(height_km, 1),
            "dx_km": spacing,
            "dy_km": spacing,
            "e_we": e_we,
            "e_sn": e_sn,
            "vertical_levels": vertical_levels,
            "time_step_seconds": time_step_seconds,
            "projection_recommendation": "lambert-conformal" if abs(bounds.center_lat) >= 25 else "mercator",
        },
        "resources": resources,
        "assumptions": [
            "Grid dimensions are aligned so e_we - 1 and e_sn - 1 are divisible by six.",
            "Resource values are planning estimates, not guarantees.",
            "Scientific suitability still depends on physics, boundary conditions and validation.",
        ],
    }


def available_profiles() -> list[dict[str, Any]]:
    return [
        {
            "id": profile_id,
            "label": profile["label"],
            "grid_spacing_km": profile["grid_spacing_km"],
            "description": profile["description"],
        }
        for profile_id, profile in QUALITY_PROFILES.items()
    ]
