#!/usr/bin/env python3
"""Offline tests for the secret-safe CDS credential test script."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPO_ROOT / "ci" / "validate-cds-credentials.py"


class CdsCredentialValidatorTests(unittest.TestCase):
    def run_validator(self, module_source: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory(prefix="cds-validator-test-") as temporary:
            root = Path(temporary)
            (root / "cdsapi.py").write_text(textwrap.dedent(module_source), encoding="utf-8")
            result_path = root / "result.json"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(root)
            completed = subprocess.run(
                [sys.executable, str(VALIDATOR), "--result", str(result_path)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            return completed, json.loads(result_path.read_text(encoding="utf-8"))

    def test_success_uses_real_request_shape_and_retains_no_file(self) -> None:
        completed, result = self.run_validator(
            """
            from pathlib import Path
            class Client:
                def __init__(self, *args, **kwargs):
                    pass
                def retrieve(self, dataset, request, target):
                    assert dataset == "reanalysis-era5-single-levels"
                    assert request["variable"] == ["2m_temperature"]
                    assert request["area"] == [52.0, 7.0, 51.75, 7.25]
                    Path(target).write_bytes(b"tiny-real-era5-response")
            """
        )
        self.assertEqual(0, completed.returncode)
        self.assertEqual("VALID", result["status"])
        self.assertEqual("credentials_valid", result["code"])
        self.assertGreater(result["response"]["size_bytes"], 0)
        self.assertEqual(64, len(result["response"]["sha256"]))
        self.assertFalse(result["response"]["retained"])
        self.assertFalse(result["artificial_weather_data"])

    def test_provider_exception_is_classified_without_raw_secret(self) -> None:
        completed, result = self.run_validator(
            """
            class Client:
                def __init__(self, *args, **kwargs):
                    pass
                def retrieve(self, dataset, request, target):
                    raise RuntimeError("401 invalid API key SUPER-SECRET-VALUE")
            """
        )
        self.assertEqual(2, completed.returncode)
        self.assertEqual("INVALID", result["status"])
        self.assertEqual("invalid_credentials", result["code"])
        rendered = json.dumps(result) + completed.stdout + completed.stderr
        self.assertNotIn("SUPER-SECRET-VALUE", rendered)
        self.assertNotIn("401 invalid API key", rendered)


if __name__ == "__main__":
    unittest.main()
