#!/usr/bin/env python3
"""Hardened public worker for persistent real WPS/WRF simulations.

The private core implements the executor protocol and artifact handling. This
facade adds strict interruption semantics, defensive checksum validation and
portable resource accounting.
"""

from __future__ import annotations

import os
import re
import resource
import signal
import sys
import time
from pathlib import Path
from typing import Any

from workbench._simulation_worker_core import (
    ArtifactResult,
    ExternalStepExecutor as _CoreExternalStepExecutor,
    SimulationWorker as _CoreSimulationWorker,
    StepCancelled,
    StepContext,
    StepExecutionError,
    StepResult,
    _TERMINAL,
    build_parser,
)
from workbench.era5_service import Era5DataService
from workbench.pipeline_specification_service import PipelineSpecificationService
from workbench.simulation_store import SimulationStore, SimulationStoreError

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StepInterrupted(StepExecutionError):
    """The worker stopped independently of a user cancellation request."""

    def __init__(self) -> None:
        super().__init__(
            "worker_interrupted",
            "The simulation worker stopped before the current step reached a checkpoint.",
        )


def _max_rss_bytes(value: Any, *, platform: str | None = None) -> int:
    """Normalize ``ru_maxrss`` to bytes across supported Unix platforms."""

    try:
        measured = max(0, int(value))
    except (TypeError, ValueError):
        return 0
    current_platform = platform or sys.platform
    return measured if current_platform == "darwin" else measured * 1024


class ExternalStepExecutor(_CoreExternalStepExecutor):
    """Core executor with distinct worker-shutdown and user-cancel semantics."""

    def execute(
        self,
        context: StepContext,
        cancellation_requested,
        stopping_requested,
    ) -> StepResult:
        interrupted = False

        def tracked_stopping() -> bool:
            nonlocal interrupted
            stopping = bool(stopping_requested())
            if stopping and not cancellation_requested():
                interrupted = True
            return stopping

        try:
            return super().execute(
                context,
                cancellation_requested,
                tracked_stopping,
            )
        except StepCancelled:
            if interrupted and not cancellation_requested():
                raise StepInterrupted() from None
            raise


