#!/usr/bin/env python3
"""Unit tests for server-side WRF domain planning."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workbench.domain_planner import DomainPlanningError, available_profiles, plan_domain  # noqa: E402


def planning_request(profile: str = "balanced") -> dict:
    return {
        "bounds": {"west": 2.0, "south": 51.0, "east": 14.0, "north": 58.0},
        "period": {"start": "2013-12-05T12:00:00Z", "end": "2013-12-06T06:00:00Z"},
        "quality_profile": profile,
    }


def assert_raises(request: dict, expected_fragment: str) -> None:
    try:
        plan_domain(request)
    except DomainPlanningError as exc:
        assert any(expected_fragment in error for error in exc.errors), exc.errors
    else:
        raise AssertionError(f"Expected DomainPlanningError containing {expected_fragment!r}")


def main() -> int:
    profiles = {profile["id"]: profile for profile in available_profiles()}
    assert set(profiles) == {"quick-preview", "balanced", "detailed"}
    assert profiles["detailed"]["grid_spacing_km"] < profiles["balanced"]["grid_spacing_km"]

    balanced = plan_domain(planning_request())
    domain = balanced["domain"]
    assert balanced["valid"] is True
    assert domain["center_lat"] == 54.5
    assert domain["center_lon"] == 8.0
    assert domain["dx_km"] == 9.0
    assert domain["e_we"] == 91
    assert domain["e_sn"] == 91
    assert (domain["e_we"] - 1) % 6 == 0
    assert (domain["e_sn"] - 1) % 6 == 0
    assert 700 < domain["width_km"] < 850
    assert 700 < domain["height_km"] < 850
    assert balanced["resources"]["estimated_wall_clock_minutes"]["upper"] > 0
    assert balanced["resources"]["estimated_storage_gb"]["working_total"] > 0

    quick = plan_domain(planning_request("quick-preview"))
    detailed = plan_domain(planning_request("detailed"))
    assert quick["domain"]["e_we"] < balanced["domain"]["e_we"]
    assert detailed["domain"]["e_we"] > balanced["domain"]["e_we"]
    assert detailed["resources"]["estimated_ram_gb"]["recommended"] > balanced["resources"]["estimated_ram_gb"]["recommended"]

    expert = planning_request()
    expert["expert"] = {
        "grid_spacing_km": 12,
        "vertical_levels": 40,
        "output_interval_minutes": 90,
    }
    expert_plan = plan_domain(expert)
    assert expert_plan["domain"]["dx_km"] == 12
    assert expert_plan["domain"]["vertical_levels"] == 40
    assert expert_plan["period"]["output_interval_minutes"] == 90

    invalid_bounds = planning_request()
    invalid_bounds["bounds"] = {"west": 14, "south": 51, "east": 2, "north": 58}
    assert_raises(invalid_bounds, "bounds.west")

    too_long = planning_request()
    too_long["period"]["end"] = "2014-01-01T00:00:00Z"
    assert_raises(too_long, "must not exceed")

    too_fine = planning_request()
    too_fine["expert"] = {"grid_spacing_km": 0.5}
    assert_raises(too_fine, "grid spacing")

    print("Domain planner tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
