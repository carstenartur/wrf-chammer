#!/usr/bin/env python3
"""Tests for deterministic immutable real-run specification identities."""

from __future__ import annotations

import unittest

from workbench.pipeline_specification import (
    PipelineSpecificationError,
    build_run_specification_identity,
    generate_namelists,
    sha256_value,
)


JOB = {
    "id": "xaver-real-spec",
    "name": "Storm Xaver real micro run",
    "period": {
        "start": "2013-12-05T12:00:00Z",
        "end": "2013-12-05T18:30:00Z",
    },
    "domain": {
        "label": "xaver-small",
        "center_lat": 54.0,
        "center_lon": 9.0,
        "dx_km": 9,
        "dy_km": 9,
        "e_we": 40,
        "e_sn": 30,
    },
    "metadata": {"quality_profile": "balanced"},
}
PLAN_KEY = "a" * 64
PLAN = {
    "plan_key": PLAN_KEY,
    "period": {
        "start": "2013-12-05T12:00:00Z",
        "end": "2013-12-05T18:30:00Z",
        "time_points": 7,
    },
    "cache": {"status": "complete"},
}
CHECKSUMS = {
    "files": {
        "files/pressure.grib": {
            "sha256": "b" * 64,
            "size_bytes": 100,
            "request_name": "pressure",
        },
        "files/surface.grib": {
            "sha256": "c" * 64,
            "size_bytes": 50,
            "request_name": "surface",
        },
    }
}
PROVENANCE = {
    "plan_key": PLAN_KEY,
    "source": "Copernicus Climate Data Store ERA5 reanalysis",
    "datasets": ["pressure", "surface"],
    "verified_at": "2026-07-20T12:00:00Z",
    "download_job_id": "era5-aaaaaaaaaaaa-bbbbbbbbbb",
    "artificial_weather_data": False,
}
RUNTIME = {
    "wps": {"reference": "wps:test", "identity": "sha256:" + "d" * 64},
    "wrf": {"reference": "wrf:test", "identity": "sha256:" + "e" * 64},
    "postprocessing": {
        "reference": "postprocess:test",
        "identity": "sha256:" + "f" * 64,
    },
}


class PipelineSpecificationTests(unittest.TestCase):
    def test_namelists_use_actual_duration_and_profile(self) -> None:
        namelists = generate_namelists(JOB, "small-real-data-demo")
        self.assertIn("run_hours                           = 6", namelists["namelist.input"])
        self.assertIn("run_minutes                         = 30", namelists["namelist.input"])
        self.assertIn("time_step                           = 54", namelists["namelist.input"])
        self.assertIn("e_vert                              = 35", namelists["namelist.input"])
        self.assertIn("start_date = '2013-12-05_12:00:00'", namelists["namelist.wps"])
        self.assertIn("end_date   = '2013-12-05_18:30:00'", namelists["namelist.wps"])

    def test_non_utc_timestamp_is_rejected(self) -> None:
        job = {
            **JOB,
            "period": {
                "start": "2013-12-05T13:00:00+01:00",
                "end": "2013-12-05T18:30:00Z",
            },
        }
        with self.assertRaises(PipelineSpecificationError) as context:
            generate_namelists(job, "small-real-data-demo")
        self.assertIn("must use the UTC offset", str(context.exception))

    def test_invalid_center_coordinate_has_classified_error(self) -> None:
        job = {**JOB, "domain": {**JOB["domain"], "center_lat": "north"}}
        with self.assertRaises(PipelineSpecificationError) as context:
            generate_namelists(job, "small-real-data-demo")
        self.assertIn("domain.center_lat", str(context.exception))

    def test_identity_is_stable_and_contains_eight_step_contracts(self) -> None:
        first, first_namelists = build_run_specification_identity(
            job=JOB,
            era5_plan=PLAN,
            checksums=CHECKSUMS,
            provenance=PROVENANCE,
            runtime=RUNTIME,
            source_revision="1" * 40,
            profile_id="small-real-data-demo",
        )
        second, second_namelists = build_run_specification_identity(
            job=JOB,
            era5_plan=PLAN,
            checksums=CHECKSUMS,
            provenance=PROVENANCE,
            runtime=RUNTIME,
            source_revision="1" * 40,
            profile_id="small-real-data-demo",
        )
        self.assertEqual(sha256_value(first), sha256_value(second))
        self.assertEqual(first_namelists, second_namelists)
        self.assertEqual(8, len(first["steps"]))
        self.assertEqual(
            [
                "input-data",
                "geogrid",
                "ungrib",
                "metgrid",
                "real",
                "wrf",
                "postprocessing",
                "result-indexing",
            ],
            [step["id"] for step in first["steps"]],
        )
        self.assertEqual(2, len(first["era5_input"]["files"]))
        self.assertFalse(first["era5_input"]["provenance"]["artificial_weather_data"])

    def test_incomplete_or_artificial_input_is_rejected(self) -> None:
        incomplete = {**PLAN, "cache": {"status": "partial"}}
        with self.assertRaises(PipelineSpecificationError):
            build_run_specification_identity(
                job=JOB,
                era5_plan=incomplete,
                checksums=CHECKSUMS,
                provenance=PROVENANCE,
                runtime=RUNTIME,
                source_revision="1" * 40,
                profile_id="small-real-data-demo",
            )
        artificial = {**PROVENANCE, "artificial_weather_data": True}
        with self.assertRaises(PipelineSpecificationError):
            build_run_specification_identity(
                job=JOB,
                era5_plan=PLAN,
                checksums=CHECKSUMS,
                provenance=artificial,
                runtime=RUNTIME,
                source_revision="1" * 40,
                profile_id="small-real-data-demo",
            )

    def test_unpinned_runtime_is_rejected(self) -> None:
        runtime = {**RUNTIME, "wrf": {"reference": "wrf:latest", "identity": "latest"}}
        with self.assertRaises(PipelineSpecificationError) as context:
            build_run_specification_identity(
                job=JOB,
                era5_plan=PLAN,
                checksums=CHECKSUMS,
                provenance=PROVENANCE,
                runtime=runtime,
                source_revision="1" * 40,
                profile_id="small-real-data-demo",
            )
        self.assertIn("runtime.wrf.identity", str(context.exception))


if __name__ == "__main__":
    unittest.main()
