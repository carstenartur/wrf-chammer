#!/usr/bin/env python3
"""Shared event catalogue and preset logic for the WRF Workbench.

This module is intentionally dependency-free and acts as the single source of
truth for loading, validating, searching, resolving and converting event
catalogue data into Workbench job configurations.

The CLI, the future local API service and the browser UI should call this module
instead of reimplementing catalogue semantics in shell or JavaScript.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from workbench.validate import validate_config

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
VALID_WARNINGS = {"low", "medium", "high", "critical"}
REQUIRED_EVENTS = {"xaver", "kyrill", "custom-template"}


class CatalogueError(ValueError):
    """Raised when catalogue or preset data is invalid."""


class EventNotFoundError(CatalogueError):
    """Raised when an event id, name or alias cannot be resolved."""


class PresetNotFoundError(CatalogueError):
    """Raised when a referenced domain or resolution preset does not exist."""


def default_repo_root() -> Path:
    """Return the repository root inferred from this module location."""
    return Path(__file__).resolve().parents[2]


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from *path* with useful error messages."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogueError(f"Missing catalogue file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogueError(f"Expected JSON object in {path}")
    return data


def load_catalogue(repo_root: Path | None = None) -> dict[str, Any]:
    """Load event, domain and resolution preset catalogues.

    The returned object is a lightweight data bundle:

    ```python
    {
        "repo_root": Path(...),
        "events": {...},
        "domains": {...},
        "resolution_presets": {...},
        "raw": {...}
    }
    ```
    """
    root = Path(repo_root) if repo_root else default_repo_root()
    events_raw = load_json(root / "workbench" / "events" / "catalogue.json")
    domains_raw = load_json(root / "workbench" / "presets" / "domains.json")
    resolutions_raw = load_json(root / "workbench" / "presets" / "resolutions.json")

    events = events_raw.get("events")
    domains = domains_raw.get("domains")
    resolutions = resolutions_raw.get("resolution_presets")

    if not isinstance(events, dict) or not events:
        raise CatalogueError("catalogue.json must contain a non-empty events object")
    if not isinstance(domains, dict) or not domains:
        raise CatalogueError("domains.json must contain a non-empty domains object")
    if not isinstance(resolutions, dict) or not resolutions:
        raise CatalogueError("resolutions.json must contain a non-empty resolution_presets object")

    return {
        "repo_root": root,
        "events": events,
        "domains": domains,
        "resolution_presets": resolutions,
        "raw": {
            "events": events_raw,
            "domains": domains_raw,
            "resolution_presets": resolutions_raw,
        },
    }


def parse_utc(value: Any, label: str) -> _dt.datetime:
    """Parse strict UTC timestamps used by Workbench JSON files."""
    if not isinstance(value, str) or not ISO_RE.match(value):
        raise CatalogueError(f"{label} must be ISO UTC YYYY-MM-DDTHH:MM:SSZ, got {value!r}")
    return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CatalogueError(message)


def _require_fields(obj: dict[str, Any], fields: Iterable[str], label: str) -> None:
    for field in fields:
        _require(field in obj, f"{label} missing field {field!r}")


def validate_domain_presets(domains: dict[str, Any]) -> list[str]:
    """Return validation errors for reusable domain presets."""
    errors: list[str] = []
    for domain_id, domain in domains.items():
        label = f"Domain {domain_id!r}"
        try:
            _require(isinstance(domain, dict), f"{label} must be an object")
            _require_fields(
                domain,
                (
                    "id", "label", "center_lat", "center_lon", "dx_km", "dy_km",
                    "e_we", "e_sn", "bounds", "intended_use", "runtime_class", "warning_level",
                ),
                label,
            )
            _require(domain["id"] == domain_id, f"{label} key must match id {domain.get('id')!r}")
            _require(isinstance(domain["label"], str) and domain["label"].strip(), f"{label} label must be non-empty")
            _require(isinstance(domain["center_lat"], (int, float)) and -90 <= domain["center_lat"] <= 90, f"{label} invalid center_lat")
            _require(isinstance(domain["center_lon"], (int, float)) and -180 <= domain["center_lon"] <= 180, f"{label} invalid center_lon")
            _require(isinstance(domain["dx_km"], (int, float)) and domain["dx_km"] > 0, f"{label} invalid dx_km")
            _require(isinstance(domain["dy_km"], (int, float)) and domain["dy_km"] > 0, f"{label} invalid dy_km")
            _require(isinstance(domain["e_we"], int) and domain["e_we"] >= 3, f"{label} invalid e_we")
            _require(isinstance(domain["e_sn"], int) and domain["e_sn"] >= 3, f"{label} invalid e_sn")
            bounds = domain["bounds"]
            _require(isinstance(bounds, list) and len(bounds) == 4, f"{label} bounds must be [west,south,east,north]")
            west, south, east, north = bounds
            _require(isinstance(west, (int, float)) and isinstance(east, (int, float)) and -180 <= west < east <= 180, f"{label} invalid west/east bounds")
            _require(isinstance(south, (int, float)) and isinstance(north, (int, float)) and -90 <= south < north <= 90, f"{label} invalid south/north bounds")
            _require(domain["warning_level"] in VALID_WARNINGS, f"{label} invalid warning_level")
        except CatalogueError as exc:
            errors.append(str(exc))
    return errors


def validate_resolution_presets(resolutions: dict[str, Any]) -> list[str]:
    """Return validation errors for resolution/runtime presets."""
    errors: list[str] = []
    for preset_id, preset in resolutions.items():
        label = f"Resolution preset {preset_id!r}"
        try:
            _require(isinstance(preset, dict), f"{label} must be an object")
            _require_fields(
                preset,
                (
                    "id", "label", "description", "suggested_dx_km", "suggested_dy_km",
                    "suggested_max_grid_cells", "runtime_class", "warning_level",
                ),
                label,
            )
            _require(preset["id"] == preset_id, f"{label} key must match id {preset.get('id')!r}")
            _require(preset["suggested_dx_km"] > 0 and preset["suggested_dy_km"] > 0, f"{label} spacing must be positive")
            _require(isinstance(preset["suggested_max_grid_cells"], int) and preset["suggested_max_grid_cells"] > 0, f"{label} max cells must be positive")
            _require(preset["warning_level"] in VALID_WARNINGS, f"{label} invalid warning_level")
        except CatalogueError as exc:
            errors.append(str(exc))
    return errors


def validate_events(events: dict[str, Any], domains: dict[str, Any], resolutions: dict[str, Any]) -> list[str]:
    """Return validation errors for event definitions and cross references."""
    errors: list[str] = []

    missing_events = REQUIRED_EVENTS - set(events)
    if missing_events:
        errors.append(f"Missing required events: {sorted(missing_events)}")

    for event_id, event in events.items():
        label = f"Event {event_id!r}"
        try:
            _require(isinstance(event, dict), f"{label} must be an object")
            _require_fields(
                event,
                (
                    "id", "name", "aliases", "event_type", "description", "period",
                    "default_domain", "domains", "suggested_outputs",
                    "default_resolution_preset", "resolution_presets",
                ),
                label,
            )
            _require(event["id"] == event_id, f"{label} key must match id {event.get('id')!r}")
            _require(isinstance(event["name"], str) and event["name"].strip(), f"{label} name must be non-empty")
            _require(isinstance(event["description"], str) and event["description"].strip(), f"{label} description must be non-empty")
            _require(isinstance(event["event_type"], str) and event["event_type"].strip(), f"{label} event_type must be non-empty")

            aliases = event["aliases"]
            _require(isinstance(aliases, list) and aliases, f"{label} aliases must be a non-empty list")
            for alias in aliases:
                _require(isinstance(alias, str) and alias.strip(), f"{label} aliases must contain non-empty strings")

            period = event["period"]
            _require(isinstance(period, dict), f"{label} period must be an object")
            start = parse_utc(period.get("start"), f"{label} period.start")
            end = parse_utc(period.get("end"), f"{label} period.end")
            _require(start < end, f"{label} period.start must be before period.end")

            if "start" in event:
                _require(event["start"] == period["start"], f"{label} start must match period.start")
            if "end" in event:
                _require(event["end"] == period["end"], f"{label} end must match period.end")

            default_domain = event["default_domain"]
            _require(default_domain in domains, f"{label} default_domain {default_domain!r} is not defined")
            event_domains = event["domains"]
            _require(isinstance(event_domains, list) and event_domains, f"{label} domains must be non-empty")
            _require(default_domain in event_domains, f"{label} domains must include default_domain")
            for domain_id in event_domains:
                _require(domain_id in domains, f"{label} references unknown domain {domain_id!r}")

            if "domain" in event:
                inline = event["domain"]
                preset = domains[default_domain]
                for field in ("center_lat", "center_lon", "dx_km", "dy_km", "e_we", "e_sn"):
                    _require(inline.get(field) == preset[field], f"{label} compatibility domain.{field} must match default preset")

            outputs = event["suggested_outputs"]
            _require(isinstance(outputs, list) and outputs, f"{label} suggested_outputs must be non-empty")
            for output in outputs:
                _require(isinstance(output, str) and output.strip(), f"{label} outputs must be non-empty strings")

            default_resolution = event["default_resolution_preset"]
            _require(default_resolution in resolutions, f"{label} default resolution {default_resolution!r} is not defined")
            event_resolutions = event["resolution_presets"]
            _require(isinstance(event_resolutions, list) and event_resolutions, f"{label} resolution_presets must be non-empty")
            _require(default_resolution in event_resolutions, f"{label} resolution_presets must include default_resolution_preset")
            for preset_id in event_resolutions:
                _require(preset_id in resolutions, f"{label} references unknown resolution preset {preset_id!r}")
        except CatalogueError as exc:
            errors.append(str(exc))
    return errors


def build_search_index(events: dict[str, Any]) -> dict[str, set[str]]:
    """Build a simple exact-token search index from ids, names, aliases and types."""
    index: dict[str, set[str]] = {}
    for event_id, event in events.items():
        terms = [event_id, event.get("name", ""), event.get("event_type", "")]
        terms.extend(event.get("aliases", []))
        for term in terms:
            normalized = str(term).strip().lower()
            if normalized:
                index.setdefault(normalized, set()).add(event_id)
    return index


def search_events(query: str, catalogue: dict[str, Any] | None = None, repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Search events by id, name, alias or event type.

    This intentionally uses predictable local matching rather than a fuzzy search
    dependency.  It returns event dictionaries ordered by id for deterministic UI
    tests and CLI output.
    """
    data = catalogue or load_catalogue(repo_root)
    events = data["events"]
    query_norm = query.strip().lower()
    if not query_norm:
        return []

    matches: set[str] = set()
    index = build_search_index(events)
    if query_norm in index:
        matches.update(index[query_norm])

    for event_id, event in events.items():
        candidates = [event_id, event.get("name", ""), event.get("event_type", ""), *event.get("aliases", [])]
        if any(query_norm in str(candidate).lower() for candidate in candidates):
            matches.add(event_id)

    return [events[event_id] for event_id in sorted(matches)]


