#!/usr/bin/env python3
"""Focused tests for deterministic simulation run manifests."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from workbench.simulation_run_manifest import (
    FORMAT_NAME,
    FORMAT_VERSION,
    SimulationRunManifestError,
    SimulationRunManifestService,
)


class FakeSpecificationService:
    def __init__(self, root: Path, specification: dict):
        self.root = root
        self.specification = specification

    def get(self, specification_key: str) -> dict:
        if specification_key != self.specification["specification_key"]:
            raise KeyError(specification_key)
        return copy.deepcopy(self.specification)


class FakeSimulationStore:
    def __init__(self, specification_service: FakeSpecificationService, job: dict):
        self.specification_service = specification_service
        self.job = job

    def get_job(self, job_id: str) -> dict:
        if job_id != self.job["id"]:
            raise KeyError(job_id)
        return copy.deepcopy(self.job)


class SimulationRunManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="run-manifest-")
        self.root = Path(self.temporary.name)
        self.spec_key = "a" * 64
        spec_dir = self.root / "specifications" / self.spec_key
        spec_dir.mkdir(parents=True)
        (spec_dir / "namelist.wps").write_text(
            "&share\n max_dom = 1,\n/\n", encoding="utf-8"
        )
        (spec_dir / "namelist.input").write_text(
            "&time_control\n run_hours = 6,\n/\n", encoding="utf-8"
        )
        self.specification = {
            "specification_key": self.spec_key,
            "created_at": "2026-07-20T10:00:00Z",
            "immutable": True,
            "execution_started": False,
            "identity": {
                "source_revision": "1" * 40,
                "job": {"id": "xaver", "name": "Storm Xaver"},
                "runtime": {
                    "wrf": {
                        "reference": "wrf:test",
                        "identity": "sha256:" + "b" * 64,
                    }
                },
            },
            "artifacts": {
                "namelist_wps": f"specifications/{self.spec_key}/namelist.wps",
                "namelist_input": f"specifications/{self.spec_key}/namelist.input",
            },
        }
        self.job = {
            "id": "sim-aaaaaaaaaaaa-bbbbbbbbbbbb",
            "specification_key": self.spec_key,
            "retry_of": None,
            "status": "SUCCEEDED",
            "created_at": "2026-07-20T10:05:00Z",
            "queued_at": "2026-07-20T10:06:00Z",
            "started_at": "2026-07-20T10:10:00Z",
            "finished_at": "2026-07-20T10:20:00Z",
            "current_step_id": None,
            "error": None,
            "steps": [
                {
                    "id": "wrf",
                    "position": 0,
                    "status": "SUCCEEDED",
                    "contract": {},
                },
                {
                    "id": "postprocessing",
                    "position": 1,
                    "status": "SUCCEEDED",
                    "contract": {},
                },
            ],
            "input_datasets": [
                {
                    "plan_key": "c" * 64,
                    "provenance": {"artificial_weather_data": False},
                    "files": [
                        {"path": "files/pressure.grib", "size_bytes": 10},
                        {"path": "files/surface.grib", "size_bytes": 20},
                    ],
                }
            ],
            "runtime_snapshots": [
                {
                    "name": "wrf",
                    "reference": "wrf:test",
                    "identity": "sha256:" + "b" * 64,
                }
            ],
            "artifacts": [
                {
                    "step_id": "wrf",
                    "kind": "wrfout",
                    "relative_path": "wrf/wrfout",
                    "size_bytes": 5,
                },
                {
                    "step_id": "postprocessing",
                    "kind": "result-index",
                    "relative_path": "visualizations/result-index.json",
                    "size_bytes": 7,
                },
            ],
            "resource_measurements": [
                {
                    "step_id": "wrf",
                    "cpu_seconds": 2,
                    "wall_seconds": 3,
                    "max_rss_bytes": 100,
                    "disk_bytes": 50,
                    "metadata": {"secret_token": "must-not-leak"},
                },
                {
                    "step_id": "wrf",
                    "cpu_seconds": 1,
                    "wall_seconds": 4,
                    "max_rss_bytes": 200,
                    "disk_bytes": 80,
                    "metadata": {"log_path": str(self.root / "private.log")},
                },
                {
                    "step_id": None,
                    "cpu_seconds": 0.5,
                    "wall_seconds": 1,
                    "max_rss_bytes": 150,
                    "disk_bytes": 100,
                    "metadata": {},
                },
            ],
        }
        service = FakeSpecificationService(
            self.root / "specifications", self.specification
        )
        store = FakeSimulationStore(service, self.job)
        self.service = SimulationRunManifestService(self.root, store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_manifest_contains_verified_configuration_and_resource_summary(self) -> None:
        manifest = self.service.manifest(self.job["id"])
        self.assertEqual(
            manifest["format"], {"name": FORMAT_NAME, "version": FORMAT_VERSION}
        )
        self.assertEqual(manifest["simulation"]["id"], self.job["id"])
        self.assertTrue(manifest["completeness"]["successful"])
        self.assertEqual(
            manifest["immutable_specification"]["specification_key"], self.spec_key
        )
        self.assertIn(
            "run_hours = 6",
            manifest["resolved_namelists"]["namelist_input"]["content"],
        )
        self.assertEqual(manifest["resource_report"]["input_size_bytes"], 30)
        self.assertEqual(manifest["resource_report"]["artifact_size_bytes"], 12)
        self.assertEqual(manifest["resource_report"]["cpu_seconds_sum"], 3.5)
        self.assertEqual(manifest["resource_report"]["wall_seconds_sum"], 8.0)
        self.assertEqual(manifest["resource_report"]["max_rss_bytes"], 200.0)
        self.assertEqual(
            manifest["resource_report"]["max_reported_disk_bytes"], 100.0
        )
        self.assertEqual(manifest["resource_report"]["elapsed_wall_seconds"], 600.0)

        rendered = json.dumps(manifest, sort_keys=True)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("must-not-leak", rendered)
        self.assertIn("[redacted]", rendered)
        self.assertIn("[redacted:absolute-path]", rendered)

        without_integrity = copy.deepcopy(manifest)
        integrity = without_integrity.pop("integrity")
        expected = hashlib.sha256(
            json.dumps(
                without_integrity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(integrity["canonical_payload_sha256"], expected)
        self.assertEqual(self.service.manifest(self.job["id"]), manifest)

    def test_missing_resolved_namelist_fails_closed(self) -> None:
        (self.root / "specifications" / self.spec_key / "namelist.input").unlink()
        with self.assertRaises(SimulationRunManifestError) as context:
            self.service.manifest(self.job["id"])
        self.assertEqual(context.exception.code, "run_manifest_integrity_error")


if __name__ == "__main__":
    unittest.main()