class SimulationWorker(_CoreSimulationWorker):
    """Worker with classified interruption and validated immutable input metadata."""

    def execute_job(self, job_id: str) -> dict[str, Any]:
        while True:
            job = self.store.get_job(job_id)
            if job["status"] in _TERMINAL:
                return job
            if job["status"] == "CANCELLING":
                return self.store.finalize_cancel(job_id)
            step_id = job.get("current_step_id")
            step = next(
                (item for item in job["steps"] if item["id"] == step_id), None
            )
            if step is None or step["status"] != "RUNNING":
                return self.store.fail_step(
                    job_id,
                    step_id or "input-data",
                    code="JOB_STATE_INVALID",
                    message="The claimed simulation has no current running step.",
                )
            specification = self.specification_service.get(job["specification_key"])
            run_root = self._run_root(job_id)
            step_root = run_root / "steps" / step_id
            context = StepContext(
                repo_root=self.repo_root,
                run_root=run_root,
                step_root=step_root,
                job=job,
                step=step,
                specification=specification,
                specification_directory=self.specification_service.root
                / job["specification_key"],
                store=self.store,
            )
            started = time.monotonic()
            child_usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
            try:
                result = (
                    self._verify_input(context)
                    if step_id == "input-data"
                    else self.executor.execute(
                        context,
                        lambda: self.store.get_job(job_id)["status"]
                        == "CANCELLING",
                        self._stopping.is_set,
                    )
                )
                if result.progress:
                    self.store.update_step_progress(job_id, step_id, result.progress)
                disk_bytes = self._record_artifacts(context, result.artifacts)
                child_usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
                resources = dict(result.resources)
                resources.setdefault(
                    "cpu_seconds",
                    max(
                        0.0,
                        (child_usage_after.ru_utime + child_usage_after.ru_stime)
                        - (child_usage_before.ru_utime + child_usage_before.ru_stime),
                    ),
                )
                resources.setdefault("wall_seconds", time.monotonic() - started)
                resources.setdefault("disk_bytes", disk_bytes)
                resources.setdefault(
                    "max_rss_bytes", _max_rss_bytes(child_usage_after.ru_maxrss)
                )
                self.store.add_resource_measurement(
                    job_id,
                    step_id=step_id,
                    cpu_seconds=resources.get("cpu_seconds"),
                    wall_seconds=resources.get("wall_seconds"),
                    disk_bytes=resources.get("disk_bytes"),
                    max_rss_bytes=resources.get("max_rss_bytes"),
                    metadata={"worker_id": self.worker_id},
                )
                if self.store.get_job(job_id)["status"] == "CANCELLING":
                    return self.store.finalize_cancel(job_id)
                job = self.store.complete_step(job_id, step_id)
                if job["status"] == "SUCCEEDED":
                    return job
            except StepInterrupted as exc:
                return self.store.fail_step(
                    job_id, step_id, code=exc.code, message=exc.message
                )
            except StepCancelled:
                current = self.store.get_job(job_id)
                if current["status"] != "CANCELLING":
                    self.store.request_cancel(job_id)
                return self.store.finalize_cancel(job_id)
            except StepExecutionError as exc:
                return self.store.fail_step(
                    job_id, step_id, code=exc.code, message=exc.message
                )
            except Exception as exc:  # pragma: no cover - final classification
                return self.store.fail_step(
                    job_id,
                    step_id,
                    code="WORKER_ERROR",
                    message=(
                        f"Simulation worker failed during {step_id} "
                        f"({type(exc).__name__})."
                    ),
                )

    def _verify_input(self, context: StepContext) -> StepResult:
        try:
            files = context.specification["identity"]["era5_input"]["files"]
        except (KeyError, TypeError) as exc:
            raise StepExecutionError(
                "INPUT_DATA_MISSING",
                "The immutable specification has no valid ERA5 file metadata.",
            ) from exc
        if not isinstance(files, list) or not files:
            raise StepExecutionError(
                "INPUT_DATA_MISSING",
                "The immutable specification has no verified ERA5 input files.",
            )
        for metadata in files:
            if not isinstance(metadata, dict):
                raise StepExecutionError(
                    "INPUT_DATA_MISSING", "ERA5 input metadata is invalid."
                )
            digest = metadata.get("sha256")
            size = metadata.get("size_bytes")
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise StepExecutionError(
                    "INPUT_DATA_MISSING", "ERA5 input checksum metadata is invalid."
                )
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise StepExecutionError(
                    "INPUT_DATA_MISSING", "ERA5 input size metadata is invalid."
                )
        try:
            return super()._verify_input(context)
        except StepCancelled:
            current = self.store.get_job(context.job["id"])
            if self._stopping.is_set() and current["status"] != "CANCELLING":
                raise StepInterrupted() from None
            raise


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    data_service = Era5DataService(repo_root)
    specification_service = PipelineSpecificationService(repo_root, data_service)
    store = SimulationStore(repo_root, specification_service)
    configured_executor = args.executor or (
        Path(os.environ["WRF_CHAMMER_SIMULATION_STEP_EXECUTOR"])
        if os.environ.get("WRF_CHAMMER_SIMULATION_STEP_EXECUTOR")
        else None
    )
    worker = SimulationWorker(
        repo_root,
        store,
        data_service,
        specification_service,
        worker_id=args.worker_id,
        executor=ExternalStepExecutor(
            configured_executor,
            poll_seconds=min(args.poll_seconds, 0.25),
            cancel_grace_seconds=args.cancel_grace_seconds,
        ),
        poll_seconds=args.poll_seconds,
    )
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)
    try:
        return worker.run(once=args.once)
    except SimulationStoreError as exc:
        print(f"Simulation worker failed: {exc.message}", file=sys.stderr)
        return 1


__all__ = [
    "ArtifactResult",
    "ExternalStepExecutor",
    "SimulationWorker",
    "StepCancelled",
    "StepContext",
    "StepExecutionError",
    "StepInterrupted",
    "StepResult",
]


if __name__ == "__main__":
    raise SystemExit(main())
