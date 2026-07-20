#!/usr/bin/env python3
"""Regression tests for the validated simulation-store boundary."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from workbench.pipeline_specification_service import PipelineSpecificationServiceError
from workbench.simulation_store import SimulationStore, SimulationStoreError

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_KEY = "a" * 64
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


def valid_specification() -> dict:
    return {
        "specification_key": SPEC_KEY,
        "created_at": "2026-07-20T12:00:00Z",
        "immutable": True,
        "execution_started": False,
        "identity": {
            "job": {"id": "xaver-real", "name": "Xaver real simulation"},
            "era5_input": {
                "plan_key": "b" * 64,
                "files": [
                    {
                        "path": "files/surface.grib",
                        "sha256": "c" * 64,
                        "size_bytes": 123,
                        "request_name": "surface",
                    }
                ],
                "provenance": {
                    "source": "Copernicus Climate Data Store ERA5 reanalysis",
                    "artificial_weather_data": False,
                },
            },
            "runtime": {
                "wps": {"reference": "wps:test", "identity": "sha256:" + "d" * 64},
                "wrf": {"reference": "wrf:test", "identity": "sha256:" + "e" * 64},
                "postprocessing": {
                    "reference": "postprocess:test",
                    "identity": "sha256:" + "f" * 64,
                },
            },
            "steps": [
                {
                    "id": step_id,
                    "label": step_id.replace("-", " ").title(),
                    "status": "PENDING",
                    "inputs": [],
                    "outputs": [],
                    "progress_metrics": [],
                }
                for step_id in STEP_IDS
            ],
        },
    }


class MutableSpecificationService:
    def __init__(self, payload: dict):
        self.payload = payload

    def get(self, key: str) -> dict:
        if key != SPEC_KEY:
            raise PipelineSpecificationServiceError(
                "specification_not_found", "Immutable pipeline specification not found."
            )
        return copy.deepcopy(self.payload)


class SimulationStoreValidationTests(unittest.TestCase):
    def make_store(self, root: Path, payload: dict) -> SimulationStore:
        return SimulationStore(
            REPO_ROOT,
            MutableSpecificationService(payload),  # type: ignore[arg-type]
            database_path=root / "workbench.sqlite3",
        )

    def assert_integrity_error(self, payload: dict) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-store-invalid-") as temporary:
            store = self.make_store(Path(temporary), payload)
            with self.assertRaises(SimulationStoreError) as context:
                store.create_job(SPEC_KEY)
            self.assertEqual("specification_integrity_error", context.exception.code)
            self.assertEqual([], store.list_jobs())

    def test_malformed_specifications_are_classified_before_sqlite_insertion(self) -> None:
        cases: list[dict] = []

        missing_identity = valid_specification()
        missing_identity.pop("identity")
        cases.append(missing_identity)

        invalid_step_shape = valid_specification()
        invalid_step_shape["identity"]["steps"][0] = "input-data"
        cases.append(invalid_step_shape)

        unknown_step = valid_specification()
        unknown_step["identity"]["steps"][0]["id"] = "unknown"
        cases.append(unknown_step)

        duplicate_step = valid_specification()
        duplicate_step["identity"]["steps"][1]["id"] = "input-data"
        cases.append(duplicate_step)

        invalid_plan = valid_specification()
        invalid_plan["identity"]["era5_input"]["plan_key"] = "not-a-plan"
        cases.append(invalid_plan)

        artificial_input = valid_specification()
        artificial_input["identity"]["era5_input"]["provenance"][
            "artificial_weather_data"
        ] = True
        cases.append(artificial_input)

        missing_runtime_reference = valid_specification()
        missing_runtime_reference["identity"]["runtime"]["wrf"].pop("reference")
        cases.append(missing_runtime_reference)

        mutable_runtime_identity = valid_specification()
        mutable_runtime_identity["identity"]["runtime"]["wrf"]["identity"] = "latest"
        cases.append(mutable_runtime_identity)

        already_started = valid_specification()
        already_started["execution_started"] = True
        cases.append(already_started)

        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_integrity_error(payload)

    def test_artifact_paths_are_portable_and_unambiguous(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-store-paths-") as temporary:
            store = self.make_store(Path(temporary), valid_specification())
            job = store.create_job(SPEC_KEY)
            for path in (
                "../outside",
                "..\\outside",
                "C:\\outside",
                "/absolute",
                "outputs//field.nc",
                "outputs/./field.nc",
            ):
                with self.subTest(path=path):
                    with self.assertRaises(SimulationStoreError) as context:
                        store.add_artifact(
                            job["id"],
                            step_id="wrf",
                            kind="wrf-output",
                            relative_path=path,
                        )
                    self.assertEqual("invalid_artifact", context.exception.code)

            artifact = store.add_artifact(
                job["id"],
                step_id="wrf",
                kind="wrf-output",
                relative_path="outputs/wrfout_d01.nc",
            )
            self.assertEqual("outputs/wrfout_d01.nc", artifact["relative_path"])


if __name__ == "__main__":
    unittest.main()
