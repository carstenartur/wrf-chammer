#!/usr/bin/env python3
"""Pinned isolated container executor for real postprocessing and indexing."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

_RUNTIME_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SUPPORTED_STEPS = {"postprocessing", "result-indexing"}


class ContainerExecutorError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one postprocessing step in a pinned container"
    )
    parser.add_argument("--step", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--specification-key", required=True)
    parser.add_argument("--specification-directory", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--step-directory", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    return parser.parse_args(argv)


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


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def reject_symlink_chain(root: Path, child: Path, label: str) -> None:
    raw_root = absolute(root)
    raw_child = absolute(child)
    try:
        relative = raw_child.relative_to(raw_root)
    except ValueError as exc:
        raise ContainerExecutorError(
            "EXECUTOR_OUTPUT_INVALID", f"{label} escaped its managed root."
        ) from exc
    current = raw_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ContainerExecutorError(
                "EXECUTOR_OUTPUT_INVALID", f"{label} contains a symbolic link."
            )


def load_specification(directory: Path, expected_key: str) -> dict[str, Any]:
    raw_directory = absolute(directory)
    if raw_directory.is_symlink():
        raise ContainerExecutorError(
            "INPUT_DATA_MISSING",
            "Immutable specification directory is a symbolic link.",
        )
    path = raw_directory / "specification.json"
    if path.is_symlink() or not path.is_file():
        raise ContainerExecutorError(
            "INPUT_DATA_MISSING", "Immutable specification is unavailable."
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContainerExecutorError(
            "INPUT_DATA_MISSING", "Immutable specification JSON is invalid."
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("specification_key") != expected_key
        or value.get("immutable") is not True
        or value.get("execution_started") is not False
        or not isinstance(value.get("identity"), dict)
    ):
        raise ContainerExecutorError(
            "INPUT_DATA_MISSING", "Immutable specification identity is invalid."
        )
    return value


def contained_directory(root: Path, child: Path, label: str) -> Path:
    reject_symlink_chain(root, child, label)
    resolved_root = root.resolve()
    resolved = child.resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise ContainerExecutorError(
            "EXECUTOR_OUTPUT_INVALID", f"{label} escaped its managed root."
        )
    return resolved


def container_path(run_root: Path, path: Path) -> str:
    reject_symlink_chain(run_root, path, "Executor state path")
    resolved_run = run_root.resolve()
    resolved = path.resolve()
    if resolved == resolved_run or resolved_run not in resolved.parents:
        raise ContainerExecutorError(
            "EXECUTOR_OUTPUT_INVALID", "Executor state path escaped run root."
        )
    relative = resolved.relative_to(resolved_run).as_posix()
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.split("/")
    ):
        raise ContainerExecutorError(
            "EXECUTOR_OUTPUT_INVALID", "Executor state path is unsafe."
        )
    return f"/run/{relative}"


def clear_state_file(run_root: Path, path: Path, label: str) -> None:
    container_path(run_root, path)
    if path.is_symlink():
        raise ContainerExecutorError(
            "EXECUTOR_OUTPUT_INVALID", f"{label} is a symbolic link."
        )
    if path.exists():
        if not path.is_file():
            raise ContainerExecutorError(
                "EXECUTOR_OUTPUT_INVALID", f"{label} is not a regular file."
            )
        path.unlink()


def engine_program() -> str:
    configured = os.environ.get("WRF_CHAMMER_CONTAINER_ENGINE", "docker")
    program = shutil.which(configured)
    if not program:
        raise ContainerExecutorError(
            "RUNTIME_IMAGE_MISMATCH",
            f"Container engine {configured!r} is not available.",
        )
    return program


def inspect_image(engine: str, reference: str, identity: str) -> str:
    reference = reference.strip()
    if not reference or not _RUNTIME_ID_RE.fullmatch(identity):
        raise ContainerExecutorError(
            "RUNTIME_IMAGE_MISMATCH",
            "Postprocessing runtime reference or pinned identity is invalid.",
        )
    process = subprocess.run(
        [engine, "image", "inspect", reference],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise ContainerExecutorError(
            "RUNTIME_IMAGE_MISMATCH",
            "The configured postprocessing runtime image is not available locally.",
        )
    try:
        payload = json.loads(process.stdout)
        image = payload[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise ContainerExecutorError(
            "RUNTIME_IMAGE_MISMATCH",
            "Container image inspection returned invalid metadata.",
        ) from exc
    image_id = image.get("Id") if isinstance(image, dict) else None
    digests = image.get("RepoDigests", []) if isinstance(image, dict) else []
    if image_id == identity:
        return identity
    if isinstance(digests, list):
        for digest in digests:
            if isinstance(digest, str) and digest.endswith(f"@{identity}"):
                return digest
    raise ContainerExecutorError(
        "RUNTIME_IMAGE_MISMATCH",
        "The local postprocessing image does not match the immutable identity.",
    )


def mount_argument(path: Path, destination: str, mode: str) -> str:
    return f"{path.resolve()}:{destination}:{mode}"


def build_command(args: argparse.Namespace, specification: dict[str, Any]) -> list[str]:
    if args.step not in _SUPPORTED_STEPS:
        raise ContainerExecutorError(
            "EXECUTOR_UNAVAILABLE",
            f"The postprocessing executor does not implement step {args.step}.",
        )
    run_root = args.run_directory.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    contained_directory(run_root, args.step_directory, "Step directory").mkdir(
        parents=True, exist_ok=True
    )
    specification_directory = absolute(args.specification_directory)
    if specification_directory.is_symlink():
        raise ContainerExecutorError(
            "INPUT_DATA_MISSING",
            "Immutable specification directory is a symbolic link.",
        )
    runtime = specification["identity"].get("runtime", {}).get("postprocessing")
    if not isinstance(runtime, dict):
        raise ContainerExecutorError(
            "RUNTIME_IMAGE_MISMATCH", "Postprocessing runtime snapshot is missing."
        )
    reference = runtime.get("reference")
    pinned = runtime.get("identity")
    if not isinstance(reference, str) or not isinstance(pinned, str):
        raise ContainerExecutorError(
            "RUNTIME_IMAGE_MISMATCH", "Postprocessing runtime snapshot is invalid."
        )
    engine = engine_program()
    selector = inspect_image(engine, reference, pinned)
    workdir = run_root / "work" / "postprocessing" / args.step
    workdir.mkdir(parents=True, exist_ok=True)
    command = [
        engine,
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit",
        os.environ.get("WRF_CHAMMER_POSTPROCESSING_PIDS_LIMIT", "256"),
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=512m",
    ]
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        command += ["--user", f"{os.getuid()}:{os.getgid()}"]
    cpus = os.environ.get("WRF_CHAMMER_POSTPROCESSING_CPUS")
    memory = os.environ.get("WRF_CHAMMER_POSTPROCESSING_MEMORY")
    if cpus:
        command += ["--cpus", cpus]
    if memory:
        command += ["--memory", memory]
    command += [
        "-v",
        mount_argument(specification_directory, "/spec", "ro"),
        "-v",
        mount_argument(run_root, "/run", "rw"),
        "--entrypoint",
        "python3",
        selector,
        "/usr/local/bin/run-postprocessing-step.py",
        "--step",
        args.step,
        "--specification",
        "/spec/specification.json",
        "--run-root",
        "/run",
        "--workdir",
        f"/run/work/postprocessing/{args.step}",
        "--result",
        container_path(run_root, args.result),
        "--progress",
        container_path(run_root, args.progress),
    ]
    return command


def write_failure(path: Path, code: str, message: str) -> None:
    atomic_json(
        path, {"status": "FAILED", "error": {"code": code, "message": message}}
    )


def read_result(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_root = args.run_directory.resolve()
        run_root.mkdir(parents=True, exist_ok=True)
        clear_state_file(run_root, args.result, "Executor result")
        clear_state_file(run_root, args.progress, "Executor progress")
        specification = load_specification(
            args.specification_directory, args.specification_key
        )
        atomic_json(
            args.progress, {"phase": "starting-container", "step": args.step}
        )
        command = build_command(args, specification)
        process = subprocess.run(
            command,
            cwd=Path.cwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        payload = read_result(args.result)
        if process.returncode != 0:
            if not payload:
                write_failure(
                    args.result,
                    "PROCESS_CRASH",
                    (
                        "Postprocessing container exited unsuccessfully without "
                        "a classified result document."
                    ),
                )
            return process.returncode
        if payload.get("status") != "SUCCEEDED":
            if not payload:
                write_failure(
                    args.result,
                    "PROCESS_CRASH",
                    (
                        "Postprocessing container exited successfully without "
                        "a valid success result."
                    ),
                )
            return 1
        return 0
    except ContainerExecutorError as exc:
        write_failure(args.result, exc.code, exc.message)
        print(f"{exc.code}: {exc.message}", file=os.sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        write_failure(
            args.result,
            "PROCESS_CRASH",
            f"Postprocessing container executor failed ({type(exc).__name__}).",
        )
        print(
            f"Postprocessing container executor failed ({type(exc).__name__}).",
            file=os.sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
