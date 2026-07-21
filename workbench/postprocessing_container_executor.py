#!/usr/bin/env python3
"""Hardened public entry point for pinned postprocessing execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workbench import _postprocessing_container_executor_core as _core
from workbench._postprocessing_container_executor_core import *  # noqa: F401,F403

_core_build_command = _core.build_command


def load_specification(directory: Path, expected_key: str) -> dict[str, Any]:
    """Validate the canonical immutable run specification."""

    raw_directory = _core.absolute(directory)
    if raw_directory.is_symlink():
        raise _core.ContainerExecutorError(
            "NAMELIST_INVALID",
            "Immutable specification directory is a symbolic link.",
        )
    path = raw_directory / "run-specification.json"
    if path.is_symlink() or not path.is_file():
        raise _core.ContainerExecutorError(
            "NAMELIST_INVALID", "Immutable run specification is unavailable."
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _core.ContainerExecutorError(
            "NAMELIST_INVALID", "Immutable run specification JSON is invalid."
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("specification_key") != expected_key
        or value.get("immutable") is not True
        or value.get("execution_started") is not False
        or not isinstance(value.get("identity"), dict)
    ):
        raise _core.ContainerExecutorError(
            "NAMELIST_INVALID", "Immutable run specification identity is invalid."
        )
    return value


def build_command(args: Any, specification: dict[str, Any]) -> list[str]:
    """Build an isolated command using the canonical spec filename."""

    command = _core_build_command(args, specification)
    return [
        "--security-opt=no-new-privileges:true"
        if item == "--security-opt=no-new-privileges"
        else "/spec/run-specification.json"
        if item == "/spec/specification.json"
        else item
        for item in command
    ]


_core.load_specification = load_specification
_core.build_command = build_command


def main(argv: list[str] | None = None) -> int:
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
