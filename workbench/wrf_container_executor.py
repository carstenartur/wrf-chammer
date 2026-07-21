#!/usr/bin/env python3
"""Hardened public entry point for pinned WRF container execution."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from workbench import _wrf_container_executor_core as _core
from workbench._wrf_container_executor_core import *  # noqa: F401,F403

_core_build_command = _core.build_command
_core_mount_argument = _core.mount_argument


def load_specification(directory: Path, expected_key: str) -> dict[str, Any]:
    """Load the canonical immutable run specification."""

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


def mount_argument(path: Path, destination: str, mode: str) -> str:
    """Validate every container mount target and access mode."""

    if ":" in destination or not destination.startswith("/"):
        raise AssertionError(f"Invalid container mount destination: {destination}")
    if mode not in {"ro", "rw"}:
        raise AssertionError(f"Invalid container mount mode: {mode}")
    return _core_mount_argument(path, destination, mode)


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


def run_container(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run Docker without persisting unclassified container diagnostics."""

    return subprocess.run(
        command,
        cwd=Path.cwd(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


_core.load_specification = load_specification
_core.mount_argument = mount_argument
_core.build_command = build_command


def main(argv: list[str] | None = None) -> int:
    args = _core.parse_args(argv)
    try:
        run_root = args.run_directory.resolve()
        run_root.mkdir(parents=True, exist_ok=True)
        _core.clear_state_file(run_root, args.result, "Executor result")
        _core.clear_state_file(run_root, args.progress, "Executor progress")
        specification = load_specification(
            args.specification_directory, args.specification_key
        )
        _core.atomic_json(
            args.progress, {"phase": "starting-container", "step": args.step}
        )
        command = build_command(args, specification)
        process = run_container(command)
        payload = _core.read_result(args.result)
        if process.returncode != 0:
            if not payload:
                _core.write_failure(
                    args.result,
                    "PROCESS_CRASH",
                    (
                        "WRF container exited unsuccessfully without a "
                        "classified result document."
                    ),
                )
            return process.returncode
        if payload.get("status") != "SUCCEEDED":
            if not payload:
                _core.write_failure(
                    args.result,
                    "PROCESS_CRASH",
                    "WRF container exited successfully without a valid success result.",
                )
            return 1
        return 0
    except _core.ContainerExecutorError as exc:
        _core.write_failure(args.result, exc.code, exc.message)
        print(f"{exc.code}: {exc.message}", file=os.sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - final classification
        _core.write_failure(
            args.result,
            "PROCESS_CRASH",
            f"WRF container executor failed ({type(exc).__name__}).",
        )
        print(
            f"WRF container executor failed ({type(exc).__name__}).",
            file=os.sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
