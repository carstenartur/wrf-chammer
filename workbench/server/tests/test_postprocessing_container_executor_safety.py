#!/usr/bin/env python3
"""Specification error semantics for the postprocessing container executor."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workbench import postprocessing_container_executor

SPEC_KEY = "a" * 64


class PostprocessingSpecificationSafetyTests(unittest.TestCase):
    def assert_invalid(self, directory: Path) -> None:
        with self.assertRaises(
            postprocessing_container_executor.ContainerExecutorError
        ) as context:
            postprocessing_container_executor.load_specification(
                directory, SPEC_KEY
            )
        self.assertEqual("NAMELIST_INVALID", context.exception.code)

    def test_missing_specification_is_namelist_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postprocessing-spec-missing-") as temporary:
            self.assert_invalid(Path(temporary) / "missing")

    def test_malformed_specification_is_namelist_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postprocessing-spec-json-") as temporary:
            directory = Path(temporary)
            (directory / "run-specification.json").write_text(
                "{", encoding="utf-8"
            )
            self.assert_invalid(directory)

    def test_inconsistent_identity_is_namelist_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postprocessing-spec-identity-") as temporary:
            directory = Path(temporary)
            (directory / "run-specification.json").write_text(
                json.dumps(
                    {
                        "specification_key": "b" * 64,
                        "immutable": True,
                        "execution_started": False,
                        "identity": {},
                    }
                ),
                encoding="utf-8",
            )
            self.assert_invalid(directory)


if __name__ == "__main__":
    unittest.main()
