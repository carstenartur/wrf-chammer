#!/usr/bin/env python3
"""Execute one real WRF initialization or simulation step in a pinned image."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WRF_RUN = Path("/opt/wrf/run")
_TIMING_RE = re.compile(
    r"Timing for main:\s+time\s+(\d{4}-\d{2}-\d{2}_\d{2}:\d{2}:\d{2})\s+on domain"
)


class WrfStepError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one real WRF pipeline step")
    parser.add_argument("--step", choices=("real", "wrf"), required=True)
    parser.add_argument("--specification", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
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


def reject_symlink_chain(root: Path, child: Path, code: str, message: str) -> None:
    raw_root = absolute(root)
    raw_child = absolute(child)
    try:
        relative = raw_child.relative_to(raw_root)
    except ValueError as exc:
        raise WrfStepError(code, message) from exc
    current = raw_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise WrfStepError(code, message)


def load_specification(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise WrfStepError("NAMELIST_INVALID", "Immutable specification is unavailable.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WrfStepError("NAMELIST_INVALID", "Immutable specification JSON is invalid.") from exc
    identity = value.get("identity") if isinstance(value, dict) else None
    if (
        not isinstance(identity, dict)
        or value.get("immutable") is not True
        or value.get("execution_started") is not False
    ):
        raise WrfStepError("NAMELIST_INVALID", "Immutable specification identity is invalid.")
    return value


def parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise WrfStepError("NAMELIST_INVALID", f"{field} is not a UTC timestamp.")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise WrfStepError("NAMELIST_INVALID", f"{field} is not a UTC timestamp.") from exc


def copy_namelist(specification_path: Path, workdir: Path) -> Path:
    source = specification_path.parent / "namelist.input"
    if source.is_symlink() or not source.is_file():
        raise WrfStepError("NAMELIST_INVALID", "Frozen namelist.input is missing.")
    target = workdir / "namelist.input"
    if target.is_symlink():
        raise WrfStepError("NAMELIST_INVALID", "WRF work namelist is a symbolic link.")
    if target.exists() and not target.is_file():
        raise WrfStepError("NAMELIST_INVALID", "WRF work namelist is not a regular file.")
    shutil.copyfile(source, target)
    return target


def link_runtime_assets(workdir: Path) -> None:
    if not WRF_RUN.is_dir():
        raise WrfStepError("RUNTIME_IMAGE_MISMATCH", "WRF runtime directory is unavailable.")
    for source in WRF_RUN.iterdir():
        if source.name in {"real.exe", "wrf.exe", "namelist.input"}:
            continue
        target = workdir / source.name
        if target.exists() or target.is_symlink():
            continue
        target.symlink_to(source, target_is_directory=source.is_dir())


def remove_previous(paths: list[Path]) -> None:
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            raise WrfStepError("EXECUTOR_OUTPUT_INVALID", "Previous WRF output is not a regular file.")


def link_or_copy(source: Path, target: Path, run_root: Path) -> None:
    reject_symlink_chain(
        run_root,
        source,
        "INPUT_DATA_MISSING",
        "WRF input contains a symbolic link.",
    )
    resolved_run = run_root.resolve()
    resolved_source = source.resolve()
    if resolved_run not in resolved_source.parents or not resolved_source.is_file():
        raise WrfStepError("INPUT_DATA_MISSING", "WRF input escaped the managed run directory.")
    if target.is_symlink() or target.exists():
        if target.is_file() or target.is_symlink():
            target.unlink()
        else:
            raise WrfStepError("EXECUTOR_OUTPUT_INVALID", "WRF input target is not a regular file.")
    try:
        os.link(resolved_source, target)
    except OSError:
        shutil.copyfile(resolved_source, target)


def collect_artifacts(run_root: Path, paths: list[Path], kind: str) -> list[dict[str, Any]]:
    resolved_root = run_root.resolve()
    artifacts: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for original in paths:
        raw = absolute(original)
        reject_symlink_chain(
            run_root,
            raw,
            "EXECUTOR_OUTPUT_INVALID",
            "WRF produced a symbolic-link artifact.",
        )
        resolved = raw.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved_root not in resolved.parents or not resolved.is_file():
            raise WrfStepError("EXECUTOR_OUTPUT_INVALID", "WRF produced an unsafe artifact.")
        artifacts.append({"path": resolved.relative_to(resolved_root).as_posix(), "kind": kind})
    return artifacts


def classify_failure(workdir: Path, executable: str, return_code: int) -> WrfStepError:
    text = ""
    for name in ("rsl.error.0000", "rsl.out.0000", f"{executable}-executor.log"):
        path = workdir / name
        if path.is_file() and not path.is_symlink():
            try:
                text += "\n" + path.read_text(encoding="utf-8", errors="replace")[-16000:]
            except OSError:
                pass
    lowered = text.lower()
    if "cfl" in lowered or "w_critical_cfl" in lowered:
        return WrfStepError(
            "WRF_NUMERICAL_INSTABILITY",
            "WRF reported a CFL or numerical stability failure.",
        )
    if "namelist" in lowered or "error while reading" in lowered:
        return WrfStepError("NAMELIST_INVALID", "WRF rejected the frozen namelist.")
    if "met_em" in lowered or "wrfinput" in lowered or "wrfbdy" in lowered:
        return WrfStepError("INPUT_DATA_MISSING", "WRF required input files are missing or invalid.")
    if "cannot allocate memory" in lowered or "out of memory" in lowered or return_code in {137, -9}:
        return WrfStepError("INSUFFICIENT_MEMORY", "WRF terminated because memory was unavailable.")
    if "no space left on device" in lowered:
        return WrfStepError("DISK_FULL", "WRF terminated because the run filesystem is full.")
    if executable == "real" and ("domain" in lowered or "mismatch" in lowered):
        return WrfStepError("DOMAIN_CONFIGURATION_INVALID", "real.exe rejected the domain configuration.")
    return WrfStepError(
        "PROCESS_CRASH", f"{executable}.exe exited with code {return_code}."
    )


def publish_wrf_progress(
    progress_path: Path,
    workdir: Path,
    start: datetime,
    end: datetime,
    wall_started: float,
) -> None:
    rsl = workdir / "rsl.out.0000"
    current: datetime | None = None
    if rsl.is_file() and not rsl.is_symlink():
        try:
            matches = _TIMING_RE.findall(rsl.read_text(encoding="utf-8", errors="replace"))
            if matches:
                current = datetime.strptime(matches[-1], "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc)
        except (OSError, ValueError):
            current = None
    total = max(1.0, (end - start).total_seconds())
    simulated = 0.0 if current is None else min(total, max(0.0, (current - start).total_seconds()))
    elapsed = max(0.0, time.monotonic() - wall_started)
    fraction = simulated / total
    eta = None if fraction <= 0 else max(0.0, elapsed * (1.0 - fraction) / fraction)
    atomic_json(
        progress_path,
        {
            "phase": "wrf",
            "simulation_time": current.isoformat().replace("+00:00", "Z") if current else None,
            "simulated_seconds": simulated,
            "total_seconds": total,
            "fraction": fraction,
            "output_files": len(list(workdir.glob("wrfout_d*"))),
            "eta_seconds": eta,
        },
    )


def run_process(
    executable: str,
    workdir: Path,
    progress_path: Path,
    specification: dict[str, Any],
    poll_seconds: float,
) -> None:
    log_path = workdir / f"{executable}-executor.log"
    if log_path.is_symlink():
        raise WrfStepError("EXECUTOR_OUTPUT_INVALID", "WRF executor log is a symbolic link.")
    start_wall = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(WRF_RUN / f"{executable}.exe")],
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if executable == "wrf":
            period = specification["identity"].get("job", {}).get("period", {})
            start = parse_utc(period.get("start"), "period.start")
            end = parse_utc(period.get("end"), "period.end")
            while process.poll() is None:
                publish_wrf_progress(progress_path, workdir, start, end, start_wall)
                time.sleep(max(0.1, poll_seconds))
        return_code = process.wait()
    if return_code != 0:
        raise classify_failure(workdir, executable, return_code)


def run_real(args: argparse.Namespace, specification: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    args.workdir.mkdir(parents=True, exist_ok=True)
    copy_namelist(args.specification, args.workdir)
    link_runtime_assets(args.workdir)
    wps_work = args.run_root / "work" / "wps"
    met_em = sorted(wps_work.glob("met_em.d*.nc"))
    if not met_em:
        raise WrfStepError("INPUT_DATA_MISSING", "real.exe requires met_em output from metgrid.")
    remove_previous(list(args.workdir.glob("met_em.d*.nc")))
    remove_previous(list(args.workdir.glob("wrfinput_d*")) + list(args.workdir.glob("wrfbdy_d*")))
    remove_previous(list(args.workdir.glob("rsl.*")))
    for source in met_em:
        link_or_copy(source, args.workdir / source.name, args.run_root)
    atomic_json(args.progress, {"phase": "real", "initialization_started": True})
    run_process("real", args.workdir, args.progress, specification, args.poll_seconds)
    outputs = list(args.workdir.glob("wrfinput_d*")) + list(args.workdir.glob("wrfbdy_d*"))
    if not any(path.name.startswith("wrfinput_d") for path in outputs) or not any(
        path.name.startswith("wrfbdy_d") for path in outputs
    ):
        raise WrfStepError("PROCESS_CRASH", "real.exe produced no wrfinput/wrfbdy pair.")
    artifacts = collect_artifacts(args.run_root, outputs, "wrf-initial-condition")
    logs = list(args.workdir.glob("rsl.*")) + [args.workdir / "real-executor.log"]
    artifacts.extend(collect_artifacts(args.run_root, logs, "wrf-log"))
    return artifacts, {
        "phase": "completed",
        "initialization_started": True,
        "wrfinput_files": len(list(args.workdir.glob("wrfinput_d*"))),
        "wrfbdy_files": len(list(args.workdir.glob("wrfbdy_d*"))),
    }


def run_wrf(args: argparse.Namespace, specification: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    args.workdir.mkdir(parents=True, exist_ok=True)
    copy_namelist(args.specification, args.workdir)
    link_runtime_assets(args.workdir)
    real_work = args.run_root / "work" / "wrf" / "real"
    inputs = list(real_work.glob("wrfinput_d*")) + list(real_work.glob("wrfbdy_d*"))
    if not any(path.name.startswith("wrfinput_d") for path in inputs) or not any(
        path.name.startswith("wrfbdy_d") for path in inputs
    ):
        raise WrfStepError("INPUT_DATA_MISSING", "wrf.exe requires real.exe output.")
    remove_previous(list(args.workdir.glob("wrfinput_d*")) + list(args.workdir.glob("wrfbdy_d*")))
    remove_previous(list(args.workdir.glob("wrfout_d*")) + list(args.workdir.glob("rsl.*")))
    for source in inputs:
        link_or_copy(source, args.workdir / source.name, args.run_root)
    atomic_json(args.progress, {"phase": "wrf", "simulation_time": None, "output_files": 0})
    run_process("wrf", args.workdir, args.progress, specification, args.poll_seconds)
    period = specification["identity"].get("job", {}).get("period", {})
    start = parse_utc(period.get("start"), "period.start")
    end = parse_utc(period.get("end"), "period.end")
    publish_wrf_progress(args.progress, args.workdir, start, end, time.monotonic())
    outputs = list(args.workdir.glob("wrfout_d*"))
    if not outputs:
        raise WrfStepError("PROCESS_CRASH", "wrf.exe produced no wrfout files.")
    artifacts = collect_artifacts(args.run_root, outputs, "wrf-model-output")
    logs = list(args.workdir.glob("rsl.*")) + [args.workdir / "wrf-executor.log"]
    artifacts.extend(collect_artifacts(args.run_root, logs, "wrf-log"))
    return artifacts, {
        "phase": "completed",
        "simulation_time": end.isoformat().replace("+00:00", "Z"),
        "simulated_seconds": (end - start).total_seconds(),
        "total_seconds": (end - start).total_seconds(),
        "fraction": 1.0,
        "output_files": len(outputs),
        "eta_seconds": 0.0,
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
            "EXECUTOR_OUTPUT_INVALID",
            "WRF work directory contains a symbolic link.",
        )
        args.run_root = raw_run.resolve()
        args.workdir = raw_work.resolve()
        if args.run_root not in args.workdir.parents:
            raise WrfStepError("EXECUTOR_OUTPUT_INVALID", "WRF work directory escaped run root.")
        runners = {"real": run_real, "wrf": run_wrf}
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
    except WrfStepError as exc:
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
                    "message": f"WRF step runner failed ({type(exc).__name__}).",
                },
                "resources": {"wall_seconds": time.monotonic() - started},
            },
        )
        print(f"WRF step runner failed ({type(exc).__name__}).", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
