#!/usr/bin/env python3
"""Public WRF step runner with bounded progress-log scanning."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

import _run_wrf_step_core as _core  # noqa: E402
from _run_wrf_step_core import *  # noqa: F401,F403,E402

_PROGRESS_TAIL_BYTES = 64 * 1024


def _read_text_tail(path: Path, maximum_bytes: int = _PROGRESS_TAIL_BYTES) -> str:
    """Read only the bounded tail needed for the latest WRF timing record."""

    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - maximum_bytes))
        data = handle.read(maximum_bytes)
    return data.decode("utf-8", errors="replace")


def publish_wrf_progress(
    progress_path: Path,
    workdir: Path,
    start: datetime,
    end: datetime,
    wall_started: float,
) -> None:
    """Publish structured progress without rescanning an unbounded rsl log."""

    rsl = workdir / "rsl.out.0000"
    current: datetime | None = None
    if rsl.is_file() and not rsl.is_symlink():
        try:
            matches = _core._TIMING_RE.findall(_read_text_tail(rsl))
            if matches:
                current = datetime.strptime(
                    matches[-1], "%Y-%m-%d_%H:%M:%S"
                ).replace(tzinfo=_core.timezone.utc)
        except (OSError, ValueError):
            current = None
    total = max(1.0, (end - start).total_seconds())
    simulated = (
        0.0
        if current is None
        else min(total, max(0.0, (current - start).total_seconds()))
    )
    elapsed = max(0.0, time.monotonic() - wall_started)
    fraction = simulated / total
    eta = None if fraction <= 0 else max(0.0, elapsed * (1.0 - fraction) / fraction)
    _core.atomic_json(
        progress_path,
        {
            "phase": "wrf",
            "simulation_time": (
                current.isoformat().replace("+00:00", "Z") if current else None
            ),
            "simulated_seconds": simulated,
            "total_seconds": total,
            "fraction": fraction,
            "output_files": len(list(workdir.glob("wrfout_d*"))),
            "eta_seconds": eta,
        },
    )


_core.publish_wrf_progress = publish_wrf_progress


def main(argv: list[str] | None = None) -> int:
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
