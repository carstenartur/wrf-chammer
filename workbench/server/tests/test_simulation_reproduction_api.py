#!/usr/bin/env python3
"""HTTP contract tests for exact persistent simulation reproduction."""

from __future__ import annotations

import copy
import json
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
SPEC_KEY = "a" * 64


class FakeSpecificationService:
    def __init__(self, specification: dict):
        self.specification = specification

    def get(self, specification_key: str) -> dict:
        if specification_key != SPEC_KEY:
            raise KeyError(specification_key)
        return copy.deepcopy(self.specification)


def request_json(base: str, path: str, *, method: str = "GET", expected: int = 200) -> dict:
    request = Request(base + path, method=method)
    if method == "POST":
        request.add_header("Content-Type", "application/json")
        request.data = b"{}"
    try:
        with urlopen(request, timeout=10) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    if status != expected:
        raise AssertionError(f"{method} {path}: expected {expected}, got {status}: {payload}")
    return payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="simulation-reproduction-api-") as temporary:
        root = Path(temporary)
        specification = {
            "specification_key": SPEC_KEY,
            "created_at": "2026-07-23T06:00:00Z",
            "immutable": True,
            "execution_started": False,
            "identity": {
                "job": {"id": "xaver-reproduction-api", "name": "Xaver reproduction API"},
                "steps": [
                    {"id": step_id, "label": step_id.replace("-", " ").title()}
                    for step_id in STEP_IDS
                ],
                "era5_input": {
                    "plan_key": "b" * 64,
                    "provenance": {"artificial_weather_data": False},
                    "files": [
                        {
                            "path": "files/xaver.grib",
                            "size_bytes": 123,
                            "sha256": "c" * 64,
                        }
                    ],
                },
                "runtime": {
                    "wps": {"reference": "wps:test", "identity": "sha256:" + "d" * 64},
                    "wrf": {"reference": "wrf:test", "identity": "sha256:" + "e" * 64},
                    "postprocessing": {
                        "reference": "postprocess:test",
                        "identity": "sha256:" + "f" * 64,
                    },
                },
            },
        }
        store = SimulationStore(
            root,
            FakeSpecificationService(specification),  # type: ignore[arg-type]
            database_path=root / "state" / "simulations.sqlite3",
        )
        source = store.create_job(SPEC_KEY)

        server = WorkbenchApiServer(
            ("127.0.0.1", 0), WorkbenchApplicationHandler, root
        )
        server.simulation_store = store
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            result = request_json(
                base,
                f"/api/simulations/{source['id']}/reproduce",
                method="POST",
                expected=201,
            )
            reproduced = result["simulation"]
            assert reproduced["id"] != source["id"]
            assert reproduced["status"] == "READY"
            assert reproduced["specification_key"] == SPEC_KEY
            assert reproduced["retry_of"] is None
            assert reproduced["reproduced_from"] == source["id"]
            assert reproduced["queued_at"] is None
            assert reproduced["started_at"] is None

            source_detail = request_json(base, f"/api/simulations/{source['id']}")[
                "simulation"
            ]
            assert source_detail["status"] == "READY"
            assert source_detail["reproductions"] == [reproduced["id"]]

            listing = request_json(base, "/api/simulations")["simulations"]
            by_id = {job["id"]: job for job in listing}
            assert by_id[reproduced["id"]]["reproduced_from"] == source["id"]
            assert by_id[source["id"]]["reproductions"] == [reproduced["id"]]

            retry_conflict = request_json(
                base,
                f"/api/simulations/{source['id']}/retry",
                method="POST",
                expected=409,
            )
            assert retry_conflict["error"]["code"] == "job_not_retryable"

            missing = request_json(
                base,
                "/api/simulations/sim-000000000000-000000000000/reproduce",
                method="POST",
                expected=404,
            )
            assert missing["error"]["code"] == "job_not_found"
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()

    print("Exact simulation reproduction API tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
