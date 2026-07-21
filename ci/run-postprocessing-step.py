#!/usr/bin/env python3
"""Public postprocessing runner with canonical result provenance."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

import _run_postprocessing_step_core as _core  # noqa: E402
from _run_postprocessing_step_core import *  # noqa: F401,F403,E402


def run_result_indexing(
    args: Any, specification: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Index products using the canonical immutable source-revision field."""

    visualization = args.run_root / "visualizations"
    metadata = _core.validate_visualization_metadata(
        visualization / "metadata.json"
    )
    products = _core.safe_files(visualization)
    if not products:
        raise _core.PostprocessingStepError(
            "INPUT_DATA_MISSING",
            "Result indexing requires postprocessing products.",
        )
    output = args.run_root / "results"
    _core.clear_directory(args.run_root, output)
    indexed = [
        {
            "path": path.relative_to(args.run_root).as_posix(),
            "sha256": _core.sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in products
    ]
    identity = specification["identity"]
    source = identity.get("source", {})
    source_revision = (
        source.get("repository_revision") if isinstance(source, dict) else None
    )
    if not isinstance(source_revision, str) or not source_revision:
        raise _core.PostprocessingStepError(
            "NAMELIST_INVALID",
            "Immutable source repository revision is unavailable.",
        )
    index = {
        "version": 1,
        "created_at": _core.utc_now(),
        "specification_key": specification.get("specification_key"),
        "source_revision": source_revision,
        "era5_plan_key": identity.get("era5_input", {}).get("plan_key"),
        "runtime": identity.get("runtime"),
        "visualization_provenance": metadata.get("provenance"),
        "artificial_weather_data": False,
        "products": indexed,
    }
    index_path = output / "index.json"
    _core.atomic_json(index_path, index)
    return _core.artifact_entries(
        args.run_root, [index_path], "result-index"
    ), {
        "phase": "completed",
        "indexed_products": len(indexed),
        "total_bytes": sum(item["size_bytes"] for item in indexed),
    }


_core.run_result_indexing = run_result_indexing


def main(argv: list[str] | None = None) -> int:
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
