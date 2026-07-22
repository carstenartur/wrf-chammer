#!/usr/bin/env python3
"""Verify that the checked-in planning-map screenshot is an attributed OSM capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_SCREENSHOT = "xaver-03b-map-domain-wizard.png"
_EXPECTED_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_EXPECTED_HOST = "tile.openstreetmap.org"


class ProvenanceError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProvenanceError(f"{label} must be a non-empty string")
    return value.strip()


def verify(provenance_path: Path) -> dict[str, Any]:
    if provenance_path.is_symlink() or not provenance_path.is_file():
        raise ProvenanceError(f"provenance file is missing or unsafe: {provenance_path}")
    try:
        payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceError("provenance is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ProvenanceError("unsupported screenshot provenance schema")

    screenshot_name = require_string(payload.get("screenshot"), "screenshot")
    if screenshot_name != _EXPECTED_SCREENSHOT or Path(screenshot_name).name != screenshot_name:
        raise ProvenanceError("provenance references an unexpected screenshot")
    screenshot_path = provenance_path.parent / screenshot_name
    if screenshot_path.is_symlink() or not screenshot_path.is_file():
        raise ProvenanceError("referenced screenshot is missing or unsafe")

    expected_digest = require_string(payload.get("screenshot_sha256"), "screenshot_sha256")
    if not _SHA256_RE.fullmatch(expected_digest):
        raise ProvenanceError("screenshot SHA-256 is malformed")
    actual_digest = sha256_file(screenshot_path)
    if actual_digest != expected_digest:
        raise ProvenanceError("screenshot bytes do not match their provenance SHA-256")

    if payload.get("basemap") != "openstreetmap-standard":
        raise ProvenanceError("checked-in planning screenshot is not marked as OpenStreetMap")
    if payload.get("tile_url_template") != _EXPECTED_TEMPLATE:
        raise ProvenanceError("OpenStreetMap tile URL template is not canonical")
    if payload.get("tile_host") != _EXPECTED_HOST:
        raise ProvenanceError("OpenStreetMap tile host is not canonical")
    tile_count = payload.get("successful_tile_responses")
    if isinstance(tile_count, bool) or not isinstance(tile_count, int) or tile_count < 4:
        raise ProvenanceError("too few successful visible OpenStreetMap tiles were recorded")
    attribution = require_string(payload.get("attribution"), "attribution")
    if "OpenStreetMap contributors" not in attribution:
        raise ProvenanceError("OpenStreetMap contributor attribution is missing")
    generated_at = require_string(payload.get("generated_at"), "generated_at")
    try:
        datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProvenanceError("generated_at is not an ISO-8601 timestamp") from exc
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "provenance",
        nargs="?",
        type=Path,
        default=Path(
            "doc/user-guide/screenshots/"
            "xaver-03b-map-domain-wizard.png.provenance.json"
        ),
    )
    args = parser.parse_args()
    payload = verify(args.provenance)
    print(
        "Verified OpenStreetMap screenshot provenance: "
        f"{payload['successful_tile_responses']} visible tiles, "
        f"SHA-256 {payload['screenshot_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
