#!/usr/bin/env python3
"""Standalone worker for persistent real WPS/WRF simulation jobs.

The product path performs real ERA5 integrity verification itself. Remaining
pipeline steps require an explicit local executor program; missing executors
fail clearly instead of producing fixture or synthetic model output.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import resource
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from workbench.era5_service import Era5DataService
from workbench.pipeline_specification_service import PipelineSpecificationService
from workbench.simulation_store import SimulationStore, SimulationStoreError

_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
_SECRET_ENVIRONMENT_NAMES = {
    "CDSAPI_KEY",
    "CDSAPI_URL",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
}


class StepExecutionError(RuntimeError):
    """Classified executor failure safe for persistent state and API display."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class StepCancelled(RuntimeError):
    pass


@dataclass
class ArtifactResult:
    path: Path
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)
    expected_sha256: str | None = None


@dataclass
class StepResult:
    artifacts: list[ArtifactResult] = field(default_factory=list)
    progress: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepContext:
    repo_root: Path
    run_root: Path
    step_root: Path
    job: dict[str, Any]
    step: dict[str, Any]
    specification: dict[str, Any]
    specification_directory: Path
    store: SimulationStore


class ExternalStepExecutor:
    """Run one configured executor program using a file-based local protocol."""

    def __init__(
        self,
        program: Path | None,
        *,
        poll_seconds: float = 0.2,
        cancel_grace_seconds: float = 8.0,
    ):
        self.program = program.resolve() if program is not None else None
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.cancel_grace_seconds = max(0.1, float(cancel_grace_seconds))

    def execute(
        self,
        context: StepContext,
        cancellation_requested: Callable[[], bool],
        stopping_requested: Callable[[], bool],
    ) -> StepResult:
        if self.program is None or not self.program.is_file():
            raise StepExecutionError(
                "EXECUTOR_UNAVAILABLE",
                f"No real executor is configured for pipeline step {context.step['id']}.",
            )
        context.step_root.mkdir(parents=True, exist_ok=True)
        result_path = context.step_root / "executor-result.json"
        progress_path = context.step_root / "executor-progress.json"
        log_path = context.step_root / "executor.log"
        command = self._command(context, result_path, progress_path)
        environment = os.environ.copy()
        for name in _SECRET_ENVIRONMENT_NAMES:
            environment.pop(name, None)
        with log_path.open("w", encoding="utf-8") as log:
            log.write(
                f"Starting configured executor {self.program.name} for step {context.step['id']}.\n"
            )
            log.flush()
            process = subprocess.Popen(
                command,
                cwd=context.repo_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=(os.name == "posix"),
            )
            cancellation_started: float | None = None
            progress_stamp: int | None = None
            while process.poll() is None:
                progress_stamp = self._publish_progress(
                    context, progress_path, progress_stamp
                )
                if cancellation_requested() or stopping_requested():
                    if cancellation_started is None:
                        cancellation_started = time.monotonic()
                        self._signal_process(process, signal.SIGTERM)
                    elif (
                        time.monotonic() - cancellation_started
                        >= self.cancel_grace_seconds
                    ):
                        self._signal_process(process, signal.SIGKILL)
                time.sleep(self.poll_seconds)
            exit_code = process.wait()
        self._publish_progress(context, progress_path, progress_stamp)
        log_artifact = ArtifactResult(log_path, "step-log")
        if cancellation_started is not None:
            raise StepCancelled()
        result = self._load_result(result_path)
        if exit_code != 0 or result.get("status") != "SUCCEEDED":
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            code = error.get("code") if isinstance(error.get("code"), str) else "PROCESS_CRASH"
            message = (
                error.get("message")
                if isinstance(error.get("message"), str)
                else f"Configured executor exited with code {exit_code}."
            )
            raise StepExecutionError(code, message)
        artifacts = [log_artifact]
        raw_artifacts = result.get("artifacts", [])
        if not isinstance(raw_artifacts, list):
            raise StepExecutionError(
                "EXECUTOR_OUTPUT_INVALID",
                "The configured executor returned an invalid artifact list.",
            )
        for raw in raw_artifacts:
            if not isinstance(raw, dict):
                raise StepExecutionError(
                    "EXECUTOR_OUTPUT_INVALID",
                    "The configured executor returned an invalid artifact entry.",
                )
            relative = raw.get("path")
            kind = raw.get("kind")
            if not isinstance(relative, str) or not isinstance(kind, str):
                raise StepExecutionError(
                    "EXECUTOR_OUTPUT_INVALID",
                    "Executor artifacts require path and kind strings.",
                )
            artifact_path = self._contained_artifact(context.run_root, relative)
            expected = raw.get("sha256")
            artifacts.append(
                ArtifactResult(
                    artifact_path,
                    kind,
                    raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
                    expected if isinstance(expected, str) else None,
                )
            )
        progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
        resources = result.get("resources") if isinstance(result.get("resources"), dict) else {}
        return StepResult(artifacts=artifacts, progress=progress, resources=resources)

    def _command(
        self, context: StepContext, result_path: Path, progress_path: Path
    ) -> list[str]:
        prefix = [sys.executable, str(self.program)] if self.program.suffix == ".py" else [str(self.program)]
        return prefix + [
            "--step",
            str(context.step["id"]),
            "--job-id",
            str(context.job["id"]),
            "--specification-key",
            str(context.job["specification_key"]),
            "--specification-directory",
            str(context.specification_directory),
            "--run-directory",
            str(context.run_root),
            "--step-directory",
            str(context.step_root),
            "--result",
            str(result_path),
            "--progress",
            str(progress_path),
        ]

    @staticmethod
    def _load_result(path: Path) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _publish_progress(
        context: StepContext, path: Path, previous_stamp: int | None
    ) -> int | None:
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return previous_stamp
        if previous_stamp == stamp or path.is_symlink():
            return stamp
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return stamp
        if isinstance(payload, dict):
            context.store.update_step_progress(
                context.job["id"], context.step["id"], payload
            )
        return stamp

    @staticmethod
    def _contained_artifact(run_root: Path, relative: str) -> Path:
        if "\\" in relative:
            raise StepExecutionError(
                "EXECUTOR_OUTPUT_INVALID", "Executor artifact paths must use POSIX separators."
            )
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise StepExecutionError(
                "EXECUTOR_OUTPUT_INVALID", "Executor artifact path is unsafe."
            )
        candidate = (run_root / Path(*pure.parts)).resolve()
        root = run_root.resolve()
        if candidate == root or root not in candidate.parents:
            raise StepExecutionError(
                "EXECUTOR_OUTPUT_INVALID", "Executor artifact escaped the managed run directory."
            )
        if candidate.is_symlink() or not candidate.is_file():
            raise StepExecutionError(
                "EXECUTOR_OUTPUT_INVALID", "Executor artifact is missing or unsafe."
            )
        return candidate

    @staticmethod
    def _signal_process(process: subprocess.Popen[str], sig: signal.Signals) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            else:
                process.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass


