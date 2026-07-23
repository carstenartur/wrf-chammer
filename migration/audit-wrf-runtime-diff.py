#!/usr/bin/env python3
"""Audit non-product changes between an upstream WRF ref and the current fork ref."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class AuditError(RuntimeError):
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


def _resolve(root: Path, revision: str) -> str:
    completed = _run(root, "git", "rev-parse", "--verify", f"{revision}^{{commit}}")
    if completed.returncode != 0:
        raise AuditError(
            f"Cannot resolve {revision!r}: {(completed.stderr or completed.stdout).strip()}"
        )
    return completed.stdout.strip()


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"Cannot read extraction manifest: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise AuditError("Unsupported extraction manifest")
    return value


def _excluded_pathspecs(manifest: dict[str, Any]) -> list[str]:
    pathspecs: list[str] = []
    for prefix in manifest.get("include_prefixes", []):
        normalized = str(prefix).rstrip("/")
        pathspecs.append(f":(exclude){normalized}/**")
    for filename in manifest.get("include_files", []):
        pathspecs.append(f":(exclude){filename}")
    return pathspecs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List WRF-fork changes that remain after product paths are excluded"
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--upstream-ref", required=True)
    parser.add_argument("--fork-ref", default="HEAD")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repository.resolve()
    manifest_path = (
        args.manifest.resolve()
        if args.manifest
        else root / "migration" / "standalone-product.json"
    )
    try:
        manifest = _load_manifest(manifest_path)
        upstream = _resolve(root, args.upstream_ref)
        fork = _resolve(root, args.fork_ref)
        command = [
            "git",
            "diff",
            "--find-renames",
            "--name-status",
            f"{upstream}...{fork}",
            "--",
            ":(top)**",
            *_excluded_pathspecs(manifest),
        ]
        completed = _run(root, *command)
        if completed.returncode != 0:
            raise AuditError(
                "Cannot compare WRF refs: "
                + (completed.stderr or completed.stdout).strip()
            )
        changes: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            fields = line.split("\t")
            status = fields[0]
            paths = fields[1:]
            changes.append({"status": status, "paths": paths})
        report = {
            "schema_version": 1,
            "upstream_ref": args.upstream_ref,
            "upstream_revision": upstream,
            "fork_ref": args.fork_ref,
            "fork_revision": fork,
            "product_paths_excluded": True,
            "remaining_change_count": len(changes),
            "permanent_wrf_fork_may_be_required": bool(changes),
            "changes": changes,
            "interpretation": (
                "Every remaining change must be classified as scientific core change, "
                "portable build fix, upstream candidate, or removable fork residue."
            ),
        }
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        return 0
    except AuditError as exc:
        print(f"WRF runtime diff audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
