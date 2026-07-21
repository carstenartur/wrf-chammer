#!/usr/bin/env python3
"""Run real WRF postprocessing or result indexing in the pinned product image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POSTPROCESSOR = Path("/app/postprocess.py")


class PostprocessingStepError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one real postprocessing pipeline step")
    parser.add_argument(
        "--step", choices=("postprocessing", "result-indexing"), required=True
    )
    parser.add_argument("--specification", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    return parser.parse_args(argv)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def reject_symlink_chain(root: Path, child: Path, message: str) -> None:
    raw_root = absolute(root)
    raw_child = absolute(child)
    try:
        relative = raw_child.relative_to(raw_root)
    except ValueError as exc:
        raise PostprocessingStepError("EXECUTOR_OUTPUT_INVALID", message) from exc
    current = raw_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PostprocessingStepError("EXECUTOR_OUTPUT_INVALID", message)


def load_specification(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PostprocessingStepError(
            "NAMELIST_INVALID", "Immutable specification is unavailable."
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostprocessingStepError(
            "NAMELIST_INVALID", "Immutable specification JSON is invalid."
        ) from exc
    identity = value.get("identity") if isinstance(value, dict) else None
    provenance = identity.get("era5_input", {}).get("provenance", {}) if isinstance(identity, dict) else {}
    if (
        not isinstance(identity, dict)
        or value.get("immutable") is not True
        or value.get("execution_started") is not False
        or not isinstance(provenance, dict)
        or provenance.get("artificial_weather_data") is not False
    ):
        raise PostprocessingStepError(
            "INPUT_DATA_MISSING",
            "The immutable specification does not prove real ERA5 provenance.",
        )
    return value


def safe_files(root: Path) -> list[Path]:
    resolved_root = root.resolve()
    files: list[Path] = []
    if root.is_symlink() or not root.is_dir():
        raise PostprocessingStepError(
            "INPUT_DATA_MISSING", "Required postprocessing input directory is unavailable."
        )
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        for directory in list(directory_names):
            candidate = current / directory
            if candidate.is_symlink():
                raise PostprocessingStepError(
                    "EXECUTOR_OUTPUT_INVALID",
                    "Postprocessing directory contains a symbolic link.",
                )
        for filename in file_names:
            raw = current / filename
            reject_symlink_chain(
                resolved_root,
                raw,
                "Postprocessing file contains a symbolic link.",
            )
            resolved = raw.resolve()
            if resolved_root not in resolved.parents or not resolved.is_file():
                raise PostprocessingStepError(
                    "EXECUTOR_OUTPUT_INVALID", "Postprocessing file escaped its managed root."
                )
            files.append(resolved)
    return sorted(files)


def clear_directory(run_root: Path, directory: Path) -> None:
    reject_symlink_chain(
        run_root,
        directory,
        "Postprocessing output directory contains a symbolic link.",
    )
    resolved_run = run_root.resolve()
    resolved = directory.resolve()
    if resolved == resolved_run or resolved_run not in resolved.parents:
        raise PostprocessingStepError(
            "EXECUTOR_OUTPUT_INVALID", "Postprocessing output escaped run root."
        )
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir():
            raise PostprocessingStepError(
                "EXECUTOR_OUTPUT_INVALID", "Postprocessing output is not a safe directory."
            )
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_entries(run_root: Path, paths: list[Path], kind: str) -> list[dict[str, Any]]:
    resolved_root = run_root.resolve()
    entries: list[dict[str, Any]] = []
    for path in paths:
        reject_symlink_chain(
            resolved_root,
            path,
            "Postprocessing artifact contains a symbolic link.",
        )
        resolved = path.resolve()
        if resolved_root not in resolved.parents or not resolved.is_file():
            raise PostprocessingStepError(
                "EXECUTOR_OUTPUT_INVALID", "Postprocessing artifact is unsafe."
            )
        entries.append(
            {
                "path": resolved.relative_to(resolved_root).as_posix(),
                "kind": kind,
                "sha256": sha256_file(resolved),
                "metadata": {"size_bytes": resolved.stat().st_size},
            }
        )
    return entries


def validate_visualization_metadata(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PostprocessingStepError(
            "PROCESS_CRASH", "Postprocessor produced no metadata.json."
        )
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostprocessingStepError(
            "PROCESS_CRASH", "Postprocessor metadata is invalid."
        ) from exc
    provenance = metadata.get("provenance") if isinstance(metadata, dict) else None
    layers = metadata.get("layers") if isinstance(metadata, dict) else None
    if (
        not isinstance(provenance, dict)
        or provenance.get("mode") != "wrf"
        or not isinstance(provenance.get("wrfout_files"), list)
        or not provenance.get("wrfout_files")
        or not isinstance(layers, list)
        or not layers
    ):
        raise PostprocessingStepError(
            "PROCESS_CRASH",
            "Postprocessor metadata does not describe real WRF input products.",
        )
    return metadata


def run_postprocessing(
    args: argparse.Namespace, specification: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    wrf_directory = args.run_root / "work" / "wrf" / "wrf"
    wrf_outputs = [
        path for path in safe_files(wrf_directory) if path.name.startswith("wrfout_d")
    ]
    if not wrf_outputs:
        raise PostprocessingStepError(
            "INPUT_DATA_MISSING", "Postprocessing requires real wrfout files."
        )
    output = args.run_root / "visualizations"
    clear_directory(args.run_root, output)
    args.workdir.mkdir(parents=True, exist_ok=True)
    log_path = args.workdir / "postprocessing.log"
    if log_path.is_symlink():
        raise PostprocessingStepError(
            "EXECUTOR_OUTPUT_INVALID", "Postprocessing log is a symbolic link."
        )
    atomic_json(
        args.progress,
        {
            "phase": "postprocessing",
            "input_files": len(wrf_outputs),
            "products": 0,
        },
    )
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.run(
            [
                os.sys.executable,
                str(POSTPROCESSOR),
                "--input",
                str(wrf_directory),
                "--output",
                str(output),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if process.returncode != 0:
        raise PostprocessingStepError(
            "PROCESS_CRASH", "Real WRF postprocessing failed."
        )
    validate_visualization_metadata(output / "metadata.json")
    products = safe_files(output)
    artifacts = artifact_entries(args.run_root, products, "visualization-product")
    artifacts.extend(artifact_entries(args.run_root, [log_path], "postprocessing-log"))
    return artifacts, {
        "phase": "completed",
        "input_files": len(wrf_outputs),
        "products": len(products),
    }


def run_result_indexing(
    args: argparse.Namespace, specification: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    visualization = args.run_root / "visualizations"
    metadata = validate_visualization_metadata(visualization / "metadata.json")
    products = safe_files(visualization)
    if not products:
        raise PostprocessingStepError(
            "INPUT_DATA_MISSING", "Result indexing requires postprocessing products."
        )
    output = args.run_root / "results"
    clear_directory(args.run_root, output)
    indexed = []
    for path in products:
        indexed.append(
            {
                "path": path.relative_to(args.run_root).as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    identity = specification["identity"]
    index = {
        "version": 1,
        "created_at": utc_now(),
        "specification_key": specification.get("specification_key"),
        "source_revision": identity.get("source", {}).get("revision"),
        "era5_plan_key": identity.get("era5_input", {}).get("plan_key"),
        "runtime": identity.get("runtime"),
        "visualization_provenance": metadata.get("provenance"),
        "artificial_weather_data": False,
        "products": indexed,
    }
    index_path = output / "index.json"
    atomic_json(index_path, index)
    return artifact_entries(args.run_root, [index_path], "result-index"), {
        "phase": "completed",
        "indexed_products": len(indexed),
        "total_bytes": sum(item["size_bytes"] for item in indexed),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    try:
        specification = load_specification(args.specification)
        raw_run = absolute(args.run_root)
        raw_work = absolute(args.workdir)
        reject_symlink_chain(
            raw_run,
            raw_work,
            "Postprocessing work directory contains a symbolic link.",
        )
        args.run_root = raw_run.resolve()
        args.workdir = raw_work.resolve()
        if args.run_root not in args.workdir.parents:
            raise PostprocessingStepError(
                "EXECUTOR_OUTPUT_INVALID", "Postprocessing work directory escaped run root."
            )
        runners = {
            "postprocessing": run_postprocessing,
            "result-indexing": run_result_indexing,
        }
        artifacts, progress = runners[args.step](args, specification)
        atomic_json(
            args.result,
            {
                "status": "SUCCEEDED",
                "progress": progress,
                "artifacts": artifacts,
                "resources": {"wall_seconds": time.monotonic() - started},
            },
        )
        atomic_json(args.progress, progress)
        return 0
    except PostprocessingStepError as exc:
        atomic_json(
            args.result,
            {
                "status": "FAILED",
                "error": {"code": exc.code, "message": exc.message},
                "resources": {"wall_seconds": time.monotonic() - started},
            },
        )
        print(f"{exc.code}: {exc.message}", file=os.sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        atomic_json(
            args.result,
            {
                "status": "FAILED",
                "error": {
                    "code": "PROCESS_CRASH",
                    "message": f"Postprocessing step runner failed ({type(exc).__name__}).",
                },
                "resources": {"wall_seconds": time.monotonic() - started},
            },
        )
        print(
            f"Postprocessing step runner failed ({type(exc).__name__}).",
            file=os.sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
