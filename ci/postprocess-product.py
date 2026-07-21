#!/usr/bin/env python3
"""Product CLI facade for the existing WRF visualization postprocessor.

It preserves the established CLI and computations while adding explicit source
provenance to metadata.json. Fixture/demo executions remain marked as fixtures;
the persistent product runner accepts only ``mode = wrf``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

import postprocess_core as _core  # noqa: E402
from postprocess_core import *  # noqa: F401,F403,E402

_core_load_wrf_netcdf = _core.load_wrf_netcdf
_core_load_fixture_json = _core.load_fixture_json
_core_export_metadata = _core.export_metadata


def load_wrf_netcdf(input_dir: str | Path) -> dict[str, Any]:
    """Load real WRF output and attach deterministic input provenance."""

    input_path = Path(input_dir)
    data = _core_load_wrf_netcdf(input_path)
    wrfout_files = sorted(
        {
            path.name
            for pattern in ("wrfout_d01_*", "wrfout_d*")
            for path in input_path.glob(pattern)
            if path.is_file() and not path.is_symlink()
        }
    )
    data["provenance"] = {
        "mode": "wrf",
        "wrfout_files": wrfout_files,
    }
    return data


def load_fixture_json(path: str | Path | None = None) -> dict[str, Any]:
    """Load a compatibility fixture and label it so product runs reject it."""

    data = _core_load_fixture_json(path)
    data["provenance"] = {
        "mode": "fixture",
        "fixture": str(path) if path is not None else "built-in-demo",
        "wrfout_files": [],
    }
    return data


def export_metadata(
    output_dir: str | Path,
    data: dict[str, Any],
    layers: dict[str, Any],
    max_layers: dict[str, Any],
) -> dict[str, Any]:
    """Export existing metadata plus explicit source provenance atomically."""

    metadata = _core_export_metadata(output_dir, data, layers, max_layers)
    provenance = data.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {
            "mode": "unknown",
            "wrfout_files": [],
        }
    metadata["provenance"] = provenance
    path = Path(output_dir) / "metadata.json"
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
    temporary.replace(path)
    return metadata


_core.load_wrf_netcdf = load_wrf_netcdf
_core.load_fixture_json = load_fixture_json
_core.export_metadata = export_metadata


def main() -> None:
    _core.main()


if __name__ == "__main__":
    main()
