#!/usr/bin/env python3
"""Offline tests for the persistent simulation worker and executor protocol."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

from workbench.era5_service import Era5DataService
from workbench.simulation_store import SimulationStore
from workbench.simulation_worker import ExternalStepExecutor, SimulationWorker

STEP_IDS = (
    "input-data",
    "geogrid",
    "ungrib",
    "metgrid",
    "real",
    "wrf",
    "postprocessing",
    "result-indexing",
)
SPEC_KEY = "a" * 64
PLAN_KEY = "b" * 64


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeSpecificationService:
    def __init__(self, root: Path, specification: dict):
        self.root = root
        self.specification = specification
        directory = root / SPEC_KEY
        directory.mkdir(parents=True)
        (directory / "specification.json").write_text(
            json.dumps(specification), encoding="utf-8"
        )

    def get(self, key: str) -> dict:
        if key != SPEC_KEY:
            raise AssertionError(key)
        return json.loads(json.dumps(self.specification))


def prepare_environment(root: Path) -> tuple[Era5DataService, FakeSpecificationService, SimulationStore]:
    data_service = Era5DataService(root, root / "cache")
    plan_directory = data_service.plan_directory(PLAN_KEY)
    input_file = plan_directory / "files" / "surface.grib"
    input_file.parent.mkdir(parents=True)
    input_file.write_bytes(b"real-era5-worker-input")
    specification = {
        "specification_key": SPEC_KEY,
        "created_at": "2026-07-20T12:00:00Z",
        "immutable": True,
        "execution_started": False,
        "identity": {
            "job": {"id": "xaver-worker", "name": "Xaver worker test"},
            "era5_input": {
                "plan_key": PLAN_KEY,
                "files": [
                    {
                        "path": "files/surface.grib",
                        "sha256": sha256_file(input_file),
                        "size_bytes": input_file.stat().st_size,
                        "request_name": "surface",
                    }
                ],
                "provenance": {
                    "source": "Copernicus Climate Data Store ERA5 reanalysis",
                    "artificial_weather_data": False,
                },
            },
            "runtime": {
                "wps": {"reference": "wps:test", "identity": "sha256:" + "c" * 64},
                "wrf": {"reference": "wrf:test", "identity": "sha256:" + "d" * 64},
                "postprocessing": {
                    "reference": "postprocess:test",
                    "identity": "sha256:" + "e" * 64,
                },
            },
            "steps": [
                {
                    "id": step_id,
                    "label": step_id.replace("-", " ").title(),
                    "status": "PENDING",
                    "inputs": [],
                    "outputs": [],
                    "progress_metrics": [],
                }
                for step_id in STEP_IDS
            ],
        },
    }
    specification_service = FakeSpecificationService(
        root / "specifications", specification
    )
    store = SimulationStore(
        root,
        specification_service,  # type: ignore[arg-type]
        database_path=root / "state" / "simulations.sqlite3",
    )
    return data_service, specification_service, store


def write_success_executor(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            """
            import argparse
            import json
            import os
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument('--step', required=True)
            parser.add_argument('--job-id', required=True)
            parser.add_argument('--specification-key', required=True)
            parser.add_argument('--specification-directory', required=True)
            parser.add_argument('--run-directory', required=True)
            parser.add_argument('--step-directory', required=True)
            parser.add_argument('--result', required=True)
            parser.add_argument('--progress', required=True)
            args = parser.parse_args()
            if os.environ.get('CDSAPI_KEY'):
                raise SystemExit(91)
            run = Path(args.run_directory)
            step = Path(args.step_directory)
            step.mkdir(parents=True, exist_ok=True)
            Path(args.progress).write_text(json.dumps({'phase': 'executing', 'percent': 50}), encoding='utf-8')
            output = step / f'{args.step}-output.dat'
            output.write_text(f'real-executor-protocol-output:{args.step}', encoding='utf-8')
            relative = output.relative_to(run).as_posix()
            Path(args.result).write_text(json.dumps({
                'status': 'SUCCEEDED',
                'progress': {'phase': 'completed', 'percent': 100},
                'artifacts': [{'path': relative, 'kind': f'{args.step}-output'}],
                'resources': {'cpu_seconds': 0.01}
            }), encoding='utf-8')
            """
        ),
        encoding="utf-8",
    )


def write_blocking_executor(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            """
            import argparse
            import json
            import time
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument('--step', required=True)
            parser.add_argument('--job-id', required=True)
            parser.add_argument('--specification-key', required=True)
            parser.add_argument('--specification-directory', required=True)
            parser.add_argument('--run-directory', required=True)
            parser.add_argument('--step-directory', required=True)
            parser.add_argument('--result', required=True)
            parser.add_argument('--progress', required=True)
            args = parser.parse_args()
            Path(args.progress).write_text(json.dumps({'phase': 'blocked'}), encoding='utf-8')
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )


