#!/usr/bin/env python3
"""Filesystem and retry regressions for the in-container WPS step runner."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "ci" / "run-wps-step.py"
SPEC = importlib.util.spec_from_file_location("run_wps_step", RUNNER_PATH)
assert SPEC and SPEC.loader
run_wps_step = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_wps_step)


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
class WpsStepRunnerSafetyTests(unittest.TestCase):
    def test_symlinked_era5_input_is_rejected_before_resolve(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-step-input-link-") as temporary:
            root = Path(temporary)
            actual = root / "actual.grib"
            actual.write_bytes(b"data")
            link = root / "files" / "surface.grib"
            link.parent.mkdir()
            link.symlink_to(actual)
            with self.assertRaises(run_wps_step.WpsStepError) as context:
                run_wps_step.safe_child(root, "files/surface.grib")
            self.assertEqual("INPUT_DATA_MISSING", context.exception.code)

    def test_frozen_namelist_replaces_mutated_work_copy_on_retry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-step-namelist-") as temporary:
            root = Path(temporary)
            specification = root / "spec"
            work = root / "work"
            specification.mkdir()
            work.mkdir()
            specification_path = specification / "specification.json"
            specification_path.write_text("{}", encoding="utf-8")
            frozen = specification / "namelist.wps"
            frozen.write_text("prefix = 'FILE',\n", encoding="utf-8")
            target = work / "namelist.wps"
            target.write_text("prefix = 'MUTATED',\n", encoding="utf-8")

            copied = run_wps_step.copy_namelist(specification_path, work)
            self.assertEqual("prefix = 'FILE',\n", copied.read_text(encoding="utf-8"))

            target.unlink()
            external = root / "external-namelist"
            external.write_text("prefix = 'EXTERNAL',\n", encoding="utf-8")
            target.symlink_to(external)
            with self.assertRaises(run_wps_step.WpsStepError) as context:
                run_wps_step.copy_namelist(specification_path, work)
            self.assertEqual("NAMELIST_INVALID", context.exception.code)

    def test_symlinked_artifact_is_rejected_before_resolve(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-step-artifact-link-") as temporary:
            root = Path(temporary)
            run = root / "run"
            work = run / "work"
            work.mkdir(parents=True)
            actual = work / "actual.nc"
            actual.write_bytes(b"netcdf")
            linked = work / "met_em.d01.test.nc"
            linked.symlink_to(actual)
            with self.assertRaises(run_wps_step.WpsStepError) as context:
                run_wps_step.relative_artifacts(run, [linked], "wps-metgrid-input")
            self.assertEqual("EXECUTOR_OUTPUT_INVALID", context.exception.code)

    def test_previous_regular_and_symlink_outputs_are_removed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-step-cleanup-") as temporary:
            root = Path(temporary)
            regular = root / "old.nc"
            regular.write_bytes(b"old")
            actual = root / "actual.nc"
            actual.write_bytes(b"actual")
            linked = root / "old-link.nc"
            linked.symlink_to(actual)
            run_wps_step.remove_previous([regular, linked])
            self.assertFalse(regular.exists())
            self.assertFalse(linked.exists())
            self.assertTrue(actual.exists())


if __name__ == "__main__":
    unittest.main()
