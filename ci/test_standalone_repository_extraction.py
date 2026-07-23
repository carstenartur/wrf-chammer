#!/usr/bin/env python3
"""Network-free tests for the standalone product extraction contract."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "migration" / "standalone-product.json"
VERIFIER = REPO_ROOT / "ci" / "verify-standalone-product-extraction.py"
EXPORTER = REPO_ROOT / "migration" / "export-standalone-product.py"


def run(*command: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Command failed ({completed.returncode}): {command!r}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def load_exporter_module():
    specification = importlib.util.spec_from_file_location(
        "wrf_chammer_standalone_exporter", EXPORTER
    )
    if specification is None or specification.loader is None:
        raise AssertionError("Cannot load standalone exporter module")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["source_repository"] == "carstenartur/wrf-chammer"
    assert manifest["suggested_repository"].endswith("wrf-chammer-workbench")
    assert "README.md" in manifest["required_export_paths"]
    assert "THIRD_PARTY_NOTICES/WRF-LICENSE.txt" in manifest["required_export_paths"]
    assert "runtime/source-baseline.json" in manifest["required_export_paths"]
    assert "phys" in manifest["forbidden_export_roots"]
    assert "workbench/" in manifest["include_prefixes"]
    assert "migration/standalone-root/" in manifest["path_renames"]
    assert manifest["path_renames"]["LICENSE.txt"] == "THIRD_PARTY_NOTICES/WRF-LICENSE.txt"
    assert (
        manifest["path_renames"]["migration/runtime-source-baseline.json"]
        == "runtime/source-baseline.json"
    )
    assert ".github/workflows/docker-build.yml" in manifest["remove_after_export"]
    assert (
        ".github/workflows/standalone-repository-extraction-tests.yml"
        in manifest["remove_after_export"]
    )

    source = run(
        sys.executable,
        str(VERIFIER),
        "--source-root",
        str(REPO_ROOT),
        "--manifest",
        str(MANIFEST),
        "--json",
    )
    source_report = json.loads(source.stdout)
    assert source_report["mode"] == "source"
    assert len(source_report["source_revision"]) == 40
    assert source_report["minimum_source_revision"] == manifest["minimum_source_revision"]
    assert source_report["known_runtime_couplings"]
    assert any(
        item["path"] == "workbench/cli.py" and item["present"] == "true"
        for item in source_report["known_runtime_couplings"]
    )

    with tempfile.TemporaryDirectory(prefix="wrf-chammer-export-plan-") as temporary:
        destination = Path(temporary) / "standalone"
        planned = run(
            sys.executable,
            str(EXPORTER),
            str(destination),
            "--source",
            str(REPO_ROOT),
            "--manifest",
            str(MANIFEST),
            "--plan",
        )
    plan = json.loads(planned.stdout)
    assert plan["destination"] == str(destination.resolve())
    assert plan["source_revision"] == source_report["source_revision"]
    assert plan["minimum_source_revision"] == manifest["minimum_source_revision"]
    command = plan["filter_repo_command"]
    assert "--force" in command
    assert "workbench/" in command
    assert "migration/standalone-root/:" in command
    assert "LICENSE.txt:THIRD_PARTY_NOTICES/WRF-LICENSE.txt" in command
    assert "migration/runtime-source-baseline.json:runtime/source-baseline.json" in command
    assert ".github/workflows/docker-build.yml" in plan["remove_after_export"]
    assert "ci/verify-standalone-product-extraction.py" in plan["remove_after_export"]
    assert plan["push"] is False

    exporter = load_exporter_module()
    safe_destination = REPO_ROOT.parent / "wrf-chammer-workbench-safe-test"
    exporter._assert_safe_destination(REPO_ROOT, safe_destination)
    for unsafe in (REPO_ROOT, REPO_ROOT / "child", REPO_ROOT.parent):
        try:
            exporter._assert_safe_destination(REPO_ROOT, unsafe)
        except exporter.ExportError:
            pass
        else:
            raise AssertionError(f"Unsafe export destination was accepted: {unsafe}")

    readme = REPO_ROOT / "migration" / "standalone-root" / "README.md"
    readme_text = readme.read_text(encoding="utf-8")
    assert readme_text.startswith("# WRF Chammer Workbench")
    assert "not the official WRF project" in readme_text
    assert "real Xaver" in readme_text
    assert "THIRD_PARTY_NOTICES/WRF-LICENSE.txt" in readme_text
    assert "No product license is inferred" in readme_text

    print("Standalone repository extraction contract tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