class SimulationWorker:
    """Claim queued simulations and execute their frozen step contracts."""

    def __init__(
        self,
        repo_root: Path,
        store: SimulationStore,
        data_service: Era5DataService,
        specification_service: PipelineSpecificationService,
        *,
        worker_id: str | None = None,
        executor: ExternalStepExecutor | None = None,
        poll_seconds: float = 1.0,
    ):
        self.repo_root = repo_root.resolve()
        self.store = store
        self.data_service = data_service
        self.specification_service = specification_service
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self.executor = executor or ExternalStepExecutor(None)
        self.poll_seconds = max(0.05, float(poll_seconds))
        self._stopping = threading.Event()

    def request_stop(self, *_args: Any) -> None:
        self._stopping.set()

    def run(self, *, once: bool = False) -> int:
        self.store.recover_interrupted_jobs()
        while not self._stopping.is_set():
            job = self.store.claim_next_job(self.worker_id)
            if job is None:
                if once:
                    return 0
                self._stopping.wait(self.poll_seconds)
                continue
            self.execute_job(job["id"])
            if once:
                return 0
        return 0

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
                        lambda: self.store.get_job(job_id)["status"] == "CANCELLING",
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
                    "max_rss_bytes", max(0, int(child_usage_after.ru_maxrss) * 1024)
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
            except StepCancelled:
                current = self.store.get_job(job_id)
                if current["status"] != "CANCELLING":
                    self.store.request_cancel(job_id)
                return self.store.finalize_cancel(job_id)
            except StepExecutionError as exc:
                return self.store.fail_step(
                    job_id, step_id, code=exc.code, message=exc.message
                )
            except Exception as exc:  # pragma: no cover - last-resort classification
                return self.store.fail_step(
                    job_id,
                    step_id,
                    code="WORKER_ERROR",
                    message=f"Simulation worker failed during {step_id} ({type(exc).__name__}).",
                )

    def _verify_input(self, context: StepContext) -> StepResult:
        identity = context.specification["identity"]
        era5_input = identity["era5_input"]
        plan_key = era5_input["plan_key"]
        plan_directory = self.data_service.plan_directory(plan_key).resolve()
        files = era5_input["files"]
        verified: list[dict[str, Any]] = []
        total_bytes = 0
        for index, metadata in enumerate(files, start=1):
            if self._stopping.is_set() or self.store.get_job(context.job["id"])[
                "status"
            ] == "CANCELLING":
                raise StepCancelled()
            relative = metadata.get("path")
            digest = metadata.get("sha256")
            size = metadata.get("size_bytes")
            target = self._input_path(plan_directory, relative)
            if target.stat().st_size != size:
                raise StepExecutionError(
                    "INPUT_DATA_MISSING",
                    f"ERA5 input size does not match for {relative}.",
                )
            actual_digest = self._sha256_file(target)
            if not hmac.compare_digest(actual_digest, digest):
                raise StepExecutionError(
                    "INPUT_DATA_MISSING",
                    f"ERA5 input checksum does not match for {relative}.",
                )
            total_bytes += size
            verified.append(
                {
                    "path": relative,
                    "sha256": actual_digest,
                    "size_bytes": size,
                    "request_name": metadata.get("request_name"),
                }
            )
            self.store.update_step_progress(
                context.job["id"],
                context.step["id"],
                {
                    "verified_files": index,
                    "total_files": len(files),
                    "verified_bytes": total_bytes,
                },
            )
        context.step_root.mkdir(parents=True, exist_ok=True)
        manifest_path = context.step_root / "verified-input.json"
        self._atomic_json(
            manifest_path,
            {
                "version": 1,
                "plan_key": plan_key,
                "verified_files": verified,
                "verified_bytes": total_bytes,
                "artificial_weather_data": False,
            },
        )
        return StepResult(
            artifacts=[ArtifactResult(manifest_path, "verified-input-set")],
            progress={
                "verified_files": len(files),
                "total_files": len(files),
                "verified_bytes": total_bytes,
            },
            resources={"disk_bytes": manifest_path.stat().st_size},
        )

    def _run_root(self, job_id: str) -> Path:
        root = (self.repo_root / "workbench-runs" / "simulations").resolve()
        root.mkdir(parents=True, exist_ok=True)
        candidate = (root / job_id).resolve()
        if candidate.parent != root:
            raise StepExecutionError(
                "JOB_STATE_INVALID", "Simulation run directory escaped its managed root."
            )
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    @staticmethod
    def _input_path(plan_directory: Path, relative: Any) -> Path:
        if not isinstance(relative, str) or "\\" in relative:
            raise StepExecutionError(
                "INPUT_DATA_MISSING", "ERA5 input path is invalid."
            )
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise StepExecutionError(
                "INPUT_DATA_MISSING", "ERA5 input path is unsafe."
            )
        candidate = (plan_directory / Path(*pure.parts)).resolve()
        if candidate == plan_directory or plan_directory not in candidate.parents:
            raise StepExecutionError(
                "INPUT_DATA_MISSING", "ERA5 input escaped the managed plan directory."
            )
        if candidate.is_symlink() or not candidate.is_file():
            raise StepExecutionError(
                "INPUT_DATA_MISSING", f"ERA5 input file is missing: {relative}."
            )
        return candidate

    def _record_artifacts(
        self, context: StepContext, artifacts: list[ArtifactResult]
    ) -> int:
        total = 0
        run_root = context.run_root.resolve()
        for artifact in artifacts:
            path = artifact.path.resolve()
            if path == run_root or run_root not in path.parents:
                raise StepExecutionError(
                    "EXECUTOR_OUTPUT_INVALID",
                    "Step artifact escaped the managed simulation directory.",
                )
            if path.is_symlink() or not path.is_file():
                raise StepExecutionError(
                    "EXECUTOR_OUTPUT_INVALID", "Step artifact is missing or unsafe."
                )
            digest = self._sha256_file(path)
            if artifact.expected_sha256 and not hmac.compare_digest(
                digest, artifact.expected_sha256
            ):
                raise StepExecutionError(
                    "EXECUTOR_OUTPUT_INVALID", "Step artifact checksum does not match."
                )
            size = path.stat().st_size
            total += size
            self.store.add_artifact(
                context.job["id"],
                step_id=context.step["id"],
                kind=artifact.kind,
                relative_path=path.relative_to(run_root).as_posix(),
                sha256=digest,
                size_bytes=size,
                metadata=artifact.metadata,
            )
        return total

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the persistent real-simulation worker"
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--worker-id")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--cancel-grace-seconds", type=float, default=8.0)
    parser.add_argument(
        "--executor",
        type=Path,
        help="Local executor program for non-input pipeline steps.",
    )
    return parser


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


if __name__ == "__main__":
    raise SystemExit(main())
