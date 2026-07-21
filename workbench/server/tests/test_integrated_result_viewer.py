#!/usr/bin/env python3
"""HTTP and integrity tests for the integrated per-job result viewer."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workbench.server.application import WorkbenchApplicationHandler
from workbench.server.server import WorkbenchApiServer
from workbench.simulation_result_service import (
    SimulationResultError,
    SimulationResultService,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
JOB_ID = "sim-aaaaaaaaaaaa-bbbbbbbbbbbb"
SPECIFICATION_KEY = "c" * 64
SOURCE_REVISION = "d" * 40
ERA5_PLAN_KEY = "e" * 64
WRF_OUTPUTS = ["wrfout_d01_2013-12-05_12:00:00"]
RUNTIME = {
    "wps": {"reference": "wps:test", "identity": "sha256:" + "1" * 64},
    "wrf": {"reference": "wrf:test", "identity": "sha256:" + "2" * 64},
    "postprocessing": {
        "reference": "postprocessing:test",
        "identity": "sha256:" + "3" * 64,
    },
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class FakeSpecificationService:
    def get(self, specification_key: str) -> dict:
        if specification_key != SPECIFICATION_KEY:
            raise AssertionError(f"unexpected specification key: {specification_key}")
        return {
            "specification_key": SPECIFICATION_KEY,
            "identity": {
                "source": {"repository_revision": SOURCE_REVISION},
                "era5_input": {"plan_key": ERA5_PLAN_KEY},
                "runtime": copy.deepcopy(RUNTIME),
            },
        }


class FakeStore:
    def __init__(self, job: dict):
        self.job = job
        self.specification_service = FakeSpecificationService()

    def get_job(self, job_id: str) -> dict:
        if job_id != JOB_ID:
            raise AssertionError(f"unexpected job id: {job_id}")
        return copy.deepcopy(self.job)


class IntegratedResultViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="integrated-result-viewer-")
        self.root = Path(self.temporary.name)
        viewer = self.root / "visualization" / "web" / "index.html"
        viewer.parent.mkdir(parents=True)
        viewer.write_bytes((REPO_ROOT / "visualization" / "web" / "index.html").read_bytes())

        self.run_root = self.root / "workbench-runs" / "simulations" / JOB_ID
        visualization = self.run_root / "visualizations"
        layers = visualization / "layers"
        results = self.run_root / "results"
        layers.mkdir(parents=True)
        results.mkdir(parents=True)

        self.layer_path = layers / "wind10m.json"
        self.layer_path.write_text(
            json.dumps(
                {
                    "id": "wind10m",
                    "type": "raster-time-series",
                    "unit": "m s-1",
                    "times": ["2013-12-05T12:00:00Z"],
                    "vmin": 2.0,
                    "vmax": 18.0,
                    "frames": [[[2.0, 8.0], [12.0, 18.0]]],
                }
            ),
            encoding="utf-8",
        )
        self.metadata_path = visualization / "metadata.json"
        self.metadata_path.write_text(
            json.dumps(
                {
                    "job_id": JOB_ID,
                    "domain": {
                        "bounds": {"west": 2, "south": 51, "east": 13, "north": 58},
                        "nx": 2,
                        "ny": 2,
                    },
                    "layers": [
                        {
                            "id": "wind10m",
                            "label": "10 m wind speed",
                            "unit": "m s-1",
                            "file": "layers/wind10m.json",
                            "vmin": 2.0,
                            "vmax": 18.0,
                        }
                    ],
                    "provenance": {"mode": "wrf", "wrfout_files": WRF_OUTPUTS},
                }
            ),
            encoding="utf-8",
        )
        products = []
        for path in (self.metadata_path, self.layer_path):
            body = path.read_bytes()
            products.append(
                {
                    "path": path.relative_to(self.run_root).as_posix(),
                    "sha256": sha256_bytes(body),
                    "size_bytes": len(body),
                }
            )
        self.original_index = {
            "version": 1,
            "specification_key": SPECIFICATION_KEY,
            "source_revision": SOURCE_REVISION,
            "era5_plan_key": ERA5_PLAN_KEY,
            "runtime": copy.deepcopy(RUNTIME),
            "artificial_weather_data": False,
            "visualization_provenance": {
                "mode": "wrf",
                "wrfout_files": WRF_OUTPUTS,
            },
            "products": products,
        }
        self.index_path = results / "index.json"
        self.index_path.write_text(json.dumps(self.original_index), encoding="utf-8")
        index_body = self.index_path.read_bytes()
        self.job = {
            "id": JOB_ID,
            "status": "SUCCEEDED",
            "specification_key": SPECIFICATION_KEY,
            "artifacts": [
                {
                    "kind": "result-index",
                    "relative_path": "results/index.json",
                    "sha256": sha256_bytes(index_body),
                    "size_bytes": len(index_body),
                }
            ],
        }
        self.store = FakeStore(self.job)
        self.service = SimulationResultService(self.root, self.store)
        self.server = WorkbenchApiServer(
            ("127.0.0.1", 0), WorkbenchApplicationHandler, self.root
        )
        self.server.simulation_result_service = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def request(self, path: str, expected: int = 200) -> tuple[dict[str, str], bytes]:
        request = Request(self.base + path, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=10) as response:
                status = response.status
                headers = dict(response.headers.items())
                body = response.read()
        except HTTPError as exc:
            status = exc.code
            headers = dict(exc.headers.items())
            body = exc.read()
        self.assertEqual(expected, status, body.decode("utf-8", errors="replace"))
        return headers, body

    def rewrite_index(self, index: dict) -> None:
        self.index_path.write_text(json.dumps(index), encoding="utf-8")
        body = self.index_path.read_bytes()
        self.store.job["artifacts"][0]["sha256"] = sha256_bytes(body)
        self.store.job["artifacts"][0]["size_bytes"] = len(body)

    def test_manifest_viewer_and_indexed_products_are_served(self) -> None:
        _headers, body = self.request(f"/api/simulations/{JOB_ID}/results")
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        results = payload["results"]
        self.assertEqual(f"/jobs/{JOB_ID}/results/", results["viewer_url"])
        self.assertFalse(results["artificial_weather_data"])
        self.assertEqual("wrf", results["provenance"]["mode"])
        self.assertEqual(SOURCE_REVISION, results["source_revision"])
        self.assertEqual(ERA5_PLAN_KEY, results["era5_plan_key"])
        self.assertEqual(
            ["layers/wind10m.json", "metadata.json"],
            [product["path"] for product in results["products"]],
        )

        headers, viewer = self.request(f"/jobs/{JOB_ID}/results")
        rendered = viewer.decode("utf-8")
        self.assertIn(f'<base href="/jobs/{JOB_ID}/results/">', rendered)
        self.assertIn("Model result, not an observation", rendered)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])

        headers, metadata = self.request(f"/jobs/{JOB_ID}/results/metadata.json")
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertEqual(JOB_ID, json.loads(metadata)["job_id"])

        _headers, layer = self.request(
            f"/jobs/{JOB_ID}/results/layers/wind10m.json"
        )
        self.assertEqual("wind10m", json.loads(layer)["id"])

    def test_unindexed_and_tampered_products_are_rejected(self) -> None:
        hidden = self.run_root / "visualizations" / "not-indexed.txt"
        hidden.write_text("must not be served", encoding="utf-8")
        _headers, body = self.request(
            f"/jobs/{JOB_ID}/results/not-indexed.txt", expected=404
        )
        self.assertEqual("result_not_found", json.loads(body)["error"]["code"])

        self.layer_path.write_text("tampered", encoding="utf-8")
        _headers, body = self.request(
            f"/jobs/{JOB_ID}/results/layers/wind10m.json", expected=422
        )
        self.assertEqual(
            "result_integrity_error", json.loads(body)["error"]["code"]
        )

    def test_non_successful_jobs_and_unsafe_paths_are_rejected(self) -> None:
        self.store.job["status"] = "FAILED"
        _headers, body = self.request(
            f"/api/simulations/{JOB_ID}/results", expected=409
        )
        self.assertEqual("results_not_ready", json.loads(body)["error"]["code"])
        self.store.job["status"] = "SUCCEEDED"

        for value in ("../metadata.json", "/metadata.json", "layers\\wind.json"):
            with self.subTest(value=value):
                with self.assertRaises(SimulationResultError):
                    self.service.read_product(JOB_ID, value)

    def test_result_index_and_metadata_provenance_are_verified(self) -> None:
        index = copy.deepcopy(self.original_index)
        index["visualization_provenance"]["mode"] = "fixture"
        self.rewrite_index(index)
        with self.assertRaisesRegex(SimulationResultError, "WRF output"):
            self.service.manifest(JOB_ID)

    def test_result_index_must_match_immutable_revision_plan_and_runtime(self) -> None:
        for field, invalid in (
            ("source_revision", "f" * 40),
            ("era5_plan_key", "f" * 64),
            ("runtime", {"wrf": {"identity": "sha256:" + "f" * 64}}),
        ):
            with self.subTest(field=field):
                index = copy.deepcopy(self.original_index)
                index[field] = invalid
                self.rewrite_index(index)
                try:
                    with self.assertRaises(SimulationResultError):
                        self.service.manifest(JOB_ID)
                finally:
                    self.rewrite_index(copy.deepcopy(self.original_index))


if __name__ == "__main__":
    unittest.main()
