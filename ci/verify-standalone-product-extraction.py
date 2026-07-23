#!/usr/bin/env python3
"""Verify the history-preserving standalone Workbench repository boundary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class VerificationError(RuntimeError):
    pass


def _run(root: Path, *command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"Cannot read extraction manifest: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise VerificationError("Unsupported standalone extraction manifest")
    return payload


def _git_revision(root: Path, revision: str = "HEAD") -> str:
    completed = _run(root, "git", "rev-parse", "--verify", f"{revision}^{{commit}}")
    if completed.returncode != 0:
        raise VerificationError(
            f"Cannot resolve Git revision {revision!r}: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _verify_source(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    git_dir = _run(root, "git", "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        raise VerificationError(f"Not a Git checkout: {root}")

    head = _git_revision(root)
    baseline = str(manifest.get("minimum_source_revision") or "")
    if len(baseline) != 40:
        raise VerificationError("minimum_source_revision must be a full Git SHA")
    ancestor = _run(root, "git", "merge-base", "--is-ancestor", baseline, head)
    if ancestor.returncode != 0:
        raise VerificationError(
            f"Current source {head} does not descend from migration baseline {baseline}"
        )

    missing: list[str] = []
    for value in manifest.get("include_prefixes", []):
        path = root / str(value).rstrip("/")
        if not path.is_dir():
            missing.append(str(value))
    for value in manifest.get("include_files", []):
        path = root / str(value)
        if not path.is_file():
            missing.append(str(value))
    if missing:
        raise VerificationError("Extraction manifest references missing paths: " + ", ".join(missing))

    known_couplings: list[dict[str, str]] = []
    for entry in manifest.get("known_runtime_couplings", []):
        if not isinstance(entry, dict):
            raise VerificationError("known_runtime_couplings entries must be objects")
        relative = str(entry.get("path") or "")
        needle = str(entry.get("needle") or "")
        reason = str(entry.get("reason") or "")
        target = root / relative
        text = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        present = bool(needle and needle in text)
        known_couplings.append(
            {"path": relative, "reason": reason, "present": str(present).lower()}
        )

    return {
        "mode": "source",
        "source_revision": head,
        "minimum_source_revision": baseline,
        "known_runtime_couplings": known_couplings,
    }


def _verify_export(
    root: Path,
    manifest: dict[str, Any],
    *,
    allow_known_couplings: bool,
) -> dict[str, Any]:
    if not root.is_dir():
        raise VerificationError(f"Export root does not exist: {root}")

    allowed = {str(value) for value in manifest.get("allowed_export_roots", [])}
    actual = {entry.name for entry in root.iterdir() if entry.name != ".git"}
    unexpected = sorted(actual - allowed)
    if unexpected:
        raise VerificationError("Unexpected export roots: " + ", ".join(unexpected))

    missing = [
        str(value)
        for value in manifest.get("required_export_paths", [])
        if not (root / str(value)).exists()
    ]
    if missing:
        raise VerificationError("Required exported paths are missing: " + ", ".join(missing))

    forbidden = [
        str(value)
        for value in manifest.get("forbidden_export_roots", [])
        if (root / str(value)).exists()
    ]
    if forbidden:
        raise VerificationError("WRF core roots leaked into product export: " + ", ".join(forbidden))

    coupling_findings: list[dict[str, str]] = []
    for entry in manifest.get("known_runtime_couplings", []):
        relative = str(entry.get("path") or "")
        needle = str(entry.get("needle") or "")
        target = root / relative
        if target.is_file() and needle and needle in target.read_text(
            encoding="utf-8", errors="replace"
        ):
            coupling_findings.append(
                {"path": relative, "reason": str(entry.get("reason") or "")}
            )

    tracked = _run(root, "git", "ls-files")
    if tracked.returncode != 0:
        raise VerificationError("Cannot enumerate exported Git files")
    tracked_files = [line for line in tracked.stdout.splitlines() if line]
    if not tracked_files:
        raise VerificationError("Export contains no tracked files")

    report = {
        "mode": "export",
        "source_revision": _git_revision(root),
        "tracked_file_count": len(tracked_files),
        "top_level_entries": sorted(actual),
        "forbidden_roots": forbidden,
        "known_runtime_couplings": coupling_findings,
        "ready_for_standalone_release": not coupling_findings,
    }
    (root / "migration-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if coupling_findings and not allow_known_couplings:
        details = "; ".join(
            f"{finding['path']}: {finding['reason']}" for finding in coupling_findings
        )
        raise VerificationError("Known full-fork runtime couplings remain: " + details)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify source or exported standalone Workbench repository boundaries"
    )
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--export-root", type=Path)
    parser.add_argument("--allow-known-couplings", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_root = args.source_root.resolve()
    manifest_path = (
        args.manifest.resolve()
        if args.manifest
        else source_root / "migration" / "standalone-product.json"
    )
    try:
        manifest = _load_manifest(manifest_path)
        if args.export_root:
            report = _verify_export(
                args.export_root.resolve(),
                manifest,
                allow_known_couplings=args.allow_known_couplings,
            )
        else:
            report = _verify_source(source_root, manifest)
    except VerificationError as exc:
        print(f"standalone extraction verification failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Standalone product extraction boundary verified")
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
