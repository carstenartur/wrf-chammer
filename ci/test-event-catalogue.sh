#!/bin/sh
# ci/test-event-catalogue.sh — validate Workbench event and preset catalogues.
#
# This test keeps the event-to-simulation UI data contract honest: every event
# must resolve to reusable domain/resolution presets and must be sufficient to
# generate a valid Workbench job config without hard-coded Xaver logic.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

python3 - "${REPO_ROOT}" <<'PY'
import datetime as _dt
import importlib.util
import json
import re
import sys
from pathlib import Path

repo = Path(sys.argv[1])
event_path = repo / "workbench" / "events" / "catalogue.json"
domain_path = repo / "workbench" / "presets" / "domains.json"
resolution_path = repo / "workbench" / "presets" / "resolutions.json"

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
VALID_WARNINGS = {"low", "medium", "high", "critical"}


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing catalogue file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def parse_utc(value, label):
    if not isinstance(value, str) or not ISO_RE.match(value):
        raise SystemExit(f"{label} must be ISO UTC YYYY-MM-DDTHH:MM:SSZ, got {value!r}")
    return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def load_workbench_validator():
    validate_path = repo / "workbench" / "validate.py"
    spec = importlib.util.spec_from_file_location("workbench_validate", validate_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.validate_config

catalogue = load_json(event_path)
domain_catalogue = load_json(domain_path)
resolution_catalogue = load_json(resolution_path)

events = catalogue.get("events")
domains = domain_catalogue.get("domains")
resolutions = resolution_catalogue.get("resolution_presets")

require(isinstance(events, dict) and events, "catalogue.json must contain a non-empty events object")
require(isinstance(domains, dict) and domains, "domains.json must contain a non-empty domains object")
require(isinstance(resolutions, dict) and resolutions, "resolutions.json must contain a non-empty resolution_presets object")

required_events = {"xaver", "kyrill", "custom-template"}
missing_events = required_events - set(events)
require(not missing_events, f"Missing required events: {sorted(missing_events)}")

# Validate domain presets first so event checks can refer to them.
for domain_id, domain in domains.items():
    required = [
        "id", "label", "center_lat", "center_lon", "dx_km", "dy_km",
        "e_we", "e_sn", "bounds", "intended_use", "runtime_class", "warning_level"
    ]
    for field in required:
        require(field in domain, f"Domain {domain_id!r} missing field {field!r}")
    require(domain["id"] == domain_id, f"Domain key {domain_id!r} must match id {domain['id']!r}")
    require(isinstance(domain["label"], str) and domain["label"].strip(), f"Domain {domain_id!r} label must be non-empty")
    require(isinstance(domain["center_lat"], (int, float)) and -90 <= domain["center_lat"] <= 90, f"Domain {domain_id!r} invalid center_lat")
    require(isinstance(domain["center_lon"], (int, float)) and -180 <= domain["center_lon"] <= 180, f"Domain {domain_id!r} invalid center_lon")
    require(isinstance(domain["dx_km"], (int, float)) and domain["dx_km"] > 0, f"Domain {domain_id!r} invalid dx_km")
    require(isinstance(domain["dy_km"], (int, float)) and domain["dy_km"] > 0, f"Domain {domain_id!r} invalid dy_km")
    require(isinstance(domain["e_we"], int) and domain["e_we"] >= 3, f"Domain {domain_id!r} invalid e_we")
    require(isinstance(domain["e_sn"], int) and domain["e_sn"] >= 3, f"Domain {domain_id!r} invalid e_sn")
    bounds = domain["bounds"]
    require(isinstance(bounds, list) and len(bounds) == 4, f"Domain {domain_id!r} bounds must be [west,south,east,north]")
    west, south, east, north = bounds
    require(-180 <= west < east <= 180, f"Domain {domain_id!r} invalid west/east bounds")
    require(-90 <= south < north <= 90, f"Domain {domain_id!r} invalid south/north bounds")
    require(domain["warning_level"] in VALID_WARNINGS, f"Domain {domain_id!r} invalid warning_level")

# Validate resolution/runtime presets.
for preset_id, preset in resolutions.items():
    required = [
        "id", "label", "description", "suggested_dx_km", "suggested_dy_km",
        "suggested_max_grid_cells", "runtime_class", "warning_level"
    ]
    for field in required:
        require(field in preset, f"Resolution preset {preset_id!r} missing field {field!r}")
    require(preset["id"] == preset_id, f"Resolution key {preset_id!r} must match id {preset['id']!r}")
    require(preset["suggested_dx_km"] > 0 and preset["suggested_dy_km"] > 0, f"Resolution preset {preset_id!r} spacing must be positive")
    require(isinstance(preset["suggested_max_grid_cells"], int) and preset["suggested_max_grid_cells"] > 0, f"Resolution preset {preset_id!r} max cells must be positive")
    require(preset["warning_level"] in VALID_WARNINGS, f"Resolution preset {preset_id!r} invalid warning_level")

# Validate event catalogue and cross references.
search_index = {}
for event_id, event in events.items():
    required = [
        "id", "name", "aliases", "event_type", "description", "period",
        "default_domain", "domains", "suggested_outputs",
        "default_resolution_preset", "resolution_presets"
    ]
    for field in required:
        require(field in event, f"Event {event_id!r} missing field {field!r}")
    require(event["id"] == event_id, f"Event key {event_id!r} must match id {event['id']!r}")
    require(isinstance(event["name"], str) and event["name"].strip(), f"Event {event_id!r} name must be non-empty")
    require(isinstance(event["description"], str) and event["description"].strip(), f"Event {event_id!r} description must be non-empty")
    require(isinstance(event["event_type"], str) and event["event_type"].strip(), f"Event {event_id!r} event_type must be non-empty")

    aliases = event["aliases"]
    require(isinstance(aliases, list) and aliases, f"Event {event_id!r} aliases must be a non-empty list")
    for alias in aliases:
        require(isinstance(alias, str) and alias.strip(), f"Event {event_id!r} aliases must contain non-empty strings")

    period = event["period"]
    require(isinstance(period, dict), f"Event {event_id!r} period must be an object")
    start = parse_utc(period.get("start"), f"Event {event_id!r} period.start")
    end = parse_utc(period.get("end"), f"Event {event_id!r} period.end")
    require(start < end, f"Event {event_id!r} period.start must be before period.end")

    # Backward compatibility with earlier Workbench catalogue readers.
    if "start" in event:
        require(event["start"] == period["start"], f"Event {event_id!r} start must match period.start")
    if "end" in event:
        require(event["end"] == period["end"], f"Event {event_id!r} end must match period.end")

    default_domain = event["default_domain"]
    require(default_domain in domains, f"Event {event_id!r} default_domain {default_domain!r} is not defined")
    event_domains = event["domains"]
    require(isinstance(event_domains, list) and event_domains, f"Event {event_id!r} domains must be non-empty")
    require(default_domain in event_domains, f"Event {event_id!r} domains must include default_domain")
    for domain_id in event_domains:
        require(domain_id in domains, f"Event {event_id!r} references unknown domain {domain_id!r}")

    if "domain" in event:
        inline = event["domain"]
        preset = domains[default_domain]
        for field in ("center_lat", "center_lon", "dx_km", "dy_km", "e_we", "e_sn"):
            require(inline.get(field) == preset[field], f"Event {event_id!r} compatibility domain.{field} must match default preset")

    outputs = event["suggested_outputs"]
    require(isinstance(outputs, list) and outputs, f"Event {event_id!r} suggested_outputs must be non-empty")
    for output in outputs:
        require(isinstance(output, str) and output.strip(), f"Event {event_id!r} outputs must be non-empty strings")

    default_resolution = event["default_resolution_preset"]
    require(default_resolution in resolutions, f"Event {event_id!r} default resolution {default_resolution!r} is not defined")
    event_resolutions = event["resolution_presets"]
    require(isinstance(event_resolutions, list) and event_resolutions, f"Event {event_id!r} resolution_presets must be non-empty")
    require(default_resolution in event_resolutions, f"Event {event_id!r} resolution_presets must include default_resolution_preset")
    for preset_id in event_resolutions:
        require(preset_id in resolutions, f"Event {event_id!r} references unknown resolution preset {preset_id!r}")

    terms = [event_id, event["name"], event["event_type"], *aliases]
    for term in terms:
        search_index.setdefault(term.lower(), set()).add(event_id)

require("xaver" in search_index and "xaver" in search_index["xaver"], "Search index must find Xaver by alias/id")
require("storm" in search_index and {"xaver", "kyrill"}.issubset(search_index["storm"]), "Search index must find storm events by event_type")
require(len(events["xaver"]["domains"]) >= 2, "Xaver must offer at least two domain/resolution choices")

# Prove that a UI can generate a valid Workbench job config from event + preset
# data without hard-coded Xaver fields.
validate_config = load_workbench_validator()
xaver = events["xaver"]
xaver_domain = domains[xaver["default_domain"]]
job_config = {
    "id": "xaver-generated-preview",
    "mode": "dry-run",
    "name": xaver["name"],
    "period": xaver["period"],
    "domain": {
        "label": xaver_domain["id"],
        "center_lat": xaver_domain["center_lat"],
        "center_lon": xaver_domain["center_lon"],
        "dx_km": xaver_domain["dx_km"],
        "dy_km": xaver_domain["dy_km"],
        "e_we": xaver_domain["e_we"],
        "e_sn": xaver_domain["e_sn"],
    },
    "inputs": {"source": "era5"},
    "outputs": {"directory": "workbench-runs/xaver-generated-preview"},
}
validation_errors = validate_config(job_config)
require(not validation_errors, "Generated Xaver job config must validate: " + "; ".join(validation_errors))

print(f"Event catalogue OK: {len(events)} events, {len(domains)} domain presets, {len(resolutions)} resolution presets")
print("Generated Xaver preview config validates")
PY
