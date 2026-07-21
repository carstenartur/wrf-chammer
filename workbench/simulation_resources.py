#!/usr/bin/env python3
"""Resource admission for immutable persistent simulation jobs."""

from __future__ import annotations

import math
import os
import shutil
from pathlib import Path
from typing import Any

_GIB = 1024**3


def _finite_nonnegative(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _memory_available_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) >= 2:
                    return max(0, int(parts[1]) * 1024)
    except (OSError, ValueError):
        pass
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        return max(0, page_size * available_pages)
    except (AttributeError, OSError, ValueError):
        return None


def collect_host_resources(repo_root: Path) -> dict[str, Any]:
    """Collect only local admission signals; no host paths are returned."""

    run_root = (repo_root.resolve() / "workbench-runs")
    run_root.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(run_root)
    return {
        "memory_available_bytes": _memory_available_bytes(),
        "disk_free_bytes": int(disk.free),
    }


def extract_resource_estimate(specification: dict[str, Any]) -> dict[str, Any]:
    identity = specification.get("identity") if isinstance(specification, dict) else None
    job = identity.get("job") if isinstance(identity, dict) else None
    metadata = job.get("metadata") if isinstance(job, dict) else None
    estimate = metadata.get("resource_estimate") if isinstance(metadata, dict) else None
    if not isinstance(estimate, dict):
        return {
            "available": False,
            "minimum_memory_bytes": None,
            "recommended_memory_bytes": None,
            "working_disk_bytes": None,
        }

    ram = estimate.get("estimated_ram_gb")
    storage = estimate.get("estimated_storage_gb")
    minimum_gb = (
        _finite_nonnegative(ram.get("minimum")) if isinstance(ram, dict) else None
    )
    recommended_gb = (
        _finite_nonnegative(ram.get("recommended"))
        if isinstance(ram, dict)
        else None
    )
    working_gb = (
        _finite_nonnegative(storage.get("working_total"))
        if isinstance(storage, dict)
        else None
    )
    available = minimum_gb is not None or working_gb is not None
    return {
        "available": available,
        "minimum_memory_bytes": (
            int(math.ceil(minimum_gb * _GIB)) if minimum_gb is not None else None
        ),
        "recommended_memory_bytes": (
            int(math.ceil(recommended_gb * _GIB))
            if recommended_gb is not None
            else None
        ),
        "working_disk_bytes": (
            int(math.ceil(working_gb * _GIB)) if working_gb is not None else None
        ),
    }


def evaluate_resource_admission(
    specification: dict[str, Any],
    host: dict[str, Any],
    *,
    memory_headroom_fraction: float = 0.15,
    disk_headroom_fraction: float = 0.10,
) -> dict[str, Any]:
    """Return a serializable preflight decision from frozen estimates and host state."""

    estimate = extract_resource_estimate(specification)
    memory_available = host.get("memory_available_bytes")
    disk_free = host.get("disk_free_bytes")
    if not isinstance(memory_available, int) or memory_available < 0:
        memory_available = None
    if not isinstance(disk_free, int) or disk_free < 0:
        disk_free = None

    required_memory = estimate["minimum_memory_bytes"]
    required_disk = estimate["working_disk_bytes"]
    memory_budget = (
        int(memory_available * max(0.0, 1.0 - memory_headroom_fraction))
        if memory_available is not None
        else None
    )
    disk_budget = (
        int(disk_free * max(0.0, 1.0 - disk_headroom_fraction))
        if disk_free is not None
        else None
    )
    reasons: list[dict[str, Any]] = []
    if (
        required_memory is not None
        and memory_budget is not None
        and required_memory > memory_budget
    ):
        reasons.append(
            {
                "resource": "memory",
                "required_bytes": required_memory,
                "available_after_headroom_bytes": memory_budget,
            }
        )
    if required_disk is not None and disk_budget is not None and required_disk > disk_budget:
        reasons.append(
            {
                "resource": "disk",
                "required_bytes": required_disk,
                "available_after_headroom_bytes": disk_budget,
            }
        )

    return {
        "admitted": not reasons,
        "estimate_available": bool(estimate["available"]),
        "estimate": estimate,
        "host": {
            "memory_available_bytes": memory_available,
            "disk_free_bytes": disk_free,
        },
        "headroom": {
            "memory_fraction": memory_headroom_fraction,
            "disk_fraction": disk_headroom_fraction,
        },
        "reasons": reasons,
    }


__all__ = [
    "collect_host_resources",
    "evaluate_resource_admission",
    "extract_resource_estimate",
]
