#!/usr/bin/env python3
"""Filesystem and stale-state regressions for the WPS container executor."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import wps_container_executor
from workbench.server.tests.test_wps_container_executor import (
    IMAGE_ID,
    PLAN_KEY,
    SPEC_KEY,
    prepare,
)


def write_no_result_engine(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            """
            import json
            import os
            import sys

            args = sys.argv[1:]
            if args[:2] == ['image', 'inspect']:
                print(json.dumps([{'Id': os.environ['FAKE_IMAGE_ID'], 'RepoDigests': []}]))
                raise SystemExit(0)
            if args and args[0] == 'run':
                raise SystemExit(0)
            raise SystemExit(2)
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def arguments(
    specification: Path,
    run: Path,
    step: Path,
    result: Path,
    progress: Path,
) -> list[str]:
    return [
        "--step", "ungrib",
        "--job-id", "job-1",
        "--specification-key", SPEC_KEY,
        "--specification-directory", str(specification),
        "--run-directory", str(run),
        "--step-directory", str(step),
        "--result", str(result),
        "--progress", str(progress),
    ]


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
class WpsContainerExecutorSafetyTests(unittest.TestCase):
    def test_stale_success_is_removed_before_container_start(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-container-stale-") as temporary:
            root = Path(temporary)
            engine = root / "no-result-engine.py"
            write_no_result_engine(engine)
            specification, cache, run, step, result, progress = prepare(root)
            result.write_text(json.dumps({"status": "SUCCEEDED"}), encoding="utf-8")
            progress.write_text(json.dumps({"phase": "old"}), encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "WRF_CHAMMER_CONTAINER_ENGINE": str(engine),
                    "WRF_CHAMMER_ERA5_CACHE_ROOT": str(cache),
                    "FAKE_IMAGE_ID": IMAGE_ID,
                },
                clear=False,
            ):
                exit_code = wps_container_executor.main(
                    arguments(specification, run, step, result, progress)
                )
            self.assertEqual(1, exit_code)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual("FAILED", payload["status"])
            self.assertEqual("PROCESS_CRASH", payload["error"]["code"])
            self.assertNotEqual("old", json.loads(progress.read_text())["phase"])

    def test_symlinked_specification_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-container-spec-link-") as temporary:
            root = Path(temporary)
            specification, cache, run, step, result, progress = prepare(root)
            actual = root / "actual-specification"
            specification.rename(actual)
            specification.symlink_to(actual, target_is_directory=True)
            with patch.dict(
                os.environ,
                {"WRF_CHAMMER_ERA5_CACHE_ROOT": str(cache)},
                clear=False,
            ):
                exit_code = wps_container_executor.main(
                    arguments(specification, run, step, result, progress)
                )
            self.assertEqual(1, exit_code)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual("NAMELIST_INVALID", payload["error"]["code"])

    def test_symlinked_era5_plan_is_rejected_before_engine_start(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-container-plan-link-") as temporary:
            root = Path(temporary)
            specification, cache, run, step, result, progress = prepare(root)
            plan = cache / PLAN_KEY
            actual = root / "actual-plan"
            plan.rename(actual)
            plan.symlink_to(actual, target_is_directory=True)
            with patch.dict(
                os.environ,
                {"WRF_CHAMMER_ERA5_CACHE_ROOT": str(cache)},
                clear=False,
            ):
                exit_code = wps_container_executor.main(
                    arguments(specification, run, step, result, progress)
                )
            self.assertEqual(1, exit_code)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual("EXECUTOR_OUTPUT_INVALID", payload["error"]["code"])


if __name__ == "__main__":
    unittest.main()
