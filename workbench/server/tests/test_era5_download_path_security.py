#!/usr/bin/env python3
"""Security regression tests for ERA5 download-job path resolution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workbench.era5_download_manager import (
    Era5DownloadManager,
    Era5DownloadManagerError,
)
from workbench.era5_service import Era5DataService

REPO_ROOT = Path(__file__).resolve().parents[3]


class Era5DownloadPathSecurityTests(unittest.TestCase):
    def test_user_job_id_never_enters_a_path_expression(self) -> None:
        with tempfile.TemporaryDirectory(prefix="era5-manager-safe-path-") as temporary:
            service = Era5DataService(REPO_ROOT, Path(temporary) / "cache")
            manager = Era5DownloadManager(REPO_ROOT, service)
            try:
                with self.assertRaises(Era5DownloadManagerError) as context:
                    manager.events("../../etc/passwd")
                self.assertEqual("download_not_found", context.exception.code)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
