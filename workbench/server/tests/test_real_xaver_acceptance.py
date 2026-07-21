#!/usr/bin/env python3
"""Offline integrity tests for the persistent real-Xaver acceptance command."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from workbench.validate import validate_config

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "ci" / "run-real-xaver-acceptance.py"
SPEC = importlib.util.spec_from_file_location("real_xaver_acceptance", SCRIPT)
assert SPEC and SPEC.loader
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)

PLAN_KEY = "a" * 64
SPEC_KEY = "b" * 64
SOURCE_REVISION = "c" * 40
START = "2013-12-05T12:00:00Z"
END = "2013-12-06T06:00:00Z"


class FakeDataService:
    def __init__(self, root: Path):
        self.root = root

    def plan_directory(self, plan_key: str) -> Path:
        return self.root / plan_key


def write_real_plan(root: Path) -> tuple[FakeDataService, dict]:
    service = FakeDataService(root)
    directory = service.plan_directory(PLAN_KEY)
    input_file = directory / "files" / "pressure.grib"
    input_file.parent.mkdir(parents=True)
    input_file.write_bytes(b"real-data-integrity-test")
    digest = acceptance.sha256_file(input_file)
    (directory / "era5-plan.json").write_text(
        json.dumps(
            {
                "plan_key": PLAN_KEY,
                "period": {
                    "start": START,
                    "end": END,
                    "interval_hours": 1,
                    "time_points": 19,
                },
                "cache": {"status": "complete"},
            }
        ),
        encoding="utf-8",
    )
    (directory / "checksums.json").write_text(
        json.dumps(
            {
                "files": {
                    "files/pressure.grib": {
                        "sha256": digest,
                        "size_bytes": input_file.stat().st_size,
                        "request_name": "pressure_levels_20131205",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (directory / "provenance.json").write_text(
        json.dumps(
            {
                "plan_key": PLAN_KEY,
                "source": "Copernicus Climate Data Store ERA5 reanalysis",
                "artificial_weather_data": False,
            }
        ),
        encoding="utf-8",
    )
    preview = {
        "valid": True,
        "config": {"period": {"start": START, "end": END}},
    }
    return service, preview


def specification() -> dict:
    return {
        "specification_key": SPEC_KEY,
        "identity": {
            "source": {"repository_revision": SOURCE_REVISION},
            "era5_input": {"plan_key": PLAN_KEY},
        },
    }


def write_result_index(repo: Path) -> tuple[Path, Path, dict]:
    run = repo / "workbench-runs" / "simulations" / "job-1"
    visualization = run / "visualizations"
    result_directory = run / "results"
    visualization.mkdir(parents=True)
    result_directory.mkdir(parents=True)
    metadata = visualization / "metadata.json"
    layer = visualization / "wind10m.json"
    metadata.write_text("{}", encoding="utf-8")
    layer.write_text('{"frames":[]}', encoding="utf-8")
    products = [
        {
            "path": path.relative_to(run).as_posix(),
            "sha256": acceptance.sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in (metadata, layer)
    ]
    index = result_directory / "index.json"
    index.write_text(
        json.dumps(
            {
                "specification_key": SPEC_KEY,
                "source_revision": SOURCE_REVISION,
                "era5_plan_key": PLAN_KEY,
                "artificial_weather_data": False,
                "visualization_provenance": {
                    "mode": "wrf",
                    "wrfout_files": ["wrfout_d01_2013-12-05_12:00:00"],
                },
                "products": products,
            }
        ),
        encoding="utf-8",
    )
    job = {
        "id": "job-1",
        "artifacts": [
            {
                "kind": "result-index",
                "relative_path": "results/index.json",
            }
        ],
    }
    return index, layer, job


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
class RealXaverAcceptanceTests(unittest.TestCase):
    def test_preview_is_valid_planning_state_with_real_run_intent(self) -> None:
        args = SimpleNamespace(
            west=2.0,
            south=51.0,
            east=13.0,
            north=58.0,
            start=START,
            end=END,
            quality_profile="balanced",
            job_id="xaver-real-acceptance-test",
        )
        preview = acceptance.build_preview(args, REPO_ROOT)
        self.assertTrue(preview["valid"])
        self.assertEqual("dry-run", preview["config"]["mode"])
        self.assertEqual("era5-wrf", preview["requested_execution_mode"])
        metadata = preview["config"]["metadata"]
        self.assertEqual("real-data", metadata["requested_data_mode"])
        self.assertEqual("era5-wrf", metadata["requested_execution_mode"])
        self.assertIn("resource_estimate", metadata)
        self.assertEqual([], validate_config(preview["config"]))

    def test_real_plan_accepts_canonical_extra_period_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="xaver-real-plan-") as temporary:
            service, preview = write_real_plan(Path(temporary))
            verified = acceptance.verify_real_plan(service, PLAN_KEY, preview)
            self.assertEqual(PLAN_KEY, verified["plan"]["plan_key"])
            self.assertEqual(1, len(verified["checksums"]["files"]))
            self.assertFalse(verified["provenance"]["artificial_weather_data"])

    def test_real_plan_rejects_a_period_boundary_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="xaver-period-mismatch-") as temporary:
            service, preview = write_real_plan(Path(temporary))
            preview["config"]["period"]["end"] = "2013-12-06T07:00:00Z"
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.verify_real_plan(service, PLAN_KEY, preview)

    def test_real_plan_rejects_symlinked_plan_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="xaver-plan-symlink-") as temporary:
            root = Path(temporary)
            service, preview = write_real_plan(root)
            directory = service.plan_directory(PLAN_KEY)
            plan = directory / "era5-plan.json"
            external = root / "external-plan.json"
            plan.replace(external)
            plan.symlink_to(external)
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.verify_real_plan(service, PLAN_KEY, preview)

    def test_result_index_verifies_every_product_checksum(self) -> None:
        with tempfile.TemporaryDirectory(prefix="xaver-result-index-") as temporary:
            repo = Path(temporary)
            _index, layer, job = write_result_index(repo)
            verified = acceptance.verify_result_index(repo, job, specification())
            self.assertEqual(2, len(verified["products"]))
            self.assertEqual(1, len(verified["wrfout_files"]))

            layer.write_text("tampered", encoding="utf-8")
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.verify_result_index(repo, job, specification())

    def test_result_index_rejects_invalid_checksum_and_size_types(self) -> None:
        with tempfile.TemporaryDirectory(prefix="xaver-result-metadata-") as temporary:
            repo = Path(temporary)
            index, _layer, job = write_result_index(repo)
            payload = json.loads(index.read_text(encoding="utf-8"))

            payload["products"][0]["sha256"] = "not-a-sha256"
            index.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                acceptance.AcceptanceError, "SHA-256 metadata is invalid"
            ):
                acceptance.verify_result_index(repo, job, specification())

            payload = json.loads(index.read_text(encoding="utf-8"))
            payload["products"][0]["sha256"] = acceptance.sha256_file(
                repo / "workbench-runs" / "simulations" / "job-1" / payload["products"][0]["path"]
            )
            payload["products"][0]["size_bytes"] = True
            index.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                acceptance.AcceptanceError, "size metadata is invalid"
            ):
                acceptance.verify_result_index(repo, job, specification())


if __name__ == "__main__":
    unittest.main()
