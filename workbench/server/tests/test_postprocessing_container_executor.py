#!/usr/bin/env python3
"""Pinned postprocessing container and completed dispatcher routing tests."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import pipeline_container_executor, postprocessing_container_executor

SPEC_KEY = "a" * 64
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
                    "era5_input": {
                        "provenance": {"artificial_weather_data": False}
                    },
                    "runtime": {
                        "postprocessing": {
                            "reference": "example/postprocess:test",
                            "identity": IMAGE_ID,
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    run = root / "runs" / "job"
    step = run / "steps" / "postprocessing"
    step.mkdir(parents=True)
    return specification, run, step, step / "result.json", step / "progress.json"


def arguments(
    specification: Path,
    run: Path,
    step: Path,
    result: Path,
    progress: Path,
    *,
    pipeline_step: str = "postprocessing",
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


class PostprocessingContainerExecutorTests(unittest.TestCase):
    def test_pinned_postprocessing_image_and_sandbox_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postprocessing-container-") as temporary:
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
                exit_code = postprocessing_container_executor.main(
                    arguments(specification, run, step, result, progress)
                )
            self.assertEqual(0, exit_code)
            calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                ["image", "inspect", "example/postprocess:test"], calls[0]
            )
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

    def test_dispatcher_routes_both_final_steps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="postprocessing-routing-") as temporary:
            result = Path(temporary) / "result.json"
            for step in ("postprocessing", "result-indexing"):
                forwarded = [
                    "--job-id",
                    "job",
                    "--step",
                    step,
                    "--result",
                    str(result),
                ]
                with patch.object(
                    pipeline_container_executor.postprocessing_container_executor,
                    "main",
                    return_value=17,
                ) as executor:
                    self.assertEqual(
                        17, pipeline_container_executor.main(forwarded)
                    )
                    executor.assert_called_once_with(forwarded)


if __name__ == "__main__":
    unittest.main()
