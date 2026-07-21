#!/usr/bin/env python3
"""Execute one real WPS step inside the pinned ERA5/WPS runtime image."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

WPS_DIR = Path("/opt/wps")
WPS_ASSETS = Path("/opt/wps-assets")
VTABLE = WPS_ASSETS / "Variable_Tables" / "Vtable.ERA-interim.pl"


class WpsStepError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one WPS pipeline step")
    parser.add_argument("--step", choices=("geogrid", "ungrib", "metgrid"), required=True)
    parser.add_argument("--specification", type=Path, required=True)
    parser.add_argument("--era5-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--geog-root", type=Path)
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


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def reject_symlink_components(root: Path, child: Path, code: str, message: str) -> None:
    raw_root = _absolute(root)
    raw_child = _absolute(child)
    try:
        relative = raw_child.relative_to(raw_root)
    except ValueError as exc:
        raise WpsStepError(code, message) from exc
    current = raw_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise WpsStepError(code, message)


def load_specification(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise WpsStepError("NAMELIST_INVALID", "Immutable specification is unavailable.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WpsStepError("NAMELIST_INVALID", "Immutable specification JSON is invalid.") from exc
    identity = value.get("identity") if isinstance(value, dict) else None
    if (
        not isinstance(identity, dict)
        or value.get("immutable") is not True
        or value.get("execution_started") is not False
    ):
        raise WpsStepError("NAMELIST_INVALID", "Immutable specification identity is invalid.")
    return value


def safe_child(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or "\\" in relative:
        raise WpsStepError("INPUT_DATA_MISSING", "ERA5 input path is invalid.")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise WpsStepError("INPUT_DATA_MISSING", "ERA5 input path is unsafe.")
    raw_root = _absolute(root)
    raw_candidate = raw_root / Path(*pure.parts)
    reject_symlink_components(
        raw_root,
        raw_candidate,
        "INPUT_DATA_MISSING",
        "ERA5 input contains a symbolic link.",
    )
    resolved_root = raw_root.resolve()
    candidate = raw_candidate.resolve()
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise WpsStepError("INPUT_DATA_MISSING", "ERA5 input escaped its mounted root.")
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise WpsStepError("INPUT_DATA_MISSING", f"ERA5 input is missing: {relative}.")
    return candidate


def replace_assignment(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(^\s*{re.escape(key)}\s*=).*$", flags=re.MULTILINE)
    if not pattern.search(text):
        raise WpsStepError("NAMELIST_INVALID", f"namelist.wps has no {key} assignment.")
    return pattern.sub(rf"\1 {value}", text)


def link_force(source: Path, target: Path) -> None:
    if target.is_symlink() or target.exists():
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.symlink_to(source)


def copy_namelist(specification_path: Path, workdir: Path) -> Path:
    source = specification_path.parent / "namelist.wps"
    if source.is_symlink() or not source.is_file():
        raise WpsStepError("NAMELIST_INVALID", "Frozen namelist.wps is missing.")
    target = workdir / "namelist.wps"
    if target.is_symlink():
        raise WpsStepError("NAMELIST_INVALID", "WPS work namelist is a symbolic link.")
    if target.exists() and not target.is_file():
        raise WpsStepError("NAMELIST_INVALID", "WPS work namelist is not a regular file.")
    shutil.copyfile(source, target)
    return target


def remove_previous(paths: list[Path]) -> None:
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            raise WpsStepError("EXECUTOR_OUTPUT_INVALID", "Previous WPS output is not a regular file.")


def prefix_for(metadata: dict[str, Any]) -> str:
    marker = f"{metadata.get('request_name', '')} {metadata.get('path', '')}".lower()
    if "pressure" in marker or "plev" in marker:
        return "PLEV"
    if "single" in marker or "surface" in marker or "sfc" in marker:
        return "SFC"
    raise WpsStepError(
        "INPUT_DATA_MISSING",
        "The immutable ERA5 request cannot be mapped to a WPS ungrib prefix.",
    )


def run_checked(command: list[str], cwd: Path, log_name: str) -> None:
    log_path = cwd / log_name
    if log_path.is_symlink():
        raise WpsStepError("EXECUTOR_OUTPUT_INVALID", "WPS executor log is a symbolic link.")
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if process.returncode != 0:
        tail = ""
        try:
            tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
        except OSError:
            pass
        code = "PROCESS_CRASH"
        lowered = tail.lower()
        if "geog_data_path" in lowered or "geographical" in lowered or "geogrid.tbl" in lowered:
            code = "WPS_GEOGRAPHY_MISSING"
        elif "namelist" in lowered:
            code = "NAMELIST_INVALID"
        raise WpsStepError(code, f"{' '.join(command)} failed with exit code {process.returncode}.")


def relative_artifacts(run_root: Path, paths: list[Path], kind: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    resolved_root = run_root.resolve()
    seen: set[Path] = set()
    for original in paths:
        raw = _absolute(original)
        reject_symlink_components(
            run_root,
            raw,
            "EXECUTOR_OUTPUT_INVALID",
            "WPS produced a symbolic-link artifact.",
        )
        resolved = raw.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file() or resolved_root not in resolved.parents:
            raise WpsStepError("EXECUTOR_OUTPUT_INVALID", "WPS produced an unsafe artifact.")
        result.append({"path": resolved.relative_to(resolved_root).as_posix(), "kind": kind})
    return result


def add_log_artifacts(run_root: Path, workdir: Path, artifacts: list[dict[str, Any]], names: tuple[str, ...]) -> None:
    existing = [workdir / name for name in names if (workdir / name).is_file() or (workdir / name).is_symlink()]
    artifacts.extend(relative_artifacts(run_root, existing, "wps-log"))


def run_geogrid(args: argparse.Namespace, specification: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if args.geog_root is None or args.geog_root.is_symlink() or not args.geog_root.is_dir():
        raise WpsStepError("WPS_GEOGRAPHY_MISSING", "A readable real WPS geography directory is required.")
    args.workdir.mkdir(parents=True, exist_ok=True)
    namelist = copy_namelist(args.specification, args.workdir)
    text = replace_assignment(namelist.read_text(encoding="utf-8"), "geog_data_path", f"'{args.geog_root}',")
    namelist.write_text(text, encoding="utf-8")
    table = args.workdir / "GEOGRID.TBL"
    if table.is_symlink():
        table.unlink()
    shutil.copyfile(WPS_ASSETS / "run" / "GEOGRID.TBL.ARW", table)
    remove_previous(list(args.workdir.glob("geo_em.d*.nc")))
    atomic_json(args.progress, {"phase": "geogrid", "domain_grid_created": False})
    run_checked([str(WPS_DIR / "geogrid.exe")], args.workdir, "geogrid-executor.log")
    outputs = list(args.workdir.glob("geo_em.d*.nc"))
    if not outputs:
        raise WpsStepError("PROCESS_CRASH", "geogrid.exe produced no geo_em files.")
    artifacts = relative_artifacts(args.run_root, outputs, "wps-geographical-grid")
    add_log_artifacts(args.run_root, args.workdir, artifacts, ("geogrid.log", "geogrid-executor.log"))
    return artifacts, {"phase": "completed", "domain_grid_created": True, "domains": len(outputs)}


def run_ungrib(args: argparse.Namespace, specification: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    identity = specification["identity"]
    files = identity.get("era5_input", {}).get("files")
    if not isinstance(files, list) or not files:
        raise WpsStepError("INPUT_DATA_MISSING", "Immutable ERA5 file list is empty.")
    args.workdir.mkdir(parents=True, exist_ok=True)
    namelist = copy_namelist(args.specification, args.workdir)
    link_force(VTABLE, args.workdir / "Vtable")
    prefixes: list[str] = []
    produced: list[Path] = []
    for index, metadata in enumerate(files, start=1):
        if not isinstance(metadata, dict):
            raise WpsStepError("INPUT_DATA_MISSING", "ERA5 file metadata is invalid.")
        target = safe_child(args.era5_root, metadata.get("path"))
        prefix = prefix_for(metadata)
        if prefix not in prefixes:
            prefixes.append(prefix)
        remove_previous(list(args.workdir.glob("GRIBFILE.*")))
        remove_previous(list(args.workdir.glob(f"{prefix}:*")))
        link_force(target, args.workdir / "GRIBFILE.AAA")
        text = replace_assignment(namelist.read_text(encoding="utf-8"), "prefix", f"'{prefix}',")
        namelist.write_text(text, encoding="utf-8")
        atomic_json(
            args.progress,
            {
                "phase": "ungrib",
                "decoded_requests": index - 1,
                "total_requests": len(files),
                "current_prefix": prefix,
            },
        )
        run_checked([str(WPS_DIR / "ungrib.exe")], args.workdir, "ungrib-executor.log")
        current = list(args.workdir.glob(f"{prefix}:*"))
        if not current:
            raise WpsStepError("PROCESS_CRASH", f"ungrib.exe produced no {prefix} files.")
        produced.extend(current)
    artifacts = relative_artifacts(args.run_root, produced, "wps-intermediate-file")
    add_log_artifacts(args.run_root, args.workdir, artifacts, ("ungrib.log", "ungrib-executor.log"))
    return artifacts, {
        "phase": "completed",
        "decoded_requests": len(files),
        "total_requests": len(files),
        "decoded_time_points": len(produced),
        "prefixes": prefixes,
    }


def run_metgrid(args: argparse.Namespace, specification: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    identity = specification["identity"]
    files = identity.get("era5_input", {}).get("files")
    if not isinstance(files, list) or not files:
        raise WpsStepError("INPUT_DATA_MISSING", "Immutable ERA5 file list is empty.")
    prefixes = []
    for metadata in files:
        if not isinstance(metadata, dict):
            raise WpsStepError("INPUT_DATA_MISSING", "ERA5 file metadata is invalid.")
        prefix = prefix_for(metadata)
        if prefix not in prefixes:
            prefixes.append(prefix)
    geo_outputs = list(args.workdir.glob("geo_em.d*.nc"))
    if not geo_outputs:
        raise WpsStepError("WPS_GEOGRAPHY_MISSING", "metgrid requires geo_em output from geogrid.")
    relative_artifacts(args.run_root, geo_outputs, "wps-geographical-grid")
    for prefix in prefixes:
        intermediate = list(args.workdir.glob(f"{prefix}:*"))
        if not intermediate:
            raise WpsStepError("INPUT_DATA_MISSING", f"metgrid requires {prefix} ungrib output.")
        relative_artifacts(args.run_root, intermediate, "wps-intermediate-file")
    namelist = copy_namelist(args.specification, args.workdir)
    text = replace_assignment(
        namelist.read_text(encoding="utf-8"),
        "fg_name",
        ",".join(f"'{prefix}'" for prefix in prefixes) + ",",
    )
    namelist.write_text(text, encoding="utf-8")
    table_directory = args.workdir / "metgrid"
    if table_directory.is_symlink():
        raise WpsStepError("EXECUTOR_OUTPUT_INVALID", "metgrid table directory is a symbolic link.")
    table_directory.mkdir(parents=True, exist_ok=True)
    table = table_directory / "METGRID.TBL"
    if table.is_symlink():
        table.unlink()
    shutil.copyfile(WPS_ASSETS / "run" / "METGRID.TBL.ARW", table)
    remove_previous(list(args.workdir.glob("met_em.d*.nc")))
    atomic_json(args.progress, {"phase": "metgrid", "met_em_time_points": 0})
    run_checked([str(WPS_DIR / "metgrid.exe")], args.workdir, "metgrid-executor.log")
    outputs = list(args.workdir.glob("met_em.d*.nc"))
    if not outputs:
        raise WpsStepError("PROCESS_CRASH", "metgrid.exe produced no met_em files.")
    artifacts = relative_artifacts(args.run_root, outputs, "wps-metgrid-input")
    add_log_artifacts(args.run_root, args.workdir, artifacts, ("metgrid.log", "metgrid-executor.log"))
    return artifacts, {"phase": "completed", "met_em_time_points": len(outputs), "prefixes": prefixes}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    try:
        specification = load_specification(args.specification)
        raw_run_root = _absolute(args.run_root)
        raw_workdir = _absolute(args.workdir)
        reject_symlink_components(
            raw_run_root,
            raw_workdir,
            "EXECUTOR_OUTPUT_INVALID",
            "WPS work directory contains a symbolic link.",
        )
        args.run_root = raw_run_root.resolve()
        args.workdir = raw_workdir.resolve()
        if args.run_root not in args.workdir.parents:
            raise WpsStepError("EXECUTOR_OUTPUT_INVALID", "WPS work directory escaped run root.")
        runners = {"geogrid": run_geogrid, "ungrib": run_ungrib, "metgrid": run_metgrid}
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
    except WpsStepError as exc:
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
                    "message": f"WPS step runner failed ({type(exc).__name__}).",
                },
                "resources": {"wall_seconds": time.monotonic() - started},
            },
        )
        print(f"WPS step runner failed ({type(exc).__name__}).", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
