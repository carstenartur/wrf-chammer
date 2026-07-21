#!/usr/bin/env python3
"""Unit tests for the product postprocessor provenance facade."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FACADE_PATH = REPO_ROOT / "ci" / "postprocess-product.py"


def load_facade():
    core = types.ModuleType("postprocess_core")

    def load_wrf_netcdf(input_dir):
        return {"job_id": "test", "domain": {}, "times": [], "variables": {}}

    def load_fixture_json(path=None):
        return {"job_id": "fixture", "domain": {}, "times": [], "variables": {}}

    def export_metadata(output_dir, data, layers, max_layers):
        metadata = {
            "jobId": data.get("job_id"),
            "layers": list(layers),
        }
        destination = Path(output_dir) / "metadata.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    core.load_wrf_netcdf = load_wrf_netcdf
    core.load_fixture_json = load_fixture_json
    core.export_metadata = export_metadata
    core.main = lambda: None
    previous = sys.modules.get("postprocess_core")
    sys.modules["postprocess_core"] = core
    try:
        spec = importlib.util.spec_from_file_location(
            "postprocess_product_test_module", FACADE_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("postprocess_core", None)
        else:
            sys.modules["postprocess_core"] = previous


class ProductPostprocessorFacadeTests(unittest.TestCase):
    def test_real_wrf_input_gets_sorted_file_provenance(self) -> None:
        facade = load_facade()
        with tempfile.TemporaryDirectory(prefix="postprocess-product-wrf-") as temporary:
            input_directory = Path(temporary)
            (input_directory / "wrfout_d01_2013-12-05_13:00:00").write_bytes(b"b")
            (input_directory / "wrfout_d01_2013-12-05_12:00:00").write_bytes(b"a")
            (input_directory / "not-wrf-output").write_bytes(b"ignored")
            data = facade.load_wrf_netcdf(input_directory)
            self.assertEqual("wrf", data["provenance"]["mode"])
            self.assertEqual(
                [
                    "wrfout_d01_2013-12-05_12:00:00",
                    "wrfout_d01_2013-12-05_13:00:00",
                ],
                data["provenance"]["wrfout_files"],
            )

    def test_fixture_input_remains_explicitly_marked(self) -> None:
        facade = load_facade()
        data = facade.load_fixture_json("fixture.json")
        self.assertEqual("fixture", data["provenance"]["mode"])
        self.assertEqual([], data["provenance"]["wrfout_files"])

    def test_exported_metadata_contains_source_provenance(self) -> None:
        facade = load_facade()
        with tempfile.TemporaryDirectory(prefix="postprocess-product-metadata-") as temporary:
            output = Path(temporary)
            data = {
                "job_id": "job",
                "provenance": {
                    "mode": "wrf",
                    "wrfout_files": ["wrfout_d01_test"],
                },
            }
            metadata = facade.export_metadata(
                output,
                data,
                {"wind10m": {}},
                {},
            )
            self.assertEqual(data["provenance"], metadata["provenance"])
            persisted = json.loads(
                (output / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["provenance"], persisted["provenance"])
            self.assertFalse((output / ".metadata.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
