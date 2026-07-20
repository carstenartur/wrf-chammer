#!/usr/bin/env python3
"""Deterministic immutable run specifications for real WPS/WRF pipelines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

_SPEC_VERSION = 1
_RUNTIME_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")

PIPELINE_PROFILES: dict[str, dict[str, Any]] = {
    "small-real-data-demo": {
        "label": "Small real-data demonstration",
        "max_grid_points": 45_000,
        "e_vert": 35,
        "history_interval_minutes": 60,
        "boundary_interval_seconds": 3600,
        "time_step_factor": 6.0,
        "physics": {
            "mp_physics": 3,
            "ra_lw_physics": 1,
            "ra_sw_physics": 1,
            "bl_pbl_physics": 1,
            "cu_physics": 1,
        },
        "postprocessing_profile": "standard-surface-fields-v1",
    },
    "quick-preview": {
        "label": "Quick preview",
        "max_grid_points": 120_000,
        "e_vert": 35,
        "history_interval_minutes": 60,
        "boundary_interval_seconds": 3600,
        "time_step_factor": 6.0,
        "physics": {
            "mp_physics": 3,
            "ra_lw_physics": 1,
            "ra_sw_physics": 1,
            "bl_pbl_physics": 1,
            "cu_physics": 1,
        },
        "postprocessing_profile": "standard-surface-fields-v1",
    },
    "balanced-regional": {
        "label": "Balanced regional",
        "max_grid_points": 300_000,
        "e_vert": 45,
        "history_interval_minutes": 60,
        "boundary_interval_seconds": 3600,
        "time_step_factor": 5.0,
        "physics": {
            "mp_physics": 6,
            "ra_lw_physics": 4,
            "ra_sw_physics": 4,
            "bl_pbl_physics": 1,
            "cu_physics": 1,
        },
        "postprocessing_profile": "standard-surface-fields-v1",
    },
}

ERROR_CATEGORIES = [
    "INPUT_DATA_MISSING",
    "DOMAIN_CONFIGURATION_INVALID",
    "WPS_GEOGRAPHY_MISSING",
    "NAMELIST_INVALID",
    "INSUFFICIENT_MEMORY",
    "DISK_FULL",
    "WRF_NUMERICAL_INSTABILITY",
    "RUNTIME_IMAGE_MISMATCH",
    "PROCESS_CRASH",
]


class PipelineSpecificationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PipelineSpecificationError([f"{field} must be an ISO-8601 UTC timestamp"])
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PipelineSpecificationError([f"{field} must be an ISO-8601 UTC timestamp"]) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PipelineSpecificationError([f"{field} must use the UTC offset Z or +00:00"])
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise PipelineSpecificationError([f"{field} must be a finite number"])
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PipelineSpecificationError([f"{field} must be a finite number"]) from exc
    if not math.isfinite(number):
        raise PipelineSpecificationError([f"{field} must be a finite number"])
    return number


def _positive_number(value: Any, field: str) -> float:
    number = _finite_number(value, field)
    if number <= 0:
        raise PipelineSpecificationError([f"{field} must be a positive number"])
    return number


def _positive_int(value: Any, field: str) -> int:
    number = _positive_number(value, field)
    if not number.is_integer():
        raise PipelineSpecificationError([f"{field} must be an integer"])
    return int(number)


def _format_wrf_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d_%H:%M:%S")


def _runtime_seconds(start: datetime, end: datetime) -> tuple[int, int, int, int]:
    seconds = int((end - start).total_seconds())
    if seconds <= 0:
        raise PipelineSpecificationError(["period.end must be after period.start"])
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return days, hours, minutes, seconds


def generate_namelists(job: dict[str, Any], profile_id: str) -> dict[str, str]:
    profile = PIPELINE_PROFILES.get(profile_id)
    if profile is None:
        raise PipelineSpecificationError([f"unknown pipeline profile: {profile_id}"])
    period = job.get("period")
    domain = job.get("domain")
    if not isinstance(period, dict) or not isinstance(domain, dict):
        raise PipelineSpecificationError(["job.period and job.domain must be objects"])

    start = parse_utc_timestamp(period.get("start"), "period.start")
    end = parse_utc_timestamp(period.get("end"), "period.end")
    run_days, run_hours, run_minutes, run_seconds = _runtime_seconds(start, end)
    dx_km = _positive_number(domain.get("dx_km"), "domain.dx_km")
    dy_km = _positive_number(domain.get("dy_km"), "domain.dy_km")
    e_we = _positive_int(domain.get("e_we"), "domain.e_we")
    e_sn = _positive_int(domain.get("e_sn"), "domain.e_sn")
    if e_we < 3 or e_sn < 3:
        raise PipelineSpecificationError(["domain.e_we and domain.e_sn must be at least 3"])
    if e_we * e_sn > int(profile["max_grid_points"]):
        raise PipelineSpecificationError([
            f"profile {profile_id} allows at most {profile['max_grid_points']} horizontal grid points"
        ])
    center_lat = _finite_number(domain.get("center_lat"), "domain.center_lat")
    center_lon = _finite_number(domain.get("center_lon"), "domain.center_lon")
    if not (-90 <= center_lat <= 90 and -180 <= center_lon <= 180):
        raise PipelineSpecificationError(["domain center coordinates are invalid"])

    dx_m = int(round(dx_km * 1000))
    dy_m = int(round(dy_km * 1000))
    truelat1 = max(min(center_lat - 5.0, 89.0), -89.0)
    truelat2 = max(min(center_lat + 5.0, 89.0), -89.0)
    boundary_interval = int(profile["boundary_interval_seconds"])
    time_step = max(1, int(round(min(dx_km, dy_km) * float(profile["time_step_factor"]))))
    physics = profile["physics"]

    namelist_wps = f"""&share
 wrf_core = 'ARW',
 max_dom = 1,
 start_date = '{_format_wrf_time(start)}',
 end_date   = '{_format_wrf_time(end)}',
 interval_seconds = {boundary_interval},
 io_form_geogrid = 2,
