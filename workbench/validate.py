#!/usr/bin/env python3
"""Validate a WRF Workbench job configuration file (JSON format).

Usage:
    python3 validate.py <config-file.json>

Exits with status 0 on success, non-zero on failure.
"""

import json
import os
import re
import sys


VALID_MODES = {"dry-run", "wrf-smoke", "era5-offline", "era5-download-only"}
VALID_SOURCES = {"era5", "none"}
_ISO8601_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
_ID_RE = re.compile(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$')
_ERA5_CONFIG_RE = re.compile(r'^(ci/era5|workbench/era5)/[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$')


def validate_config(config, config_path=None):
    """Return a list of error strings; empty list means the config is valid.

    config_path — absolute path to the config file being validated; retained for
                  compatibility with existing callers.
    """
    errors = []

    if not isinstance(config, dict):
        return ["Config root must be a JSON object"]

    # Required top-level fields
    required_fields = ("id", "mode", "name", "period", "domain", "inputs", "outputs")
    missing = [f for f in required_fields if f not in config]
    if missing:
        for f in missing:
            errors.append(f"Missing required field: '{f}'")
        # Return early — subsequent checks depend on these fields being present
        return errors

    # id
    job_id = config["id"]
    if not isinstance(job_id, str) or not job_id.strip():
        errors.append("'id' must be a non-empty string")
    elif not _ID_RE.match(job_id):
        errors.append(
            f"'id' must start and end with a lowercase letter or digit "
            f"and contain only [a-z0-9-], got: {job_id!r}"
        )

    # mode
    if config["mode"] not in VALID_MODES:
        errors.append(
            f"'mode' must be one of: {', '.join(sorted(VALID_MODES))}, "
            f"got: {config['mode']!r}"
        )

    # name
    if not isinstance(config["name"], str) or not config["name"].strip():
        errors.append("'name' must be a non-empty string")

    # period
    period = config["period"]
    if not isinstance(period, dict):
        errors.append("'period' must be an object with 'start' and 'end' fields")
    else:
        for sub in ("start", "end"):
            if sub not in period:
                errors.append(f"'period.{sub}' is required")
            elif not _ISO8601_RE.match(str(period[sub])):
                errors.append(
                    f"'period.{sub}' must be ISO 8601 (YYYY-MM-DDTHH:MM:SSZ), "
                    f"got: {period[sub]!r}"
                )
        if "start" in period and "end" in period:
            if str(period["start"]) >= str(period["end"]):
                errors.append("'period.start' must be before 'period.end'")

    # domain
    domain = config["domain"]
    if not isinstance(domain, dict):
        errors.append("'domain' must be an object")
    else:
        required_domain = ("label", "center_lat", "center_lon", "dx_km", "dy_km", "e_we", "e_sn")
        for sub in required_domain:
            if sub not in domain:
                errors.append(f"'domain.{sub}' is required")

        if "center_lat" in domain:
            lat = domain["center_lat"]
            if not isinstance(lat, (int, float)) or not -90 <= lat <= 90:
                errors.append(
                    f"'domain.center_lat' must be a number in [-90, 90], got: {lat!r}"
                )

        if "center_lon" in domain:
            lon = domain["center_lon"]
            if not isinstance(lon, (int, float)) or not -180 <= lon <= 180:
                errors.append(
                    f"'domain.center_lon' must be a number in [-180, 180], got: {lon!r}"
                )

        for sub in ("dx_km", "dy_km"):
            if sub in domain:
                val = domain[sub]
                if not isinstance(val, (int, float)) or val <= 0:
                    errors.append(f"'domain.{sub}' must be a positive number, got: {val!r}")

        for sub in ("e_we", "e_sn"):
            if sub in domain:
                val = domain[sub]
                if not isinstance(val, int) or val < 3:
                    errors.append(
                        f"'domain.{sub}' must be an integer >= 3, got: {val!r}"
                    )

    # inputs
    inputs = config["inputs"]
    if not isinstance(inputs, dict):
        errors.append("'inputs' must be an object")
    else:
        if "source" not in inputs:
            errors.append("'inputs.source' is required")
        elif inputs["source"] not in VALID_SOURCES:
            errors.append(
                f"'inputs.source' must be one of: {', '.join(sorted(VALID_SOURCES))}, "
                f"got: {inputs['source']!r}"
            )

        # era5-download-only requires inputs.era5.config in a known project path.
        if config.get("mode") == "era5-download-only":
            era5 = inputs.get("era5")
            if not isinstance(era5, dict) or not era5.get("config", "").strip():
                errors.append(
                    "'inputs.era5.config' is required for mode 'era5-download-only'"
                )
            else:
                era5_config_path = era5["config"].strip()
                if not _ERA5_CONFIG_RE.match(era5_config_path):
                    errors.append(
                        "'inputs.era5.config' must reference a supported ERA5 config path"
                    )

    # outputs
    outputs = config["outputs"]
    if not isinstance(outputs, dict):
        errors.append("'outputs' must be an object")
    else:
        if "directory" not in outputs:
            errors.append("'outputs.directory' is required")
        elif not isinstance(outputs["directory"], str) or not outputs["directory"].strip():
            errors.append("'outputs.directory' must be a non-empty string")

    return errors


def load_config(path):
    """Load and parse a JSON config file.  Raises SystemExit on I/O or parse errors."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        raise SystemExit(f"Config file not found: {path}")
    except OSError as exc:
        raise SystemExit(f"Cannot read config file '{path}': {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in '{path}': {exc}") from exc


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <config-file.json>", file=sys.stderr)
        sys.exit(1)

    config = load_config(sys.argv[1])
    errors = validate_config(config, config_path=os.path.abspath(sys.argv[1]))

    if errors:
        print(
            f"Config validation failed with {len(errors)} error(s):",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    print(f"Config valid: id={config['id']}  mode={config['mode']}")


if __name__ == "__main__":
    main()
