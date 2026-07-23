#!/usr/bin/env python3
"""HTTP contract test for the persistent simulation run-manifest endpoint."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from workbench.server.application import WorkbenchApplicationHandler
from workbench.server.server import WorkbenchApiServer
from workbench.simulation_store import SimulationStore


STEP_IDS = (
    "input-data",
    "geogrid",
    "ungrib",
    "metgrid",
    "real",
    "wrf",
    "postprocessing",
    "result-indexing",
)


class FakeSpecificationService:
    def __init__(self, root: Path, specification: dict):
        self.root = root
        self.specification = specification

    def get(self, specification_key: str) -> dict:
        if specification_key != self.specification["specification_key"]:
            raise KeyError(specification_key)
        return copy.deepcopy(self.specification)


def request_json(base: str, path: str, expected: int = 200) -> dict:
    try:
        with urlopen(base + path, timeout=10) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    if status != expected:
        raise AssertionError(f"GET {path}: expected {expected}, got {status}: {payload}")
    return payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="run-manifest-api-") as temporary:
        root = Path(temporary)
        specification_key = "a" * 64
        specification_root = root / "specifications"
        specification_directory = specification_root / specification_key
        specification_directory.mkdir(parents=True)
        namelist_wps = "&share\n max_dom = 1,\n/\n"
        namelist_input = "&time_control\n run_hours = 6,\n/\n"
        (specification_directory / "namelist.wps").write_text(
            namelist_wps, encoding="utf-8"
        )
        (specification_directory / "namelist.input").write_text(
            namelist_input, encoding="utf-8"
        )
        runtime = {
            name: {
                "reference": f"{name}:test",
                "identity": "sha256:" + character * 64,
            }
            for name, character in (
                ("wps", "b"),
                ("wrf", "c"),
                ("postprocessing", "d"),
            )
        }
        specification = {
            "specification_key": specification_key,
            "created_at": "2026-07-23T06:00:00Z",
            "immutable": True,
            "execution_started": False,
            "identity": {
                "job": {"id": "xaver-manifest-api", "name": "Xaver manifest API"},
                "steps": [
                    {"id": step_id, "label": step_id.replace("-", " ").title()}
                    for step_id in STEP_IDS
                ],
                "era5_input": {
                    "plan_key": "e" * 64,
                    "provenance": {"artificial_weather_data": False},
                    "files": [
                        {
                            "path": "files/xaver-pressure.grib",
                            "size_bytes": 123,
                            "sha256": "f" * 64,
                        }
                    ],
                },
                "namelists": {
                    "namelist.wps": {
                        "content": namelist_wps,
                        "sha256": hashlib.sha256(
                            namelist_wps.encode("utf-8")
                        ).hexdigest(),
                    },
                    "namelist.input": {
                        "content": namelist_input,
                        "sha256": hashlib.sha256(
                            namelist_input.encode("utf-8")
                        ).hexdigest(),
                    },
                },
                "runtime": runtime,
                "source_revision": "1" * 40,
            },
            "artifacts": {
                "namelist_wps": f"specifications/{specification_key}/namelist.wps",
                "namelist_input": f"specifications/{specification_key}/namelist.input",
            },
        }
        specification_service = FakeSpecificationService(
            specification_root, specification
        )
        store = SimulationStore(
            root,
            specification_service,
            database_path=root / "state" / "simulations.sqlite3",
        )
        job = store.create_job(specification_key)

        server = WorkbenchApiServer(
            ("127.0.0.1", 0), WorkbenchApplicationHandler, root
        )
        server.simulation_store = store
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            payload = request_json(
                base, f"/api/simulations/{job['id']}/run-manifest"
            )
            manifest = payload["manifest"]
            assert manifest["format"] == {
                "name": "wrf-chammer-run-manifest",
                "version": 1,
            }
            assert manifest["simulation"]["id"] == job["id"]
            assert (
                manifest["immutable_specification"]["specification_key"]
                == specification_key
            )
            assert manifest["resource_report"]["input_size_bytes"] == 123
            assert (
                "run_hours = 6"
                in manifest["resolved_namelists"]["namelist_input"]["content"]
            )
            assert (
                manifest["resolved_namelists"]["namelist_input"]
                ["verified_against_immutable_identity"]
                is True
            )
            assert len(manifest["integrity"]["canonical_payload_sha256"]) == 64

            rendered = json.dumps(payload, sort_keys=True)
            assert str(root) not in rendered
            assert "credential" not in rendered.lower()

            missing = request_json(
                base,
                "/api/simulations/sim-000000000000-000000000000/run-manifest",
                expected=404,
            )
            assert missing["error"]["code"] == "job_not_found"
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()

    print("Simulation run-manifest API tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