/

&geogrid
 parent_id         = 1,
 parent_grid_ratio = 1,
 i_parent_start    = 1,
 j_parent_start    = 1,
 e_we              = {e_we},
 e_sn              = {e_sn},
 geog_data_res     = 'default',
 dx = {dx_m},
 dy = {dy_m},
 map_proj = 'lambert',
 ref_lat   = {center_lat:.4f},
 ref_lon   = {center_lon:.4f},
 truelat1  = {truelat1:.4f},
 truelat2  = {truelat2:.4f},
 stand_lon = {center_lon:.4f},
 geog_data_path = 'geog',
/

&ungrib
 out_format = 'WPS',
 prefix = 'FILE',
/

&metgrid
 fg_name = 'FILE',
 io_form_metgrid = 2,
/
"""

    namelist_input = f"""&time_control
 run_days                            = {run_days},
 run_hours                           = {run_hours},
 run_minutes                         = {run_minutes},
 run_seconds                         = {run_seconds},
 start_year                          = {start.year},
 start_month                         = {start.month},
 start_day                           = {start.day},
 start_hour                          = {start.hour},
 start_minute                        = {start.minute},
 start_second                        = {start.second},
 end_year                            = {end.year},
 end_month                           = {end.month},
 end_day                             = {end.day},
 end_hour                            = {end.hour},
 end_minute                          = {end.minute},
 end_second                          = {end.second},
 interval_seconds                    = {boundary_interval},
 input_from_file                     = .true.,
 history_interval                    = {profile['history_interval_minutes']},
 frames_per_outfile                  = 1,
 restart                             = .false.,
 io_form_history                     = 2,
 io_form_input                       = 2,
 io_form_boundary                    = 2,
/

&domains
 time_step                           = {time_step},
 max_dom                             = 1,
 e_we                                = {e_we},
 e_sn                                = {e_sn},
 e_vert                              = {profile['e_vert']},
 dx                                  = {dx_m},
 dy                                  = {dy_m},
 grid_id                             = 1,
 parent_id                           = 0,
 i_parent_start                      = 1,
 j_parent_start                      = 1,
 parent_grid_ratio                   = 1,
 parent_time_step_ratio              = 1,
 feedback                            = 0,
 smooth_option                       = 0,
/

