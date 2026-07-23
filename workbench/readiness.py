#!/usr/bin/env python3
"""Runtime readiness checks for the local WRF Workbench.

The checks deliberately use only the Python standard library so they can run
before any optional Workbench dependencies or WRF/WPS runtime images exist.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from workbench.runtime_image_service import RuntimeImageError, load_activation

GIB = 1024**3


def _result(
    check_id: str,
    status: str,
    summary: str,
    remediation: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": check_id, "status": status, "summary": summary}
    if remediation:
        payload["remediation"] = remediation
    if details:
        payload["details"] = details
    return payload


def _memory_bytes() -> int | None:
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1]) * 1024
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if isinstance(pages, int) and isinstance(page_size, int):
            return pages * page_size
    except (AttributeError, OSError, ValueError):
        pass
    return None


def _run(
    command: list[str], timeout: float = 8.0
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _docker_image_check(
    reference: str, *, component: str, identity: str | None = None
) -> dict[str, Any]:
    completed = _run(["docker", "image", "inspect", reference], timeout=8)
    if completed and completed.returncode == 0:
        try:
            value = json.loads(completed.stdout)
            image = value[0]
        except (json.JSONDecodeError, IndexError, TypeError):
            image = None
        if isinstance(image, dict):
            image_id = image.get("Id")
            repo_digests = image.get("RepoDigests")
            matches = identity is None or image_id == identity or (
                isinstance(repo_digests, list)
                and any(
                    isinstance(value, str) and value.endswith(f"@{identity}")
                    for value in repo_digests
                )
            )
            if matches:
                return _result(
                    f"image-{component}",
                    "ready",
                    f"Digest-pinned {component} runtime is available.",
                    details={
                        "component": component,
                        "reference": reference,
                        "identity": identity,
                        "image_id": image_id,
                    },
                )
            return _result(
                f"image-{component}",
                "error",
                f"Local {component} runtime does not match the activated digest.",
                "Run 'wrf-chammer images pull' with the installed release manifest.",
                {
                    "component": component,
                    "reference": reference,
                    "expected_identity": identity,
                    "image_id": image_id,
                },
            )
    return _result(
        f"image-{component}",
        "warning",
        f"Runtime image {reference} is not available locally.",
        "Run 'wrf-chammer images pull' with a published release manifest.",
        {"component": component, "reference": reference, "identity": identity},
    )


def collect_readiness(repo_root: Path, include_images: bool = True) -> dict[str, Any]:
    """Return structured readiness information for CLI and API consumers."""

    root = repo_root.resolve()
    checks: list[dict[str, Any]] = []

    python_ok = sys.version_info >= (3, 10)
    checks.append(
        _result(
            "python",
            "ready" if python_ok else "error",
            f"Python {platform.python_version()} is active.",
            None if python_ok else "Install Python 3.10 or newer.",
            {"version": platform.python_version(), "executable": sys.executable},
        )
    )

    cpu_count = os.cpu_count() or 1
    checks.append(
        _result(
            "cpu",
            "ready" if cpu_count >= 2 else "warning",
            f"{cpu_count} CPU core(s) detected.",
            None
            if cpu_count >= 2
            else "Real simulations will be very slow with fewer than two CPU cores.",
            {"logical_cores": cpu_count},
        )
    )

    memory = _memory_bytes()
    if memory is None:
        checks.append(_result("memory", "warning", "Total RAM could not be determined."))
    else:
        memory_gib = round(memory / GIB, 2)
        status = (
            "ready"
            if memory >= 8 * GIB
            else "warning"
            if memory >= 2 * GIB
            else "error"
        )
        checks.append(
            _result(
                "memory",
                status,
                f"{memory_gib} GiB RAM detected.",
                None
                if status == "ready"
                else "Use a smaller domain/profile or run on a machine with at least 8 GiB RAM.",
                {"bytes": memory, "gib": memory_gib},
            )
        )

    disk = shutil.disk_usage(root)
    free_gib = round(disk.free / GIB, 2)
    disk_status = (
        "ready"
        if disk.free >= 20 * GIB
        else "warning"
        if disk.free >= 5 * GIB
        else "error"
    )
    checks.append(
        _result(
            "disk",
            disk_status,
            f"{free_gib} GiB free in the repository filesystem.",
            None
            if disk_status == "ready"
            else "Free disk space or choose a smaller simulation before downloading ERA5 data.",
            {"free_bytes": disk.free, "free_gib": free_gib, "path": str(root)},
        )
    )

    runs_dir = root / "workbench-runs"
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="readiness-", dir=runs_dir, delete=True):
            pass
        checks.append(
            _result(
                "workspace",
                "ready",
                "Workbench run directory is writable.",
                details={"path": str(runs_dir)},
            )
        )
    except OSError as exc:
        checks.append(
            _result(
                "workspace",
                "error",
                "Workbench run directory is not writable.",
                "Fix ownership or permissions for workbench-runs/.",
                {"path": str(runs_dir), "error": str(exc)},
            )
        )

    docker_path = shutil.which("docker")
    docker_ready = False
    if not docker_path:
        checks.append(
            _result(
                "docker",
                "error",
                "Docker CLI is not installed.",
                "Install Docker Engine or Docker Desktop to run WPS/WRF containers.",
            )
        )
    else:
        docker_info = _run(
            [docker_path, "info", "--format", "{{.ServerVersion}}"], timeout=8
        )
        if docker_info and docker_info.returncode == 0:
            docker_ready = True
            checks.append(
                _result(
                    "docker",
                    "ready",
                    f"Docker daemon {docker_info.stdout.strip() or 'available'} is reachable.",
                    details={
                        "executable": docker_path,
                        "server_version": docker_info.stdout.strip(),
                    },
                )
            )
        else:
            error = ""
            if docker_info:
                error = (docker_info.stderr or docker_info.stdout).strip()
            checks.append(
                _result(
                    "docker",
                    "error",
                    "Docker CLI is installed but the daemon is not reachable.",
                    "Start Docker and ensure the current user may access the daemon.",
                    {"executable": docker_path, "error": error[:500]},
                )
            )

    activation = None
    activation_error = None
    try:
        activation = load_activation(root, required=False)
    except RuntimeImageError as exc:
        activation_error = exc
    if activation_error:
        checks.append(
            _result(
                "runtime-release",
                "error",
                "The active runtime release record is invalid.",
                "Pull the installed release manifest again.",
                {"code": activation_error.code, "message": activation_error.message},
            )
        )
    elif activation:
        checks.append(
            _result(
                "runtime-release",
                "ready",
                f"Runtime release {activation.get('release')} is activated.",
                details={
                    "release": activation.get("release"),
                    "manifest_sha256": activation.get("manifest_sha256"),
                    "product_source_revision": activation.get(
                        "product_source_revision"
                    ),
                },
            )
        )
    else:
        checks.append(
            _result(
                "runtime-release",
                "warning",
                "No digest-pinned runtime release is activated.",
                "Run 'wrf-chammer images pull --manifest <release-manifest.json>'.",
            )
        )

    if include_images and docker_ready:
        if activation:
            for component in ("wps", "wrf", "postprocessing"):
                entry = activation["images"][component]
                checks.append(
                    _docker_image_check(
                        entry["reference"],
                        component=component,
                        identity=entry["identity"],
                    )
                )
        else:
            checks.append(
                _result(
                    "runtime-images",
                    "warning",
                    "Runtime images cannot be checked without an activated release.",
                    "Pull a digest-pinned release manifest first.",
                )
            )

    cds_configured = bool(os.environ.get("CDSAPI_KEY")) or (
        Path.home() / ".cdsapirc"
    ).is_file()
    checks.append(
        _result(
            "era5-credentials",
            "ready" if cds_configured else "warning",
            "ERA5/CDS credentials are configured."
            if cds_configured
            else "ERA5/CDS credentials were not found.",
            None
            if cds_configured
            else "Create ~/.cdsapirc or configure CDSAPI_KEY before downloading real ERA5 data.",
        )
    )

    counts = {
        status: sum(1 for check in checks if check["status"] == status)
        for status in ("ready", "warning", "error")
    }
    overall = "error" if counts["error"] else "warning" if counts["warning"] else "ready"
    return {
        "ok": counts["error"] == 0,
        "status": overall,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": counts,
        "checks": checks,
    }
