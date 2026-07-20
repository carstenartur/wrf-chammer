#!/usr/bin/env python3
"""Offline tests for immutable pipeline specification persistence."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from workbench.era5_service import Era5DataService
from workbench.pipeline_specification_service import (
    PipelineSpecificationService,
    PipelineSpecificationServiceError,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAN_KEY = "a" * 64


def prepare(service: Era5DataService) -> dict:
    directory = service.plan_directory(PLAN_KEY)
    file_path = directory / "files" / "surface.grib"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"verified-real-era5")
    plan = {
        "ok": True,
        "plan_key": PLAN_KEY,
        "period": {
            "start": "2013-12-05T12:00:00Z",
            "end": "2013-12-05T18:00:00Z",
            "time_points": 7,
        },
        "domain": {"bounds": {"west": 7, "south": 51, "east": 10, "north": 55}},
        "requests": [
            {
                "name": "surface",
                "target": "files/surface.grib",
                "request_key": "b" * 64,
            }
        ],
        "cache": {},
        "provenance": {
            "source": "Copernicus Climate Data Store ERA5 reanalysis",
            "datasets": ["surface"],
            "artificial_weather_data": False,
        },
        "download_config": {
            "requests": {
                "surface": {
                    "dataset": "surface",
                    "request": {"year": ["2013"]},
                    "target": "files/surface.grib",
                }
            }
        },
    }
    (directory / "era5-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (directory / "era5-download-config.json").write_text(
        json.dumps(plan["download_config"]), encoding="utf-8"
    )
    (directory / "checksums.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plan_key": PLAN_KEY,
                "files": {
                    "files/surface.grib": {
                        "sha256": "c" * 64,
                        "size_bytes": file_path.stat().st_size,
                        "request_name": "surface",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (directory / "provenance.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plan_key": PLAN_KEY,
                "source": "Copernicus Climate Data Store ERA5 reanalysis",
                "datasets": ["surface"],
                "verified_at": "2026-07-20T12:00:00Z",
                "download_job_id": "era5-aaaaaaaaaaaa-bbbbbbbbbb",
                "artificial_weather_data": False,
            }
        ),
        encoding="utf-8",
    )
    return {
        "valid": True,
        "config": {
            "id": "xaver-real-spec",
            "name": "Xaver real specification",
            "period": {
                "start": "2013-12-05T12:00:00Z",
                "end": "2013-12-05T18:00:00Z",
            },
            "domain": {
                "label": "xaver-small",
                "center_lat": 53.0,
                "center_lon": 8.5,
                "dx_km": 9,
                "dy_km": 9,
                "e_we": 30,
                "e_sn": 24,
            },
            "metadata": {"quality_profile": "balanced"},
        },
    }


class PipelineSpecificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = dict(os.environ)
        os.environ["WRF_CHAMMER_WPS_RUNTIME_IDENTITY"] = "sha256:" + "d" * 64
        os.environ["WRF_CHAMMER_WRF_RUNTIME_IDENTITY"] = "sha256:" + "e" * 64
        os.environ["WRF_CHAMMER_POSTPROCESSING_RUNTIME_IDENTITY"] = "sha256:" + "f" * 64
        os.environ["WRF_CHAMMER_SOURCE_REVISION"] = "1" * 40

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.previous)

    def test_create_is_content_addressed_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pipeline-spec-service-") as temporary:
            root = Path(temporary)
            data = Era5DataService(REPO_ROOT, root / "cache")
            preview = prepare(data)
            service = PipelineSpecificationService(
                REPO_ROOT,
                data,
                specification_root=root / "specifications",
            )
            first = service.create(
                {"plan_key": PLAN_KEY, "profile": "small-real-data-demo"}, preview
            )
            second = service.create(
                {"plan_key": PLAN_KEY, "profile": "small-real-data-demo"}, preview
            )
            self.assertEqual(first["specification_key"], second["specification_key"])
            self.assertEqual(first["created_at"], second["created_at"])
            directory = root / "specifications" / first["specification_key"]
            self.assertTrue((directory / "run-specification.json").is_file())
            self.assertTrue((directory / "namelist.wps").is_file())
            self.assertTrue((directory / "namelist.input").is_file())
            self.assertEqual(first, service.get(first["specification_key"]))
            self.assertEqual(1, len(service.list()))

    def test_runtime_readiness_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pipeline-spec-readiness-") as temporary:
            data = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            service = PipelineSpecificationService(REPO_ROOT, data)
            self.assertTrue(service.readiness()["ready"])
            os.environ.pop("WRF_CHAMMER_WRF_RUNTIME_IDENTITY")
            readiness = service.readiness()
            self.assertFalse(readiness["ready"])
            self.assertTrue(any("WRF_CHAMMER_WRF_RUNTIME_IDENTITY" in error for error in readiness["errors"]))

    def test_tampered_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pipeline-spec-tamper-") as temporary:
            root = Path(temporary)
            data = Era5DataService(REPO_ROOT, root / "cache")
            preview = prepare(data)
            service = PipelineSpecificationService(
                REPO_ROOT, data, specification_root=root / "specifications"
            )
            specification = service.create({"plan_key": PLAN_KEY}, preview)
            path = (
                root
                / "specifications"
                / specification["specification_key"]
                / "run-specification.json"
            )
            value = json.loads(path.read_text(encoding="utf-8"))
            value["identity"]["job"]["id"] = "tampered"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(PipelineSpecificationServiceError) as context:
                service.get(specification["specification_key"])
            self.assertEqual("specification_integrity_error", context.exception.code)

    def test_missing_checksums_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pipeline-spec-missing-") as temporary:
            root = Path(temporary)
            data = Era5DataService(REPO_ROOT, root / "cache")
            preview = prepare(data)
            (data.plan_directory(PLAN_KEY) / "checksums.json").unlink()
            service = PipelineSpecificationService(
                REPO_ROOT, data, specification_root=root / "specifications"
            )
            with self.assertRaises(PipelineSpecificationServiceError) as context:
                service.create({"plan_key": PLAN_KEY}, preview)
            self.assertEqual("era5_checksums_unavailable", context.exception.code)


if __name__ == "__main__":
    unittest.main()
