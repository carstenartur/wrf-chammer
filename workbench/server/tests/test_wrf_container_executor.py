#!/usr/bin/env python3
"""Command and routing tests for pinned WRF container execution."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import pipeline_container_executor, wrf_container_executor

SPEC_KEY = "a" * 64
IMAGE_ID = "sha256:" + "b" * 64


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
                print(json.dumps([{'Id': os.environ['FAKE_IMAGE_ID'], 'RepoDigests': []}]))
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


def prepare(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    specification = root / "specifications" / SPEC_KEY
    specification.mkdir(parents=True)
    (specification / "run-specification.json").write_text(
        json.dumps(
            {
                "specification_key": SPEC_KEY,
                "immutable": True,
                "execution_started": False,
                "identity": {
                    "job": {
                        "period": {
                            "start": "2013-12-05T12:00:00Z",
                            "end": "2013-12-05T18:00:00Z",
                        }
                    },
                    "runtime": {
                        "wrf": {
                            "reference": "example/wrf:test",
                            "identity": IMAGE_ID,
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (specification / "namelist.input").write_text(
        "&time_control\n/\n", encoding="utf-8"
    )
    run = root / "runs" / "job"
    step = run / "steps" / "real"
    step.mkdir(parents=True)
    return specification, run, step, step / "result.json", step / "progress.json"


def arguments(
    specification: Path,
    run: Path,
    step: Path,
    result: Path,
    progress: Path,
    *,
    pipeline_step: str = "real",
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


class WrfContainerExecutorTests(unittest.TestCase):
    def test_pinned_wrf_image_and_sandbox_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wrf-container-executor-") as temporary:
            root = Path(temporary)
            engine = root / "fake-engine.py"
            log = root / "engine.log"
            write_fake_engine(engine)
            specification, run, step, result, progress = prepare(root)
            with patch.dict(
                os.environ,
                {
                    "WRF_CHAMMER_CONTAINER_ENGINE": str(engine),
                    "FAKE_ENGINE_LOG": str(log),
                    "FAKE_IMAGE_ID": IMAGE_ID,
                },
                clear=False,
            ):
                exit_code = wrf_container_executor.main(
                    arguments(specification, run, step, result, progress)
                )
            self.assertEqual(0, exit_code)
            calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(["image", "inspect", "example/wrf:test"], calls[0])
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
            self.assertTrue(any(mount.endswith(":/run:rw") for mount in mounts))
            self.assertEqual("SUCCEEDED", json.loads(result.read_text())["status"])

    def test_mount_targets_and_modes_are_validated(self) -> None:
        source = Path("/tmp")
        self.assertEqual(
            f"{source.resolve()}:/run:rw",
            wrf_container_executor.mount_argument(source, "/run", "rw"),
        )
        for destination, mode in (
            ("relative", "ro"),
            ("/run:escape", "ro"),
            ("/run", "invalid"),
        ):
            with self.subTest(destination=destination, mode=mode):
                with self.assertRaises(AssertionError):
                    wrf_container_executor.mount_argument(source, destination, mode)

    def test_raw_container_output_is_discarded(self) -> None:
        completed = subprocess.CompletedProcess(["docker", "run"], 0)
        with patch.object(
            wrf_container_executor.subprocess,
            "run",
            return_value=completed,
        ) as run:
            actual = wrf_container_executor.run_container(["docker", "run"])
        self.assertIs(completed, actual)
        run.assert_called_once()
        kwargs = run.call_args.kwargs
        self.assertIs(subprocess.DEVNULL, kwargs["stdin"])
        self.assertIs(subprocess.DEVNULL, kwargs["stdout"])
        self.assertIs(subprocess.DEVNULL, kwargs["stderr"])
        self.assertFalse(kwargs["check"])

    def test_pipeline_dispatcher_forwards_original_arguments_exactly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pipeline-container-routing-") as temporary:
            result = Path(temporary) / "result.json"
            wps_arguments = [
                "--job-id",
                "job",
                "--result",
                str(result),
                "--step",
                "geogrid",
                "--progress",
                str(Path(temporary) / "progress.json"),
            ]
            with patch.object(
                pipeline_container_executor.wps_container_executor,
                "main",
                return_value=11,
            ) as wps:
                self.assertEqual(11, pipeline_container_executor.main(wps_arguments))
                wps.assert_called_once_with(wps_arguments)

            wrf_arguments = [
                "--progress",
                str(Path(temporary) / "progress.json"),
                "--step",
                "real",
                "--job-id",
                "job",
                "--result",
                str(result),
            ]
            with patch.object(
                pipeline_container_executor.wrf_container_executor,
                "main",
                return_value=12,
            ) as wrf:
                self.assertEqual(12, pipeline_container_executor.main(wrf_arguments))
                wrf.assert_called_once_with(wrf_arguments)

            self.assertEqual(
                1,
                pipeline_container_executor.main(
                    ["--step", "postprocessing", "--result", str(result)]
                ),
            )
            self.assertEqual(
                "EXECUTOR_UNAVAILABLE",
                json.loads(result.read_text(encoding="utf-8"))["error"]["code"],
            )


if __name__ == "__main__":
    unittest.main()
