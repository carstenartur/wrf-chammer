#!/usr/bin/env python3
"""Tests for canonical ERA5 request planning and cache reuse.

The tests use no network and no synthetic meteorological fields. Small non-empty
files only stand in for already downloaded GRIB files when exercising cache and
manifest behaviour.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from workbench.era5_planner import (  # noqa: E402
    Era5PlanningError,
    PRESSURE_LEVELS,
    PRESSURE_VARIABLES,
    SINGLE_LEVEL_VARIABLES,
    bounds_from_job,
    build_era5_plan,
    build_era5_plan_from_job,
)

XAVER_PERIOD = {
    "start": "2013-12-05T12:00:00Z",
    "end": "2013-12-06T06:00:00Z",
}
XAVER_BOUNDS = {"west": 2.0, "south": 51.0, "east": 14.0, "north": 58.0}


def expect_error(callback, fragment: str) -> None:
    try:
        callback()
    except Era5PlanningError as exc:
        if not any(fragment in error for error in exc.errors):
            raise AssertionError(f"Expected {fragment!r} in {exc.errors!r}") from exc
    else:
        raise AssertionError(f"Expected Era5PlanningError containing {fragment!r}")


def assert_request_shape(plan: dict) -> None:
    requests = plan["download_config"]["requests"]
    assert list(requests) == [
        "pressure_levels_20131205",
        "single_levels_20131205",
        "pressure_levels_20131206",
        "single_levels_20131206",
    ]

    day_one_pressure = requests["pressure_levels_20131205"]
    day_one_single = requests["single_levels_20131205"]
    day_two_pressure = requests["pressure_levels_20131206"]

    assert day_one_pressure["dataset"] == "reanalysis-era5-pressure-levels"
    assert day_one_pressure["ungrib_prefix"] == "PLEV"
    assert day_one_pressure["request"]["pressure_level"] == PRESSURE_LEVELS
    assert day_one_pressure["request"]["variable"] == PRESSURE_VARIABLES
    assert day_one_pressure["request"]["time"] == [f"{hour:02d}:00" for hour in range(12, 24)]

    assert day_one_single["dataset"] == "reanalysis-era5-single-levels"
    assert day_one_single["ungrib_prefix"] == "SFC"
    assert day_one_single["request"]["variable"] == SINGLE_LEVEL_VARIABLES
    assert "pressure_level" not in day_one_single["request"]

    assert day_two_pressure["request"]["time"] == [f"{hour:02d}:00" for hour in range(0, 7)]
    assert all("request_key" not in definition for definition in requests.values())


def test_stable_plan_and_real_data_provenance() -> None:
    first = build_era5_plan(period=XAVER_PERIOD, bounds=XAVER_BOUNDS)
    second = build_era5_plan(period=XAVER_PERIOD, bounds=XAVER_BOUNDS)

    assert first["plan_key"] == second["plan_key"]
    assert len(first["plan_key"]) == 64
    assert first["period"]["time_points"] == 19
    assert first["domain"]["cds_area"] == [59.0, 1.0, 50.0, 15.0]
    assert first["cache"]["status"] == "missing"
    assert first["cache"]["root"] is None
    assert first["estimated_download"]["bytes"] > 0
    assert first["estimated_download"]["gigabytes"] > 0
    assert first["provenance"]["artificial_weather_data"] is False
    assert "ERA5 reanalysis" in first["provenance"]["source"]
    assert_request_shape(first)

    request_keys = [entry["request_key"] for entry in first["requests"]]
    assert len(request_keys) == 4
    assert len(set(request_keys)) == 4
    assert all(len(key) == 64 for key in request_keys)


def test_job_bounds_resolution() -> None:
    job_with_exact_bounds = {
        "period": XAVER_PERIOD,
        "metadata": {"domain_bounds": XAVER_BOUNDS},
        "domain": {
            "center_lat": 0,
            "center_lon": 0,
            "dx_km": 99,
            "dy_km": 99,
            "e_we": 3,
            "e_sn": 3,
        },
    }
    assert bounds_from_job(job_with_exact_bounds) == XAVER_BOUNDS
    exact_plan = build_era5_plan_from_job(job_with_exact_bounds)
    assert exact_plan["domain"]["bounds"] == XAVER_BOUNDS

    grid_job = {
        "period": XAVER_PERIOD,
        "domain": {
            "center_lat": 54.5,
            "center_lon": 8.0,
            "dx_km": 9,
            "dy_km": 9,
            "e_we": 91,
            "e_sn": 91,
        },
    }
    approximated = bounds_from_job(grid_job)
    assert approximated["west"] < 8.0 < approximated["east"]
    assert approximated["south"] < 54.5 < approximated["north"]
    assert approximated["east"] - approximated["west"] > 10
    assert approximated["north"] - approximated["south"] > 6


def seed_cache(plan: dict, *, full_indices: set[int], partial_indices: set[int]) -> Path:
    plan_dir = Path(plan["cache"]["plan_directory"])
    plan_dir.mkdir(parents=True, exist_ok=True)
    for index, entry in enumerate(plan["requests"]):
        target = plan_dir / entry["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if index in full_indices:
            target.write_bytes(b"GRIB-CACHED")
        if index in partial_indices:
            Path(f"{target}.part").write_bytes(b"PARTIAL")
    return plan_dir


def test_cache_states_and_downloader_compatibility() -> None:
    with tempfile.TemporaryDirectory(prefix="wrf-era5-planner-") as temp:
        root = Path(temp)
        cache_root = root / "cache"
        initial = build_era5_plan(
            period=XAVER_PERIOD,
            bounds=XAVER_BOUNDS,
            cache_root=cache_root,
        )
        assert initial["cache"]["status"] == "missing"
        assert initial["cache"]["coverage_percent"] == 0.0

        plan_dir = seed_cache(initial, full_indices={0}, partial_indices={1})
        partial = build_era5_plan(
            period=XAVER_PERIOD,
            bounds=XAVER_BOUNDS,
            cache_root=cache_root,
        )
        assert partial["plan_key"] == initial["plan_key"]
        assert partial["cache"]["status"] == "partial"
        assert partial["cache"]["hits"] == 1
        assert partial["cache"]["partial_entries"] == 1
        assert partial["cache"]["coverage_percent"] == 25.0

        seed_cache(initial, full_indices={0, 1, 2, 3}, partial_indices=set())
        complete = build_era5_plan(
            period=XAVER_PERIOD,
            bounds=XAVER_BOUNDS,
            cache_root=cache_root,
        )
        assert complete["cache"]["status"] == "complete"
        assert complete["cache"]["hits"] == 4
        assert complete["cache"]["coverage_percent"] == 100.0
        assert all(entry["present"] for entry in complete["cache"]["entries"])

        config_path = root / "download-config.json"
        manifest_path = root / "manifest.json"
        config_path.write_text(json.dumps(complete["download_config"], indent=2) + "\n", encoding="utf-8")

        env = os.environ.copy()
        env.pop("CDSAPI_KEY", None)
        env.pop("CDSAPI_URL", None)
        subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "ci" / "download-era5.py"),
                "--config",
                str(config_path),
                "--output-dir",
                str(plan_dir),
                "--manifest",
                str(manifest_path),
            ],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(manifest["outputs"]) == 4
        assert all(entry["cached"] is True for entry in manifest["outputs"])
        assert all(entry["size_bytes"] > 0 for entry in manifest["outputs"])
        assert {entry["ungrib_prefix"] for entry in manifest["outputs"]} == {"PLEV", "SFC"}
        assert all(len(entry["request_sha256"]) == 64 for entry in manifest["outputs"])


def test_validation_errors() -> None:
    expect_error(
        lambda: build_era5_plan(
            period={"start": "2013-12-05T12:30:00Z", "end": "2013-12-06T06:00:00Z"},
            bounds=XAVER_BOUNDS,
        ),
        "whole UTC hour",
    )
    expect_error(
        lambda: build_era5_plan(
            period=XAVER_PERIOD,
            bounds={"west": 14, "south": 51, "east": 2, "north": 58},
        ),
        "west < east",
    )
    expect_error(
        lambda: build_era5_plan(period=XAVER_PERIOD, bounds=XAVER_BOUNDS, interval_hours=5),
        "divisible",
    )
    expect_error(
        lambda: build_era5_plan(period=XAVER_PERIOD, bounds=XAVER_BOUNDS, margin_degrees=11),
        "margin_degrees",
    )
    expect_error(
        lambda: build_era5_plan(
            period={"start": "2013-01-01T00:00:00Z", "end": "2013-02-15T00:00:00Z"},
            bounds=XAVER_BOUNDS,
        ),
        "must not exceed",
    )


def main() -> int:
    test_stable_plan_and_real_data_provenance()
    test_job_bounds_resolution()
    test_cache_states_and_downloader_compatibility()
    test_validation_errors()
    print("ERA5 planner tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
