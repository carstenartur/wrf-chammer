#!/usr/bin/env python3
"""Security and provenance tests for the ERA5 downloader cache writer."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOWNLOADER = REPO_ROOT / "ci" / "download-era5.py"


def run_downloader(config: dict, root: Path, *, expected_success: bool) -> subprocess.CompletedProcess[str]:
    config_path = root / "config.json"
    output_dir = root / "output"
    manifest = root / "manifest.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.pop("CDSAPI_KEY", None)
    env.pop("CDSAPI_URL", None)
    result = subprocess.run(
        [
            sys.executable,
            str(DOWNLOADER),
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--manifest",
            str(manifest),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if expected_success and result.returncode != 0:
        raise AssertionError(f"Downloader unexpectedly failed: {result.stdout}\n{result.stderr}")
    if not expected_success and result.returncode == 0:
        raise AssertionError("Downloader unexpectedly accepted an unsafe configuration")
    return result


def request(target: str) -> dict:
    return {
        "dataset": "reanalysis-era5-single-levels",
        "target": target,
        "ungrib_prefix": "SFC",
        "request": {
            "product_type": "reanalysis",
            "format": "grib",
            "variable": ["2m_temperature"],
            "year": ["2013"],
            "month": ["12"],
            "day": ["05"],
            "time": ["12:00"],
            "area": [58, 2, 51, 14],
        },
    }


def test_checksum_manifest() -> None:
    with tempfile.TemporaryDirectory(prefix="wrf-era5-downloader-") as temp:
        root = Path(temp)
        target = root / "output" / "files" / "cached.grib"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = b"cached-real-era5-grib-placeholder-for-cache-test"
        target.write_bytes(payload)

        run_downloader({"requests": {"single": request("files/cached.grib")}}, root, expected_success=True)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        entry = manifest["outputs"][0]
        assert entry["cached"] is True
        assert entry["size_bytes"] == len(payload)
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
        assert len(entry["request_sha256"]) == 64
        assert Path(entry["target"]) == target.resolve()


def test_parent_traversal_is_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="wrf-era5-downloader-") as temp:
        root = Path(temp)
        result = run_downloader({"requests": {"escape": request("../escape.grib")}}, root, expected_success=False)
        assert "inside the output directory" in result.stderr
        assert not (root / "escape.grib").exists()


def test_absolute_target_is_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="wrf-era5-downloader-") as temp:
        root = Path(temp)
        absolute = root / "outside.grib"
        result = run_downloader({"requests": {"absolute": request(str(absolute))}}, root, expected_success=False)
        assert "inside the output directory" in result.stderr
        assert not absolute.exists()


def test_symlink_escape_is_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="wrf-era5-downloader-") as temp:
        root = Path(temp)
        output = root / "output"
        output.mkdir(parents=True, exist_ok=True)
        outside = root / "outside.grib"
        outside.write_bytes(b"outside")
        (output / "linked.grib").symlink_to(outside)

        result = run_downloader({"requests": {"linked": request("linked.grib")}}, root, expected_success=False)
        assert "escapes the output directory" in result.stderr
        assert outside.read_bytes() == b"outside"


def test_duplicate_targets_are_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="wrf-era5-downloader-") as temp:
        root = Path(temp)
        target = root / "output" / "same.grib"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"cached")
        result = run_downloader(
            {"requests": {"first": request("same.grib"), "second": request("same.grib")}},
            root,
            expected_success=False,
        )
        assert "same file" in result.stderr


def main() -> int:
    test_checksum_manifest()
    test_parent_traversal_is_rejected()
    test_absolute_target_is_rejected()
    test_symlink_escape_is_rejected()
    test_duplicate_targets_are_rejected()
    print("ERA5 downloader security tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
