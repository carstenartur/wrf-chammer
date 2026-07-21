#!/usr/bin/env python3
"""Filesystem, retry and progress tests for the in-container WRF step runner."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "ci" / "run-wrf-step.py"
SPEC = importlib.util.spec_from_file_location("run_wrf_step", RUNNER_PATH)
assert SPEC and SPEC.loader
run_wrf_step = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_wrf_step)


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
class WrfStepRunnerTests(unittest.TestCase):
    def test_frozen_namelist_replaces_mutated_retry_copy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wrf-step-namelist-") as temporary:
            root = Path(temporary)
            specification = root / "spec"
            work = root / "work"
            specification.mkdir()
            work.mkdir()
            spec_path = specification / "specification.json"
            spec_path.write_text("{}", encoding="utf-8")
            (specification / "namelist.input").write_text(
                "&time_control\n/\n", encoding="utf-8"
            )
            target = work / "namelist.input"
            target.write_text("mutated", encoding="utf-8")
            copied = run_wrf_step.copy_namelist(spec_path, work)
            self.assertEqual(
                "&time_control\n/\n", copied.read_text(encoding="utf-8")
            )

    def test_symlinked_handoff_input_and_artifact_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wrf-step-links-") as temporary:
            root = Path(temporary)
            run = root / "run"
            source_dir = run / "work" / "wps"
            target_dir = run / "work" / "wrf" / "real"
            source_dir.mkdir(parents=True)
            target_dir.mkdir(parents=True)
            actual = source_dir / "actual.nc"
            actual.write_bytes(b"netcdf")
            linked = source_dir / "met_em.d01.test.nc"
            linked.symlink_to(actual)
            with self.assertRaises(run_wrf_step.WrfStepError) as context:
                run_wrf_step.link_or_copy(
                    linked, target_dir / linked.name, run
                )
            self.assertEqual("INPUT_DATA_MISSING", context.exception.code)

            output = target_dir / "wrfout_d01_test"
            output.symlink_to(actual)
            with self.assertRaises(run_wrf_step.WrfStepError) as context:
                run_wrf_step.collect_artifacts(
                    run, [output], "wrf-model-output"
                )
            self.assertEqual("EXECUTOR_OUTPUT_INVALID", context.exception.code)

    def test_rsl_timing_is_published_as_structured_progress(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wrf-step-progress-") as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            (work / "rsl.out.0000").write_text(
                "Timing for main: time 2013-12-05_15:00:00 on domain 1: "
                "1.0 elapsed seconds\n",
                encoding="utf-8",
            )
            (work / "wrfout_d01_2013-12-05_15:00:00").write_bytes(b"output")
            progress = root / "progress.json"
            run_wrf_step.publish_wrf_progress(
                progress,
                work,
                datetime(2013, 12, 5, 12, tzinfo=timezone.utc),
                datetime(2013, 12, 5, 18, tzinfo=timezone.utc),
                time.monotonic() - 30,
            )
            payload = json.loads(progress.read_text(encoding="utf-8"))
            self.assertEqual(
                "2013-12-05T15:00:00Z", payload["simulation_time"]
            )
            self.assertEqual(10800.0, payload["simulated_seconds"])
            self.assertEqual(21600.0, payload["total_seconds"])
            self.assertEqual(0.5, payload["fraction"])
            self.assertEqual(1, payload["output_files"])
            self.assertGreater(payload["eta_seconds"], 0)

    def test_progress_scans_only_bounded_log_tail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wrf-step-tail-") as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            rsl = work / "rsl.out.0000"
            old_record = (
                "Timing for main: time 2013-12-05_13:00:00 on domain 1: "
                "1.0 elapsed seconds\n"
            )
            latest_record = (
                "Timing for main: time 2013-12-05_17:00:00 on domain 1: "
                "1.0 elapsed seconds\n"
            )
            rsl.write_text(
                old_record
                + ("diagnostic filler without timing records\n" * 4000)
                + latest_record,
                encoding="utf-8",
            )
            self.assertGreater(
                rsl.stat().st_size, run_wrf_step._PROGRESS_TAIL_BYTES
            )
            progress = root / "progress.json"
            run_wrf_step.publish_wrf_progress(
                progress,
                work,
                datetime(2013, 12, 5, 12, tzinfo=timezone.utc),
                datetime(2013, 12, 5, 18, tzinfo=timezone.utc),
                time.monotonic() - 20,
            )
            payload = json.loads(progress.read_text(encoding="utf-8"))
            self.assertEqual(
                "2013-12-05T17:00:00Z", payload["simulation_time"]
            )
            self.assertEqual(18000.0, payload["simulated_seconds"])
            self.assertAlmostEqual(5 / 6, payload["fraction"])


if __name__ == "__main__":
    unittest.main()
