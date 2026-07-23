#!/usr/bin/env python3
"""Create a history-preserving standalone WRF Chammer Workbench repository."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class ExportError(RuntimeError):
    pass


def _run(
    root: Path,
    command: list[str],
    *,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if completed.returncode != 0:
        detail = ""
        if capture:
            detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise ExportError(
            f"Command failed ({completed.returncode}): {' '.join(command)}{suffix}"
        )
    return completed


def _git(root: Path, *args: str, capture: bool = True) -> str:
    return _run(root, ["git", *args], capture=capture).stdout.strip() if capture else ""


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"Cannot read extraction manifest: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ExportError("Unsupported extraction manifest")
    return payload


def _filter_repo_arguments(manifest: dict[str, Any]) -> list[str]:
    arguments = ["--force"]
    for value in manifest.get("include_prefixes", []):
        arguments.extend(["--path", str(value)])
    for value in manifest.get("include_files", []):
        arguments.extend(["--path", str(value)])
    for source, destination in manifest.get("path_renames", {}).items():
        arguments.extend(["--path-rename", f"{source}:{destination}"])
    return arguments


def _filter_repo_command(manifest: dict[str, Any], *, require_installed: bool) -> list[str]:
    arguments = _filter_repo_arguments(manifest)
    executable = shutil.which("git-filter-repo")
    if executable:
        return [executable, *arguments]
    if not require_installed:
        return ["git-filter-repo", *arguments]
    probe = subprocess.run(
        ["git", "filter-repo", "--help"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        raise ExportError(
            "git-filter-repo is required. Install it with your package manager or pipx."
        )
    return ["git", "filter-repo", *arguments]


def _resolved_revision(source: Path, requested: str | None) -> str:
    revision = requested or "HEAD"
    return _git(source, "rev-parse", "--verify", f"{revision}^{{commit}}")


def _migration_commit(root: Path, message: str) -> None:
    _run(
        root,
        [
            "git",
            "-c",
            "user.name=WRF Chammer Migration",
            "-c",
            "user.email=wrf-chammer-migration@users.noreply.github.com",
            "commit",
            "-m",
            message,
        ],
    )


def _remove_fork_only_paths(root: Path, manifest: dict[str, Any]) -> list[str]:
    requested = [str(value) for value in manifest.get("remove_after_export", [])]
    existing = [value for value in requested if (root / value).exists()]
    if not existing:
        return []
    _run(root, ["git", "rm", "-r", "--ignore-unmatch", "--", *existing])
    _migration_commit(root, "Remove fork-only build and migration workflows")
    return existing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export the Workbench product paths into a standalone Git repository"
    )
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--target-url")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-known-couplings", action="store_true")
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Validate and print the exact export plan without cloning or rewriting history",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.source.resolve()
    destination = args.destination.resolve()
    manifest_path = (
        args.manifest.resolve()
        if args.manifest
        else source / "migration" / "standalone-product.json"
    )

    try:
        manifest = _load_manifest(manifest_path)
        source_revision = _resolved_revision(source, args.revision)
        verifier = source / "ci" / "verify-standalone-product-extraction.py"
        _run(
            source,
            [
                sys.executable,
                str(verifier),
                "--source-root",
                str(source),
                "--manifest",
                str(manifest_path),
            ],
            capture=True,
        )

        plan = {
            "source": str(source),
            "source_revision": source_revision,
            "destination": str(destination),
            "suggested_repository": manifest.get("suggested_repository"),
            "filter_repo_command": _filter_repo_command(
                manifest, require_installed=False
            ),
            "remove_after_export": manifest.get("remove_after_export", []),
            "target_url": args.target_url,
            "push": args.push,
        }
        if args.plan:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0

        if destination.exists():
            if not args.force:
                raise ExportError(
                    f"Destination already exists: {destination}. Use --force to replace it."
                )
            if destination == source or source in destination.parents:
                raise ExportError(
                    "Refusing to delete the source checkout or a directory inside it"
                )
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        _run(
            source,
            [
                "git",
                "clone",
                "--no-local",
                "--no-hardlinks",
                str(source),
                str(destination),
            ],
        )
        _run(
            destination,
            ["git", "checkout", "--force", "-B", "standalone-export", source_revision],
        )
        _run(
            destination,
            _filter_repo_command(manifest, require_installed=True),
        )
        _run(destination, ["git", "branch", "-M", "main"])
        removed_paths = _remove_fork_only_paths(destination, manifest)

        verification_command = [
            sys.executable,
            str(verifier),
            "--source-root",
            str(source),
            "--manifest",
            str(manifest_path),
            "--export-root",
            str(destination),
            "--json",
        ]
        if args.allow_known_couplings:
            verification_command.append("--allow-known-couplings")
        completed = _run(destination, verification_command, capture=True)
        verification = json.loads(completed.stdout)

        report_path = destination / "migration-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report.update(
            {
                "source_repository": manifest.get("source_repository"),
                "source_revision_before_filter": source_revision,
                "suggested_repository": manifest.get("suggested_repository"),
                "export_revision": _git(destination, "rev-parse", "HEAD"),
                "fork_only_paths_removed": removed_paths,
            }
        )
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _run(destination, ["git", "add", "migration-report.json"])
        _migration_commit(destination, "Record standalone repository migration provenance")

        if args.target_url:
            current = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=destination,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if current.returncode == 0:
                _run(destination, ["git", "remote", "remove", "origin"])
            _run(destination, ["git", "remote", "add", "origin", args.target_url])
        if args.push:
            if not args.target_url:
                raise ExportError("--push requires --target-url")
            if args.allow_known_couplings:
                raise ExportError(
                    "Refusing to push an export with known full-fork runtime couplings"
                )
            _run(destination, ["git", "push", "--set-upstream", "origin", "main"])

        print(json.dumps(verification, indent=2, sort_keys=True))
        print(f"Standalone repository exported to {destination}")
        if report.get("known_runtime_couplings"):
            print(
                "Export created as a staging repository. Resolve the runtime couplings in "
                "migration-report.json before pushing it as the canonical product repository."
            )
        return 0
    except (ExportError, json.JSONDecodeError) as exc:
        print(f"standalone repository export failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
