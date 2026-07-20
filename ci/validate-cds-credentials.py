#!/usr/bin/env python3
"""Validate local CDS credentials with a tiny real ERA5 request.

The result file contains only classified, non-secret information. Raw provider
output and exceptions are intentionally written neither to the result nor to a
persistent log.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATASET = "reanalysis-era5-single-levels"
REQUEST = {
    "product_type": "reanalysis",
    "format": "grib",
    "variable": ["2m_temperature"],
    "year": ["2013"],
    "month": ["12"],
    "day": ["05"],
    "time": ["12:00"],
    "area": [52.0, 7.0, 51.75, 7.25],
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate local Copernicus CDS credentials with a tiny ERA5 request."
    )
    parser.add_argument("--result", required=True, type=Path)
    return parser.parse_args(argv)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_provider_error(exc: BaseException) -> tuple[str, str, str]:
    text = str(exc).lower()
    if any(token in text for token in ("401", "403", "unauthor", "forbidden", "invalid key", "api key", "token")):
        return (
            "INVALID",
            "invalid_credentials",
            "The Copernicus CDS rejected the configured credentials.",
        )
    if any(token in text for token in ("licence", "license", "terms", "agreement")):
        return (
            "INVALID",
            "terms_not_accepted",
            "The CDS account must accept the dataset terms before ERA5 data can be requested.",
        )
    if any(token in text for token in ("timeout", "timed out", "temporarily unavailable", "503", "502", "504")):
        return (
            "FAILED",
            "service_unavailable",
            "The CDS validation request could not complete because the service was unavailable.",
        )
    return (
        "FAILED",
        "validation_request_failed",
        "The CDS validation request failed without proving that the credentials are invalid.",
    )


def result_payload(
    *,
    status: str,
    code: str,
    summary: str,
    started: float,
    size_bytes: int = 0,
    sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "status": status,
        "code": code,
        "summary": summary,
        "checked_at": utc_now(),
        "duration_seconds": round(max(0.0, time.monotonic() - started), 3),
        "request": {
            "dataset": DATASET,
            "variable": "2m_temperature",
            "date": "2013-12-05",
            "time": "12:00 UTC",
            "area": REQUEST["area"],
        },
        "response": {
            "size_bytes": size_bytes,
            "sha256": sha256,
            "retained": False,
        },
        "artificial_weather_data": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    with open(os.devnull, "w", encoding="utf-8") as sink:
        try:
            with redirect_stdout(sink), redirect_stderr(sink):
                import cdsapi
        except ModuleNotFoundError:
            atomic_json(
                args.result,
                result_payload(
                    status="FAILED",
                    code="cdsapi_missing",
                    summary="The Python cdsapi package is required to validate CDS credentials.",
                    started=started,
                ),
            )
            return 1

        try:
            with redirect_stdout(sink), redirect_stderr(sink):
                client = cdsapi.Client(quiet=True, debug=False)
                with tempfile.TemporaryDirectory(prefix="wrf-cds-validation-") as temporary:
                    target = Path(temporary) / "credential-test.grib"
                    client.retrieve(DATASET, REQUEST, str(target))
                    if not target.is_file() or target.stat().st_size <= 0:
                        raise RuntimeError("empty validation response")
                    size_bytes = target.stat().st_size
                    digest = sha256_file(target)
            atomic_json(
                args.result,
                result_payload(
                    status="VALID",
                    code="credentials_valid",
                    summary="The configured credentials completed a minimal real ERA5 request.",
                    started=started,
                    size_bytes=size_bytes,
                    sha256=digest,
                ),
            )
            return 0
        except BaseException as exc:
            status, code, summary = classify_provider_error(exc)
            atomic_json(
                args.result,
                result_payload(
                    status=status,
                    code=code,
                    summary=summary,
                    started=started,
                ),
            )
            return 2 if status == "INVALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
