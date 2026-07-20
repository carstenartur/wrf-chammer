#!/usr/bin/env python3
"""Offline tests for persistent CDS credential validation orchestration."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from workbench.era5_credential_service import (
    Era5CredentialValidationError,
    Era5CredentialValidationService,
)
from workbench.era5_service import Era5DataService

REPO_ROOT = Path(__file__).resolve().parents[3]
TERMINAL = {"VALID", "INVALID", "FAILED", "CANCELLED"}


def write_fake_validator(path: Path, *, status: str = "VALID", sleep_seconds: float = 0) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n" + textwrap.dedent(f"""
        import argparse, json, time
        from pathlib import Path
        p = argparse.ArgumentParser()
        p.add_argument("--result", required=True)
        a = p.parse_args()
        time.sleep({sleep_seconds})
        Path(a.result).write_text(json.dumps({{
            "version": 1,
            "status": "{status}",
            "code": "credentials_valid" if "{status}" == "VALID" else "invalid_credentials",
            "summary": "classified fake result",
            "checked_at": "2026-07-20T12:00:00Z",
            "duration_seconds": 0.1,
            "request": {{
                "dataset": "reanalysis-era5-single-levels",
                "variable": "2m_temperature",
                "date": "2013-12-05",
                "time": "12:00 UTC",
                "area": [52.0, 7.0, 51.75, 7.25]
            }},
            "response": {{"size_bytes": 12, "sha256": "a" * 64, "retained": False}},
            "artificial_weather_data": False
        }}))
        raise SystemExit(0 if "{status}" == "VALID" else 2)
        """),
        encoding="utf-8",
    )


def wait_for_terminal(service: Era5CredentialValidationService, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.status()
        validation = status["validation"]
        if validation and validation["status"] in TERMINAL:
            return validation
        time.sleep(0.05)
    raise AssertionError(f"validation did not finish: {service.status()}")


class Era5CredentialValidationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_key = os.environ.get("CDSAPI_KEY")
        os.environ["CDSAPI_KEY"] = "TEST-SECRET-MUST-NOT-LEAK"

    def tearDown(self) -> None:
        if self.previous_key is None:
            os.environ.pop("CDSAPI_KEY", None)
        else:
            os.environ["CDSAPI_KEY"] = self.previous_key

    def test_valid_result_is_persisted_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cds-service-valid-") as temporary:
            root = Path(temporary)
            data = Era5DataService(REPO_ROOT, root / "cache")
            fake = root / "validator.py"
            write_fake_validator(fake)
            service = Era5CredentialValidationService(
                REPO_ROOT, data, validator_path=fake, timeout_seconds=5
            )
            try:
                started = service.start()
                self.assertEqual("RUNNING", started["status"])
                completed = wait_for_terminal(service)
                self.assertEqual("VALID", completed["status"])
                self.assertEqual("credentials_valid", completed["code"])
                self.assertFalse(completed["result"]["response"]["retained"])
                rendered = json.dumps(service.status())
                self.assertNotIn("TEST-SECRET-MUST-NOT-LEAK", rendered)
                self.assertNotIn(str(root), rendered)
            finally:
                service.close()

    def test_parallel_start_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cds-service-running-") as temporary:
            root = Path(temporary)
            data = Era5DataService(REPO_ROOT, root / "cache")
            fake = root / "validator.py"
            write_fake_validator(fake, sleep_seconds=2)
            service = Era5CredentialValidationService(
                REPO_ROOT, data, validator_path=fake, timeout_seconds=5
            )
            try:
                service.start()
                with self.assertRaises(Era5CredentialValidationError) as context:
                    service.start()
                self.assertEqual("validation_in_progress", context.exception.code)
            finally:
                service.close()

    def test_timeout_is_classified_without_provider_text(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cds-service-timeout-") as temporary:
            root = Path(temporary)
            data = Era5DataService(REPO_ROOT, root / "cache")
            fake = root / "validator.py"
            write_fake_validator(fake, sleep_seconds=3)
            service = Era5CredentialValidationService(
                REPO_ROOT, data, validator_path=fake, timeout_seconds=1
            )
            try:
                service.start()
                completed = wait_for_terminal(service, timeout=4)
                self.assertEqual("FAILED", completed["status"])
                self.assertEqual("validation_timeout", completed["code"])
            finally:
                service.close()

    def test_restart_marks_active_validation_interrupted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cds-service-recovery-") as temporary:
            root = Path(temporary)
            data = Era5DataService(REPO_ROOT, root / "cache")
            validation_root = data.cache_root / ".credential-validation"
            job_id = "cds-validation-" + "b" * 16
            job_directory = validation_root / job_id
            job_directory.mkdir(parents=True)
            (validation_root / "latest.json").write_text(
                json.dumps({"job_id": job_id}), encoding="utf-8"
            )
            (job_directory / "state.json").write_text(json.dumps({
                "version": 1,
                "id": job_id,
                "status": "RUNNING",
                "created_at": "2026-07-20T12:00:00Z",
                "started_at": "2026-07-20T12:01:00Z",
                "finished_at": None,
                "pid": 12345,
                "code": None,
                "summary": "running",
                "result": None,
            }), encoding="utf-8")
            service = Era5CredentialValidationService(REPO_ROOT, data)
            try:
                validation = service.status()["validation"]
                self.assertEqual("FAILED", validation["status"])
                self.assertEqual("validation_interrupted", validation["code"])
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