def resolve_event(event_id_or_alias: str, catalogue: dict[str, Any] | None = None, repo_root: Path | None = None) -> dict[str, Any]:
    """Resolve an event id, name or alias to a single event dictionary."""
    data = catalogue or load_catalogue(repo_root)
    events = data["events"]
    key = event_id_or_alias.strip().lower()

    if key in events:
        return events[key]

    exact_matches = search_events(event_id_or_alias, data)
    for event in exact_matches:
        candidates = [event["id"], event["name"], event.get("event_type", ""), *event.get("aliases", [])]
        if any(key == str(candidate).lower() for candidate in candidates):
            return event

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        ids = ", ".join(event["id"] for event in exact_matches)
        raise EventNotFoundError(f"Ambiguous event {event_id_or_alias!r}; matches: {ids}")
    raise EventNotFoundError(f"Unknown event: {event_id_or_alias!r}")


def domain_to_job_domain(domain: dict[str, Any]) -> dict[str, Any]:
    """Convert a domain preset into the Workbench job `domain` object."""
    return {
        "label": domain["id"],
        "center_lat": domain["center_lat"],
        "center_lon": domain["center_lon"],
        "dx_km": domain["dx_km"],
        "dy_km": domain["dy_km"],
        "e_we": domain["e_we"],
        "e_sn": domain["e_sn"],
    }


