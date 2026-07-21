#!/usr/bin/env python3
"""Route persistent pipeline steps to pinned container executors."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from workbench import wps_container_executor, wrf_container_executor

_WPS_STEPS = {"geogrid", "ungrib", "metgrid"}
_WRF_STEPS = {"real", "wrf"}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def parse_route(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--step", required=True)
    parser.add_argument("--result", type=Path, required=True)
    route, _unknown = parser.parse_known_args(arguments)
    return route, arguments


def main(argv: list[str] | None = None) -> int:
    route, arguments = parse_route(argv)
    if route.step in _WPS_STEPS:
        return wps_container_executor.main(arguments)
    if route.step in _WRF_STEPS:
        return wrf_container_executor.main(arguments)
    atomic_json(
        route.result,
        {
            "status": "FAILED",
            "error": {
                "code": "EXECUTOR_UNAVAILABLE",
                "message": f"No pinned container executor is implemented for step {route.step}.",
            },
        },
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
