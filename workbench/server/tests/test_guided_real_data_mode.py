#!/usr/bin/env python3
"""Regression tests for the guided real-data mode normalization."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from workbench.server._application_base import (
    WorkbenchApplicationHandler as BaseHandler,
)
from workbench.server.application import WorkbenchApplicationHandler


class GuidedRealDataModeTests(unittest.TestCase):
    def call_preview(self, request: dict) -> dict:
        handler = object.__new__(WorkbenchApplicationHandler)
        with patch.object(
            BaseHandler,
            "_wizard_preview",
            autospec=True,
            side_effect=lambda _self, normalized: normalized,
        ) as delegated:
            result = handler._wizard_preview(request)
        delegated.assert_called_once()
        return result

    def test_ui_real_data_label_maps_to_valid_product_mode(self) -> None:
        original = {
            "mode": "real-data",
            "event": "xaver",
            "planning": {"quality_profile": "balanced"},
        }
        normalized = self.call_preview(original)
        self.assertEqual("era5-wrf", normalized["mode"])
        self.assertEqual("real-data", original["mode"], "caller input must not mutate")

    def test_supported_modes_are_forwarded_without_rewriting(self) -> None:
        for mode in ("dry-run", "wrf-smoke", "era5-offline", "era5-download-only", "era5-wrf"):
            with self.subTest(mode=mode):
                normalized = self.call_preview({"mode": mode})
                self.assertEqual(mode, normalized["mode"])


if __name__ == "__main__":
    unittest.main()