class SimulationWorkerTests(unittest.TestCase):
    def test_real_input_is_verified_then_missing_executor_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-worker-input-") as temporary:
            root = Path(temporary)
            data, specifications, store = prepare_environment(root)
            job = store.create_job(SPEC_KEY)
            store.enqueue_job(job["id"])
            worker = SimulationWorker(
                root,
                store,
                data,
                specifications,  # type: ignore[arg-type]
                worker_id="worker-no-executor",
                executor=ExternalStepExecutor(None),
                poll_seconds=0.05,
            )
            worker.run(once=True)
            result = store.get_job(job["id"])
            self.assertEqual("FAILED", result["status"])
            self.assertEqual("EXECUTOR_UNAVAILABLE", result["error"]["code"])
            steps = {step["id"]: step for step in result["steps"]}
            self.assertEqual("SUCCEEDED", steps["input-data"]["status"])
            self.assertEqual("FAILED", steps["geogrid"]["status"])
            self.assertEqual(1, len(result["artifacts"]))
            self.assertEqual("verified-input-set", result["artifacts"][0]["kind"])
            self.assertGreaterEqual(len(result["resource_measurements"]), 1)

    def test_executor_protocol_completes_all_real_step_contracts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-worker-success-") as temporary:
            root = Path(temporary)
            data, specifications, store = prepare_environment(root)
            executor_path = root / "success-executor.py"
            write_success_executor(executor_path)
            previous_secret = os.environ.get("CDSAPI_KEY")
            os.environ["CDSAPI_KEY"] = "SECRET-MUST-NOT-REACH-STEP"
            try:
                job = store.create_job(SPEC_KEY)
                store.enqueue_job(job["id"])
                worker = SimulationWorker(
                    root,
                    store,
                    data,
                    specifications,  # type: ignore[arg-type]
                    worker_id="worker-success",
                    executor=ExternalStepExecutor(
                        executor_path, poll_seconds=0.02, cancel_grace_seconds=0.2
                    ),
                    poll_seconds=0.02,
                )
                worker.run(once=True)
            finally:
                if previous_secret is None:
                    os.environ.pop("CDSAPI_KEY", None)
                else:
                    os.environ["CDSAPI_KEY"] = previous_secret
            result = store.get_job(job["id"])
            self.assertEqual("SUCCEEDED", result["status"])
            self.assertEqual({"SUCCEEDED"}, {step["status"] for step in result["steps"]})
            self.assertGreaterEqual(len(result["artifacts"]), 15)
            measurements = result["resource_measurements"]
            self.assertEqual(9, len(measurements))
            preflight = [
                measurement
                for measurement in measurements
                if measurement["metadata"].get("phase") == "preflight"
            ]
            self.assertEqual(1, len(preflight))
            self.assertEqual(
                set(STEP_IDS),
                {
                    measurement["step_id"]
                    for measurement in measurements
                    if measurement["metadata"].get("phase") != "preflight"
                },
            )
            self.assertTrue(
                any(event["type"] == "job_succeeded" for event in result["events"])
            )

    def test_cancellation_stops_executor_process_group(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-worker-cancel-") as temporary:
            root = Path(temporary)
            data, specifications, store = prepare_environment(root)
            executor_path = root / "blocking-executor.py"
            write_blocking_executor(executor_path)
            job = store.create_job(SPEC_KEY)
            store.enqueue_job(job["id"])
            worker = SimulationWorker(
                root,
                store,
                data,
                specifications,  # type: ignore[arg-type]
                worker_id="worker-cancel",
                executor=ExternalStepExecutor(
                    executor_path, poll_seconds=0.02, cancel_grace_seconds=0.15
                ),
                poll_seconds=0.02,
            )
            thread = threading.Thread(target=lambda: worker.run(once=True))
            thread.start()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = store.get_job(job["id"])
                if current["current_step_id"] == "geogrid" and current["status"] == "PREPROCESSING":
                    break
                time.sleep(0.02)
            else:
                self.fail(f"geogrid executor did not start: {store.get_job(job['id'])}")
            store.request_cancel(job["id"])
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            result = store.get_job(job["id"])
            self.assertEqual("CANCELLED", result["status"])
            self.assertIsNone(result["worker_id"])
            self.assertIsNone(result["current_step_id"])


if __name__ == "__main__":
    unittest.main()