&physics
 mp_physics                          = {physics['mp_physics']},
 ra_lw_physics                       = {physics['ra_lw_physics']},
 ra_sw_physics                       = {physics['ra_sw_physics']},
 bl_pbl_physics                      = {physics['bl_pbl_physics']},
 cu_physics                          = {physics['cu_physics']},
/

&dynamics
 hybrid_opt                          = 2,
 w_damping                           = 0,
 diff_opt                            = 1,
 km_opt                              = 4,
/

&bdy_control
 spec_bdy_width                      = 5,
 specified                           = .true.,
/

&namelist_quilt
 nio_tasks_per_group                 = 0,
 nio_groups                          = 1,
/
"""
    return {"namelist.wps": namelist_wps, "namelist.input": namelist_input}


def validate_runtime_identities(runtime: dict[str, Any]) -> dict[str, dict[str, str]]:
    required = ("wps", "wrf", "postprocessing")
    normalized: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for name in required:
        value = runtime.get(name)
        if not isinstance(value, dict):
            errors.append(f"runtime.{name} must be an object")
            continue
        reference = value.get("reference")
        identity = value.get("identity")
        if not isinstance(reference, str) or not reference.strip():
            errors.append(f"runtime.{name}.reference must be non-empty")
        if not isinstance(identity, str) or not _RUNTIME_ID_RE.fullmatch(identity):
            errors.append(f"runtime.{name}.identity must be sha256:<64 lowercase hex characters>")
        if isinstance(reference, str) and isinstance(identity, str):
            normalized[name] = {"reference": reference.strip(), "identity": identity}
    if errors:
        raise PipelineSpecificationError(errors)
    return normalized


def build_run_specification_identity(
    *,
    job: dict[str, Any],
    era5_plan: dict[str, Any],
    checksums: dict[str, Any],
    provenance: dict[str, Any],
    runtime: dict[str, Any],
    source_revision: str,
    profile_id: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    if not isinstance(job, dict) or not isinstance(era5_plan, dict):
        raise PipelineSpecificationError(["job and ERA5 plan must be objects"])
    plan_key = era5_plan.get("plan_key")
    if not isinstance(plan_key, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_key):
        raise PipelineSpecificationError(["ERA5 plan key is invalid"])
    cache = era5_plan.get("cache")
    if not isinstance(cache, dict) or cache.get("status") != "complete":
        raise PipelineSpecificationError(["ERA5 cache must be complete before freezing a real run"])
    if job.get("period") != era5_plan.get("period"):
        job_period = job.get("period") if isinstance(job.get("period"), dict) else {}
        plan_period = era5_plan.get("period") if isinstance(era5_plan.get("period"), dict) else {}
        if job_period.get("start") != plan_period.get("start") or job_period.get("end") != plan_period.get("end"):
            raise PipelineSpecificationError(["job period does not match the ERA5 plan period"])
    files = checksums.get("files") if isinstance(checksums, dict) else None
    if not isinstance(files, dict) or not files:
        raise PipelineSpecificationError(["verified ERA5 checksums are required"])
    input_files: list[dict[str, Any]] = []
    for relative_path in sorted(files):
        metadata = files[relative_path]
        if not isinstance(metadata, dict):
            raise PipelineSpecificationError(["ERA5 checksum metadata is invalid"])
        digest = metadata.get("sha256")
        size = metadata.get("size_bytes")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PipelineSpecificationError([f"invalid checksum for {relative_path}"])
        if not isinstance(size, int) or size <= 0:
            raise PipelineSpecificationError([f"invalid size for {relative_path}"])
        input_files.append({
            "path": relative_path,
            "sha256": digest,
            "size_bytes": size,
            "request_name": metadata.get("request_name"),
        })
    if provenance.get("plan_key") not in (None, plan_key):
        raise PipelineSpecificationError(["ERA5 provenance plan key does not match"])
    if provenance.get("artificial_weather_data") is not False:
        raise PipelineSpecificationError(["real pipeline input must not be artificial weather data"])
    runtimes = validate_runtime_identities(runtime)
    if not isinstance(source_revision, str) or not _REVISION_RE.fullmatch(source_revision):
        raise PipelineSpecificationError(["source revision must be a 40-64 character lowercase Git SHA"])

    namelists = generate_namelists(job, profile_id)
    profile = PIPELINE_PROFILES[profile_id]
    namelist_metadata = {
        name: {"sha256": sha256_text(content), "content": content}
        for name, content in sorted(namelists.items())
    }
    identity = {
        "schema_version": _SPEC_VERSION,
        "job": {
            "id": job.get("id"),
            "name": job.get("name"),
            "period": job.get("period"),
            "domain": job.get("domain"),
            "metadata": job.get("metadata", {}),
        },
        "profile": {
            "id": profile_id,
            "configuration": profile,
        },
        "era5_input": {
            "plan_key": plan_key,
            "files": input_files,
            "provenance": {
                "source": provenance.get("source"),
                "datasets": provenance.get("datasets", []),
                "verified_at": provenance.get("verified_at"),
                "download_job_id": provenance.get("download_job_id"),
                "artificial_weather_data": False,
            },
        },
        "namelists": namelist_metadata,
        "runtime": runtimes,
        "source": {
            "repository_revision": source_revision,
            "wrf_version": "4.7.1",
            "wps_version": "4.6.0",
        },
        "postprocessing": {
            "profile": profile["postprocessing_profile"],
            "result_indexing": "metadata-json-v1",
        },
        "error_categories": ERROR_CATEGORIES,
        "steps": pipeline_steps(runtimes),
    }
    return identity, namelists


def pipeline_steps(runtime: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "id": "input-data",
            "label": "Verify ERA5 input",
            "status": "PENDING",
            "runtime": None,
            "inputs": ["era5-plan", "checksums", "provenance", "grib-files"],
            "outputs": ["verified-input-set"],
            "progress_metrics": ["verified_files", "verified_bytes"],
        },
        {
            "id": "geogrid",
            "label": "Generate geographical grid",
            "status": "PENDING",
            "runtime": runtime["wps"],
            "inputs": ["namelist.wps", "wps-geography"],
            "outputs": ["geo_em.d01.nc"],
            "progress_metrics": ["domain_grid_created"],
        },
        {
            "id": "ungrib",
            "label": "Decode ERA5 GRIB input",
            "status": "PENDING",
            "runtime": runtime["wps"],
            "inputs": ["verified-input-set", "Vtable", "namelist.wps"],
            "outputs": ["wps-intermediate-files"],
            "progress_metrics": ["decoded_requests", "decoded_time_points"],
        },
        {
            "id": "metgrid",
            "label": "Interpolate meteorological fields",
            "status": "PENDING",
            "runtime": runtime["wps"],
            "inputs": ["geo_em.d01.nc", "wps-intermediate-files", "namelist.wps"],
            "outputs": ["met_em.d01.*.nc"],
            "progress_metrics": ["met_em_time_points"],
        },
        {
            "id": "real",
            "label": "Initialize WRF domains",
            "status": "PENDING",
            "runtime": runtime["wrf"],
            "inputs": ["met_em.d01.*.nc", "namelist.input"],
            "outputs": ["wrfinput_d01", "wrfbdy_d01"],
            "progress_metrics": ["initialization_started", "boundary_files_created"],
        },
        {
            "id": "wrf",
            "label": "Run WRF simulation",
            "status": "PENDING",
            "runtime": runtime["wrf"],
            "inputs": ["wrfinput_d01", "wrfbdy_d01", "namelist.input"],
            "outputs": ["wrfout_d01_*"],
            "progress_metrics": ["simulation_time", "simulated_seconds", "output_files", "eta_seconds"],
        },
        {
            "id": "postprocessing",
            "label": "Postprocess WRF output",
            "status": "PENDING",
            "runtime": runtime["postprocessing"],
            "inputs": ["wrfout_d01_*"],
            "outputs": ["visualization-layers", "point-query-index"],
            "progress_metrics": ["processed_time_steps", "derived_layers"],
        },
        {
            "id": "result-indexing",
            "label": "Index result artifacts",
            "status": "PENDING",
            "runtime": runtime["postprocessing"],
            "inputs": ["visualization-layers", "point-query-index"],
            "outputs": ["result-metadata"],
            "progress_metrics": ["indexed_artifacts"],
        },
    ]
