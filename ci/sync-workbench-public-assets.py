#!/usr/bin/env python3
"""Synchronize canonical Workbench custom-element sources into Vite public assets."""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "workbench" / "web"
PUBLIC_ROOT = REPO_ROOT / "workbench" / "ui" / "public"
ASSETS = (
    "era5-credential-validation.js",
    "era5-download-control.js",
    "era5-cache-management.js",
    "real-pipeline-specification.js",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy canonical Workbench browser controls into the Vite public directory."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail instead of writing when a Vite public asset differs from its canonical source.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mismatches: list[str] = []
    PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)

    for filename in ASSETS:
        source = WEB_ROOT / filename
        target = PUBLIC_ROOT / filename
        if not source.is_file():
            mismatches.append(f"missing canonical browser asset: {source.relative_to(REPO_ROOT)}")
            continue
        source_content = source.read_text(encoding="utf-8")
        target_content = target.read_text(encoding="utf-8") if target.is_file() else None
        if target_content == source_content:
            continue
        if args.check:
            mismatches.append(
                f"out-of-sync Vite public asset: {target.relative_to(REPO_ROOT)}"
            )
        else:
            target.write_text(source_content, encoding="utf-8")
            print(f"Updated {target.relative_to(REPO_ROOT)}")

    if mismatches:
        for mismatch in mismatches:
            print(mismatch)
        print("Run: python3 ci/sync-workbench-public-assets.py")
        return 1
    if args.check:
        print("Workbench public assets match their canonical web sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
