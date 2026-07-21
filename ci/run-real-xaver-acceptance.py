#!/usr/bin/env python3
"""Public real-Xaver acceptance command with canonical planning contracts."""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

import _run_real_xaver_acceptance_core as _core  # noqa: E402
from _run_real_xaver_acceptance_core import *  # noqa: F401,F403,E402

_core_verify_real_plan = _core.verify_real_plan
_core_verify_result_index = _core.verify_result_index
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_preview(args: Any, repo_root: Path) -> dict[str, Any]:
    """Build a schema-valid planning preview while preserving real-run intent."""

    plan = _core.plan_domain(
        {
            "label": "Xaver North Sea real acceptance",
            "bounds": [args.west, args.south, args.east, args.north],
            "period": {"start": args.start, "end": args.end},
            "quality_profile": args.quality_profile,
        }
    )
    config = _core.build_job_config(
        "xaver",
        mode="dry-run",
        job_id=args.job_id,
        catalogue=_core.load_catalogue(repo_root),
    )
    config["period"] = {
        "start": plan["period"]["start"],
        "end": plan["period"]["end"],
    }
    domain = plan["domain"]
    config["domain"] = {
        "label": domain["label"],
        "center_lat": domain["center_lat"],
        "center_lon": domain["center_lon"],
        "dx_km": domain["dx_km"],
        "dy_km": domain["dy_km"],
        "e_we": domain["e_we"],
        "e_sn": domain["e_sn"],
    }
    metadata = config.setdefault("metadata", {})
    metadata.update(
        {
            "domain_source": "real-xaver-acceptance",
            "domain_bounds": domain["bounds"],
            "quality_profile": plan["quality_profile"]["id"],
            "resource_estimate": plan["resources"],
            "acceptance_workflow": True,
            "requested_data_mode": "real-data",
            "requested_execution_mode": "era5-wrf",
        }
    )
    errors = _core.validate_config(config)
    if errors:
        raise _core.AcceptanceError(
            "Invalid Xaver planning configuration: " + "; ".join(errors)
        )
    return {
        "ok": True,
        "valid": True,
        "errors": [],
        "warnings": plan["warnings"],
        "requested_execution_mode": "era5-wrf",
        "plan": plan,
        "config": config,
    }


def verify_real_plan(
    data_service: Any,
    plan_key: str,
    preview: dict[str, Any],
) -> dict[str, Any]:
    """Match start/end while allowing canonical planner metadata fields."""

    plan_directory = _core.require_directory(
        data_service.plan_directory(plan_key), "ERA5 plan directory"
    )
    plan_path = _core.require_regular_file(
        plan_directory / "era5-plan.json", "ERA5 plan"
    )
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _core.AcceptanceError("ERA5 plan JSON cannot be read.") from exc
    plan_period = plan.get("period") if isinstance(plan, dict) else None
    requested = preview.get("config", {}).get("period")
    if not isinstance(plan_period, dict) or not isinstance(requested, dict):
        raise _core.AcceptanceError("ERA5 or Xaver period is invalid.")
    for field in ("start", "end"):
        if plan_period.get(field) != requested.get(field):
            raise _core.AcceptanceError(
                f"ERA5 plan {field} does not match the Xaver preview."
            )
    compatible_preview = copy.deepcopy(preview)
    compatible_preview["config"]["period"] = plan_period
    return _core_verify_real_plan(
        data_service, plan_key, compatible_preview
    )


def verify_result_index(
    repo_root: Path,
    job: dict[str, Any],
    specification: dict[str, Any],
) -> dict[str, Any]:
    """Validate result metadata types before filesystem/checksum verification."""

    artifacts = [
        artifact
        for artifact in job.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("kind") == "result-index"
    ]
    if len(artifacts) != 1:
        raise _core.AcceptanceError(
            "Successful job must expose exactly one result index."
        )
    relative_index = artifacts[0].get("relative_path")
    if not isinstance(relative_index, str) or not relative_index.strip():
        raise _core.AcceptanceError("Result index artifact path is invalid.")
    run_root = repo_root / "workbench-runs" / "simulations" / job["id"]
    index_path = (run_root / relative_index).resolve()
    if run_root.resolve() not in index_path.parents:
        raise _core.AcceptanceError("Result index escaped the managed job directory.")
    try:
        index = json.loads(
            _core.require_regular_file(index_path, "Result index").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise _core.AcceptanceError("Result index JSON is invalid.") from exc
    products = index.get("products") if isinstance(index, dict) else None
    if not isinstance(products, list) or not products:
        raise _core.AcceptanceError("Result index has no products.")
    for product in products:
        if not isinstance(product, dict):
            raise _core.AcceptanceError("Result product metadata is not an object.")
        relative = product.get("path")
        digest = product.get("sha256")
        size = product.get("size_bytes")
        if not isinstance(relative, str) or not relative.strip():
            raise _core.AcceptanceError("Result product path is invalid.")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise _core.AcceptanceError(
                f"Result product SHA-256 metadata is invalid: {relative}"
            )
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise _core.AcceptanceError(
                f"Result product size metadata is invalid: {relative}"
            )
    return _core_verify_result_index(repo_root, job, specification)


_core.build_preview = build_preview
_core.verify_real_plan = verify_real_plan
_core.verify_result_index = verify_result_index


def main(argv: list[str] | None = None) -> int:
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
