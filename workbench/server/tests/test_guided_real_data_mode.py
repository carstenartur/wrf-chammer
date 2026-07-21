#!/usr/bin/env python3
"""Regression tests for the guided real-data preview contract."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from workbench.core.catalogue import build_job_config, load_catalogue
from workbench.server._application_base import (
    WorkbenchApplicationHandler as BaseHandler,
)
from workbench.server.application import WorkbenchApplicationHandler
from workbench.validate import validate_config


class GuidedRealDataModeTests(unittest.TestCase):
    def call_preview(self, request: dict) -> dict:
        handler = object.__new__(WorkbenchApplicationHandler)

        def preview(_self, normalized):
            return {
                "ok": True,
                "valid": True,
                "errors": [],
                "config": {
                    "mode": normalized["mode"],
                    "metadata": {},
                },
            }

        with patch.object(
            BaseHandler,
            "_wizard_preview",
            autospec=True,
            side_effect=preview,
        ) as delegated:
            result = handler._wizard_preview(request)
        delegated.assert_called_once()
        return result

    def test_ui_real_data_label_creates_valid_planning_preview(self) -> None:
        original = {
            "mode": "real-data",
            "event": "xaver",
            "planning": {"quality_profile": "balanced"},
        }
        preview = self.call_preview(original)
        config = preview["config"]
        self.assertEqual("dry-run", config["mode"])
        self.assertEqual("real-data", config["metadata"]["requested_data_mode"])
        self.assertEqual(
            "era5-wrf",
            config["metadata"]["requested_execution_mode"],
        )
        self.assertEqual("era5-wrf", preview["requested_execution_mode"])
        self.assertEqual(
            "real-data", original["mode"], "caller input must not mutate"
        )

    def test_planning_config_remains_valid_with_real_run_intent_metadata(self) -> None:
        config = build_job_config(
            "xaver",
            mode="dry-run",
            job_id="xaver-guided-real-preview",
            catalogue=load_catalogue(),
        )
        config.setdefault("metadata", {}).update(
            {
                "requested_data_mode": "real-data",
                "requested_execution_mode": "era5-wrf",
            }
        )
        self.assertEqual([], validate_config(config))

    def test_supported_modes_are_forwarded_without_rewriting(self) -> None:
        for mode in (
            "dry-run",
            "wrf-smoke",
            "era5-offline",
            "era5-download-only",
            "era5-wrf",
        ):
            with self.subTest(mode=mode):
                preview = self.call_preview({"mode": mode})
                self.assertEqual(mode, preview["config"]["mode"])
                self.assertNotIn("requested_execution_mode", preview)


if __name__ == "__main__":
    unittest.main()
