#!/usr/bin/env python3
"""Verify dependency-based trigger contracts for expensive WRF/WPS workflows."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

COMMON_EXCLUDED = (
    "README.md",
    "doc/USER_GUIDE.md",
    "migration/export-standalone-product.py",
    "migration/standalone-product.json",
    "runtime/release-manifest.schema.json",
    "runtime/release-manifest.example.json",
    "workbench/runtime_image_service.py",
    "workbench/simulation_run_manifest.py",
    "workbench/server/tests/test_runtime_image_service.py",
    "workbench/server/tests/test_simulation_reproduction.py",
    "workbench/web/simulation-job-queue.js",
    "visualization/viewer/app.js",
    "ci/verify-heavy-workflow-triggers.py",
    ".github/workflows/simulation-api-tests.yml",
    ".github/workflows/runtime-image-delivery-tests.yml",
    ".github/workflows/heavy-workflow-trigger-policy-tests.yml",
    ".github/workflows/standalone-repository-extraction-tests.yml",
    "Dockerfile.backend",
    "Dockerfile.postproc",
    "Dockerfile.postprocessing",
)

CONTRACTS = {
    ".github/workflows/docker-build.yml": {
        "included": (
            "phys/module_physics_init.F",
            "CMakeLists.txt",
            "Dockerfile",
            "workbench/run.sh",
            "workbench/validate.py",
            "workbench/config/schema.json",
            "workbench/examples/wrf-smoke.json",
            "workbench/scripts/run-wrf-smoke.sh",
            "ci/build-wrf.sh",
            "ci/find-netcdff-library.sh",
            "ci/_run_wrf_step_core.py",
            "ci/run-wrf-step.py",
            "ci/verify-wrf-runtime.sh",
            "ci/smoke-test-wrf.sh",
            ".github/workflows/docker-build.yml",
        ),
        "excluded": COMMON_EXCLUDED
        + ("Dockerfile.wps", "Dockerfile.era5", "wrf-chammer", "ci/build-wps.sh"),
    },
    ".github/workflows/docker-wps-build.yml": {
        "included": (
            "phys/module_physics_init.F",
            "configure",
            "Dockerfile.wps",
            "ci/build-wps.sh",
            "ci/verify-wps-runtime.sh",
            ".github/workflows/docker-wps-build.yml",
        ),
        "excluded": COMMON_EXCLUDED
        + (
            "Dockerfile",
            "Dockerfile.era5",
            "wrf-chammer",
            "workbench/validate.py",
            "ci/build-wrf.sh",
        ),
    },
    ".github/workflows/wps-integration-test.yml": {
        "included": (
            "phys/module_physics_init.F",
            "configure",
            "Dockerfile.wps",
            "Dockerfile.era5",
            "tests/era5-mini/pressure.grib",
            "tests/era5-mini/wps/namelist.wps",
            "ci/build-wps.sh",
            "ci/verify-wps-runtime.sh",
            "ci/download-era5.py",
            "ci/download-era5.sh",
            "ci/prepare-era5-wps.py",
            "ci/prepare-era5-wps.sh",
            "ci/run-era5-pipeline.sh",
            "ci/run-wps-step.py",
            "ci/verify-era5-outputs.sh",
            "ci/test-era5-wps-integration.sh",
            ".github/workflows/wps-integration-test.yml",
        ),
        "excluded": COMMON_EXCLUDED
        + ("Dockerfile", "wrf-chammer", "workbench/validate.py", "ci/build-wrf.sh"),
    },
}


def _trigger_block(lines: list[str], trigger: str) -> list[str]:
    marker = f"  {trigger}:"
    try:
        start = lines.index(marker) + 1
    except ValueError as exc:
        raise AssertionError(f"Missing {trigger} trigger") from exc
    for index in range(start, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.strip():
            return lines[start:index]
    return lines[start:]


def _parse_path_scalar(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise AssertionError("Empty path pattern")
    if value[0] in {'"', "'"}:
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, str):
            raise AssertionError(f"Non-string path pattern: {parsed!r}")
        return parsed
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def _paths_from_block(block: list[str], trigger: str) -> list[str]:
    if any(line.strip() == "paths-ignore:" for line in block):
        raise AssertionError(f"{trigger} still uses paths-ignore")
    try:
        start = next(i for i, line in enumerate(block) if line.strip() == "paths:") + 1
    except StopIteration as exc:
        raise AssertionError(f"Missing paths list for {trigger}") from exc
    patterns: list[str] = []
    for line in block[start:]:
        if not line.strip():
            continue
        if len(line) - len(line.lstrip(" ")) <= 4:
            break
        stripped = line.strip()
        if not stripped.startswith("- "):
            raise AssertionError(f"Unexpected {trigger} paths line: {line!r}")
        patterns.append(_parse_path_scalar(stripped[2:]))
    if not patterns:
        raise AssertionError(f"Empty paths list for {trigger}")
    return patterns


def _glob_regex(pattern: str) -> re.Pattern[str]:
    output = ["^"]
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            output.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            output.append(".*")
            index += 2
        elif pattern[index] == "*":
            output.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            output.append("[^/]")
            index += 1
        else:
            output.append(re.escape(pattern[index]))
            index += 1
    output.append("$")
    return re.compile("".join(output))


def _workflow_runs(patterns: Iterable[str], path: str) -> bool:
    selected = False
    for pattern in patterns:
        negative = pattern.startswith("!")
        candidate = pattern[1:] if negative else pattern
        if _glob_regex(candidate).fullmatch(path):
            selected = not negative
    return selected


def _verify_workflow(relative_path: str, contract: dict[str, tuple[str, ...]]) -> None:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    lines = text.splitlines()
    pull = _paths_from_block(_trigger_block(lines, "pull_request"), "pull_request")
    push = _paths_from_block(_trigger_block(lines, "push"), "push")
    if pull != push:
        raise AssertionError(f"{relative_path}: pull and push path contracts differ")
    if pull[0] != "**":
        raise AssertionError(f"{relative_path}: missing inclusive baseline")
    for required in (
        "!doc/**",
        "!migration/**",
        "!runtime/**",
        "!workbench/**",
        "!visualization/**",
        "!ci/**",
        "!.github/workflows/**",
        "!Dockerfile*",
    ):
        if required not in pull:
            raise AssertionError(f"{relative_path}: missing stable exclusion {required}")
    if "cancel-in-progress: true" not in text:
        raise AssertionError(f"{relative_path}: obsolete runs are not cancelled")
    failures = [
        *(f"must trigger: {path}" for path in contract["included"] if not _workflow_runs(pull, path)),
        *(f"must not trigger: {path}" for path in contract["excluded"] if _workflow_runs(pull, path)),
    ]
    if failures:
        raise AssertionError(f"{relative_path}: " + "; ".join(failures))


def main() -> int:
    for relative_path, contract in CONTRACTS.items():
        _verify_workflow(relative_path, contract)
        print(f"verified {relative_path}")
    print("Heavy workflow trigger policy passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
