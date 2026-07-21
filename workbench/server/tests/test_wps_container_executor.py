#!/usr/bin/env python3
"""Offline command-contract tests for the pinned WPS container executor."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import wps_container_executor

SPEC_KEY = "a" * 64
PLAN_KEY = "b" * 64
IMAGE_ID = "sha256:" + "c" * 64


def write_fake_engine(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            """
            import json
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            log = Path(os.environ['FAKE_ENGINE_LOG'])
            with log.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(args) + '\\n')
            if args[:2] == ['image', 'inspect']:
                print(json.dumps([{
                    'Id': os.environ['FAKE_IMAGE_ID'],
                    'RepoDigests': ['example/wps@' + os.environ['FAKE_REPO_DIGEST']]
                }]))
                raise SystemExit(0)
            if args and args[0] == 'run':
                run_host = None
                for index, value in enumerate(args):
                    if value == '-v':
                        host, destination, _mode = args[index + 1].rsplit(':', 2)
                        if destination == '/run':
                            run_host = Path(host)
                assert run_host is not None
                result = run_host / args[args.index('--result') + 1].removeprefix('/run/')
                progress = run_host / args[args.index('--progress') + 1].removeprefix('/run/')
                result.parent.mkdir(parents=True, exist_ok=True)
                progress.parent.mkdir(parents=True, exist_ok=True)
                result.write_text(json.dumps({
                    'status': 'SUCCEEDED',
                    'progress': {'phase': 'done'},
                    'artifacts': []
                }), encoding='utf-8')
                progress.write_text(json.dumps({'phase': 'done'}), encoding='utf-8')
                raise SystemExit(0)
            raise SystemExit(2)
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def prepare(root: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    specification_directory = root / "specifications" / SPEC_KEY
    specification_directory.mkdir(parents=True)
    (specification_directory / "run-specification.json").write_text(
        json.dumps(
            {
                "specification_key": SPEC_KEY,
                "immutable": True,
                "execution_started": False,
                "identity": {
                    "runtime": {
                        "wps": {
                            "reference": "example/wps:test",
                            "identity": IMAGE_ID,
                        }
                    },
                    "era5_input": {
                        "plan_key": PLAN_KEY,
                        "files": [
                            {
                                "path": "files/surface.grib",
                                "sha256": "d" * 64,
                                "size_bytes": 4,
                                "request_name": "single_levels_test",
                            }
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (specification_directory / "namelist.wps").write_text(
        "&ungrib\n prefix = 'FILE',\n/\n&metgrid\n fg_name = 'FILE',\n/\n",
        encoding="utf-8",
    )
    cache_root = root / "cache"
    input_file = cache_root / PLAN_KEY / "files" / "surface.grib"
    input_file.parent.mkdir(parents=True)
    input_file.write_bytes(b"data")
    run_directory = root / "runs" / "job"
    step_directory = run_directory / "steps" / "ungrib"
    step_directory.mkdir(parents=True)
    return (
        specification_directory,
        cache_root,
        run_directory,
        step_directory,
        step_directory / "result.json",
        step_directory / "progress.json",
    )


def executor_args(
    specification: Path,
    run: Path,
    step: Path,
    result: Path,
    progress: Path,
    *,
    pipeline_step: str = "ungrib",
) -> list[str]:
    return [
        "--step",
        pipeline_step,
        "--job-id",
        "job-1",
        "--specification-key",
        SPEC_KEY,
        "--specification-directory",
        str(specification),
        "--run-directory",
        str(run),
        "--step-directory",
        str(step),
        "--result",
        str(result),
        "--progress",
        str(progress),
    ]


class WpsContainerExecutorTests(unittest.TestCase):
    def test_pinned_image_and_isolation_flags_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-container-executor-") as temporary:
            root = Path(temporary)
            engine = root / "fake-engine.py"
            log = root / "engine.log"
            write_fake_engine(engine)
            specification, cache, run, step, result, progress = prepare(root)
            with patch.dict(
                os.environ,
                {
                    "WRF_CHAMMER_CONTAINER_ENGINE": str(engine),
                    "WRF_CHAMMER_ERA5_CACHE_ROOT": str(cache),
                    "FAKE_ENGINE_LOG": str(log),
                    "FAKE_IMAGE_ID": IMAGE_ID,
                    "FAKE_REPO_DIGEST": IMAGE_ID,
                },
                clear=False,
            ):
                exit_code = wps_container_executor.main(
                    executor_args(specification, run, step, result, progress)
                )
            self.assertEqual(0, exit_code)
            calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(["image", "inspect", "example/wps:test"], calls[0])
            command = calls[1]
            for flag in (
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
            ):
                self.assertIn(flag, command)
            self.assertNotIn("--security-opt=no-new-privileges", command)
            self.assertIn(IMAGE_ID, command)
            self.assertIn("/spec/run-specification.json", command)
            self.assertNotIn("/spec/specification.json", command)
            mounts = [
                command[index + 1]
                for index, value in enumerate(command)
                if value == "-v"
            ]
            self.assertTrue(any(mount.endswith(":/spec:ro") for mount in mounts))
            self.assertTrue(any(mount.endswith(":/era5:ro") for mount in mounts))
            self.assertTrue(any(mount.endswith(":/run:rw") for mount in mounts))
            self.assertEqual("SUCCEEDED", json.loads(result.read_text())["status"])

    def test_image_mismatch_is_classified_before_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-container-mismatch-") as temporary:
            root = Path(temporary)
            engine = root / "fake-engine.py"
            log = root / "engine.log"
            write_fake_engine(engine)
            specification, cache, run, step, result, progress = prepare(root)
            wrong = "sha256:" + "e" * 64
            with patch.dict(
                os.environ,
                {
                    "WRF_CHAMMER_CONTAINER_ENGINE": str(engine),
                    "WRF_CHAMMER_ERA5_CACHE_ROOT": str(cache),
                    "FAKE_ENGINE_LOG": str(log),
                    "FAKE_IMAGE_ID": wrong,
                    "FAKE_REPO_DIGEST": wrong,
                },
                clear=False,
            ):
                exit_code = wps_container_executor.main(
                    executor_args(specification, run, step, result, progress)
                )
            self.assertEqual(1, exit_code)
            self.assertEqual(
                "RUNTIME_IMAGE_MISMATCH",
                json.loads(result.read_text(encoding="utf-8"))["error"]["code"],
            )
            calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(1, len(calls))

    def test_non_wps_step_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wps-container-unsupported-") as temporary:
            root = Path(temporary)
            specification, cache, run, step, result, progress = prepare(root)
            with patch.dict(
                os.environ,
                {"WRF_CHAMMER_ERA5_CACHE_ROOT": str(cache)},
                clear=False,
            ):
                exit_code = wps_container_executor.main(
                    executor_args(
                        specification,
                        run,
                        step,
                        result,
                        progress,
                        pipeline_step="real",
                    )
                )
            self.assertEqual(1, exit_code)
            self.assertEqual(
                "EXECUTOR_UNAVAILABLE",
                json.loads(result.read_text(encoding="utf-8"))["error"]["code"],
            )


if __name__ == "__main__":
    unittest.main()