def build_job_config(
    event_id_or_alias: str,
    *,
    domain_id: str | None = None,
    resolution_preset_id: str | None = None,
    mode: str = "dry-run",
    job_id: str | None = None,
    output_directory: str | None = None,
    input_source: str = "era5",
    catalogue: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a Workbench job config from an event and selected presets.

    The returned dictionary is suitable for `workbench/validate.py` and for
    serialising to a JSON job configuration file.
    """
    data = catalogue or load_catalogue(repo_root)
    event = resolve_event(event_id_or_alias, data)
    domains = data["domains"]
    resolutions = data["resolution_presets"]

    selected_domain_id = domain_id or event["default_domain"]
    if selected_domain_id not in domains:
        raise PresetNotFoundError(f"Unknown domain preset: {selected_domain_id!r}")
    selected_resolution_id = resolution_preset_id or event["default_resolution_preset"]
    if selected_resolution_id not in resolutions:
        raise PresetNotFoundError(f"Unknown resolution preset: {selected_resolution_id!r}")

    if selected_domain_id not in event.get("domains", []):
        raise PresetNotFoundError(f"Event {event['id']!r} does not offer domain preset {selected_domain_id!r}")
    if selected_resolution_id not in event.get("resolution_presets", []):
        raise PresetNotFoundError(f"Event {event['id']!r} does not offer resolution preset {selected_resolution_id!r}")

    resolved_job_id = job_id or f"{event['id']}-{mode}"
    return {
        "id": resolved_job_id,
        "mode": mode,
        "name": event["name"],
        "period": dict(event["period"]),
        "domain": domain_to_job_domain(domains[selected_domain_id]),
        "inputs": {"source": input_source},
        "outputs": {"directory": output_directory or f"workbench-runs/{resolved_job_id}"},
        "products": list(event.get("suggested_outputs", [])),
        "metadata": {
            "event_id": event["id"],
            "domain_preset": selected_domain_id,
            "resolution_preset": selected_resolution_id,
        },
    }


def validate_catalogue(repo_root: Path | None = None) -> list[str]:
    """Validate all catalogue files and core event-to-job behavior."""
    try:
        data = load_catalogue(repo_root)
    except CatalogueError as exc:
        return [str(exc)]

    errors: list[str] = []
    errors.extend(validate_domain_presets(data["domains"]))
    errors.extend(validate_resolution_presets(data["resolution_presets"]))
    errors.extend(validate_events(data["events"], data["domains"], data["resolution_presets"]))

    if errors:
        return errors

    index = build_search_index(data["events"])
    if "xaver" not in index or "xaver" not in index["xaver"]:
        errors.append("Search index must find Xaver by alias/id")
    if "storm" not in index or not {"xaver", "kyrill"}.issubset(index["storm"]):
        errors.append("Search index must find storm events by event_type")
    if len(data["events"]["xaver"].get("domains", [])) < 2:
        errors.append("Xaver must offer at least two domain/resolution choices")

    try:
        job_config = build_job_config(
            "xaver",
            mode="dry-run",
            job_id="xaver-generated-preview",
            output_directory="workbench-runs/xaver-generated-preview",
            catalogue=data,
        )
        validation_errors = validate_config(job_config)
        if validation_errors:
            errors.append("Generated Xaver job config must validate: " + "; ".join(validation_errors))
    except CatalogueError as exc:
        errors.append(str(exc))

    return errors


def _cmd_validate(args: argparse.Namespace) -> int:
    errors = validate_catalogue(Path(args.repo_root) if args.repo_root else None)
    if errors:
        print(f"Catalogue validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    data = load_catalogue(Path(args.repo_root) if args.repo_root else None)
    print(
        "Event catalogue OK: "
        f"{len(data['events'])} events, "
        f"{len(data['domains'])} domain presets, "
        f"{len(data['resolution_presets'])} resolution presets"
    )
    print("Generated Xaver preview config validates")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    data = load_catalogue(Path(args.repo_root) if args.repo_root else None)
    matches = search_events(args.query, data)
    print(json.dumps(matches, indent=2) + "\n")
    return 0


def _cmd_build_job(args: argparse.Namespace) -> int:
    data = load_catalogue(Path(args.repo_root) if args.repo_root else None)
    job = build_job_config(
        args.event,
        domain_id=args.domain,
        resolution_preset_id=args.resolution,
        mode=args.mode,
        job_id=args.job_id,
        output_directory=args.output_directory,
        catalogue=data,
    )
    validation_errors = validate_config(job)
    if validation_errors:
        print("Generated job config is invalid:", file=sys.stderr)
        for error in validation_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(json.dumps(job, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Workbench event catalogue utilities")
    parser.add_argument("--repo-root", help="Repository root. Defaults to the current module's repository.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="Validate catalogue, domain presets and resolution presets")

    search = sub.add_parser("search", help="Search events by id, name, alias or event type")
    search.add_argument("query")

    build = sub.add_parser("build-job", help="Generate a Workbench job config from an event and presets")
    build.add_argument("event", help="Event id, name or alias, e.g. xaver")
    build.add_argument("--domain", help="Domain preset id. Defaults to event.default_domain")
    build.add_argument("--resolution", help="Resolution preset id. Defaults to event.default_resolution_preset")
    build.add_argument("--mode", default="dry-run", help="Workbench mode. Defaults to dry-run")
    build.add_argument("--job-id", help="Generated job id. Defaults to <event>-<mode>")
    build.add_argument("--output-directory", help="Generated output directory. Defaults to workbench-runs/<job-id>")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "search":
        return _cmd_search(args)
    if args.command == "build-job":
        return _cmd_build_job(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
