#!/usr/bin/env python3
"""Provenance and filesystem tests for postprocessing/result indexing runner."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "ci" / "run-postprocessing-step.py"
SPEC = importlib.util.spec_from_file_location("run_postprocessing_step", RUNNER_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

SPEC_KEY = "a" * 64
PLAN_KEY = "b" * 64
SOURCE_REVISION = "c" * 40


def specification() -> dict:
    return {
        "specification_key": SPEC_KEY,
        "immutable": True,
        "execution_started": False,
        "identity": {
            "source": {"repository_revision": SOURCE_REVISION},
            "era5_input": {
                "plan_key": PLAN_KEY,
                "provenance": {"artificial_weather_data": False},
            },
            "runtime": {
                "postprocessing": {
                    "reference": "postprocess:test",
                    "identity": "sha256:" + "d" * 64,
                }
            },
        },
    }


def metadata(mode: str = "wrf") -> dict:
    return {
        "metadata_version": 1,
        "provenance": {
            "mode": mode,
            "wrfout_files": ["wrfout_d01_test"] if mode == "wrf" else [],
        },
        "layers": [{"id": "max_wind10m"}],
    }


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
class PostprocessingStepRunnerTests(unittest.TestCase):
    def test_fixture_metadata_is_not_accepted_by_product_runner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postprocessing-fixture-mode-") as temporary:
            path = Path(temporary) / "metadata.json"
            path.write_text(json.dumps(metadata("fixture")), encoding="utf-8")
            with self.assertRaises(runner.PostprocessingStepError) as context:
                runner.validate_visualization_metadata(path)
            self.assertEqual("PROCESS_CRASH", context.exception.code)

    def test_result_index_contains_checksums_and_real_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postprocessing-index-") as temporary:
            root = Path(temporary)
            run = root / "run"
            visualization = run / "visualizations"
            layers = visualization / "layers" / "max_wind10m"
            layers.mkdir(parents=True)
            (visualization / "metadata.json").write_text(
                json.dumps(metadata()), encoding="utf-8"
            )
            (layers / "summary.json").write_text(
                json.dumps({"maximum": 42.5}), encoding="utf-8"
            )
            (layers / "latest.geojson").write_text(
                json.dumps({"type": "FeatureCollection", "features": []}),
                encoding="utf-8",
            )
            work = run / "work" / "postprocessing" / "result-indexing"
            work.mkdir(parents=True)
            args = argparse.Namespace(
                run_root=run,
                workdir=work,
                result=work / "result.json",
                progress=work / "progress.json",
            )
            artifacts, progress = runner.run_result_indexing(args, specification())
            self.assertEqual(3, progress["indexed_products"])
            self.assertEqual(1, len(artifacts))
            index_path = run / artifacts[0]["path"]
            index = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(SPEC_KEY, index["specification_key"])
            self.assertEqual(SOURCE_REVISION, index["source_revision"])
            self.assertEqual(PLAN_KEY, index["era5_plan_key"])
            self.assertFalse(index["artificial_weather_data"])
            self.assertEqual(3, len(index["products"]))
            for product in index["products"]:
                self.assertRegex(product["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(product["size_bytes"], 0)

    def test_missing_canonical_source_revision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postprocessing-source-revision-") as temporary:
            root = Path(temporary)
            visualization = root / "run" / "visualizations"
            visualization.mkdir(parents=True)
            (visualization / "metadata.json").write_text(
                json.dumps(metadata()), encoding="utf-8"
            )
            (visualization / "layer.json").write_text("{}", encoding="utf-8")
            args = argparse.Namespace(
                run_root=root / "run",
                workdir=root / "run" / "work" / "postprocessing" / "result-indexing",
            )
            args.workdir.mkdir(parents=True)
            invalid = specification()
            invalid["identity"]["source"] = {"revision": SOURCE_REVISION}
            with self.assertRaises(runner.PostprocessingStepError) as context:
                runner.run_result_indexing(args, invalid)
            self.assertEqual("NAMELIST_INVALID", context.exception.code)

    def test_symlinked_visualization_product_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postprocessing-product-link-") as temporary:
            root = Path(temporary)
            visualization = root / "visualizations"
            visualization.mkdir()
            (visualization / "metadata.json").write_text(
                json.dumps(metadata()), encoding="utf-8"
            )
            external = root / "external.json"
            external.write_text("{}", encoding="utf-8")
            (visualization / "linked.json").symlink_to(external)
            with self.assertRaises(runner.PostprocessingStepError) as context:
                runner.safe_files(visualization)
            self.assertEqual("EXECUTOR_OUTPUT_INVALID", context.exception.code)


if __name__ == "__main__":
    unittest.main()
