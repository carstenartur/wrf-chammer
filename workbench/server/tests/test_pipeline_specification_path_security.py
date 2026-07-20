#!/usr/bin/env python3
"""Path-integrity tests for immutable pipeline specifications."""

from __future__ import annotations

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


class PipelineSpecificationPathSecurityTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinked_specification_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pipeline-spec-symlink-") as temporary:
            root = Path(temporary)
            specifications = root / "specifications"
            specifications.mkdir()
            target = specifications / "target"
            target.mkdir()
            spec_key = "a" * 64
            os.symlink(target, specifications / spec_key)
            data = Era5DataService(REPO_ROOT, root / "cache")
            service = PipelineSpecificationService(
                REPO_ROOT,
                data,
                specification_root=specifications,
            )
            with self.assertRaises(PipelineSpecificationServiceError) as context:
                service.get(spec_key)
            self.assertEqual("specification_integrity_error", context.exception.code)


if __name__ == "__main__":
    unittest.main()
