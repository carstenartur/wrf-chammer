#!/usr/bin/env python3
"""Standalone persistent worker for WRF Workbench jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.job_store import JobStore, JobStoreError  # noqa: E402

DEFAULT_POLL_SECONDS = 1.0
DEFAULT_CANCEL_GRACE_SECONDS = 8.0


def _is_under(path: Path, base: Path) -> bool:
    try:
        return os.path.commonpath([str(path.resolve()), str(base.resolve())]) == str(base.resolve())
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class JobWorker:
    """Claim queued jobs, execute the existing runner, and persist events."""

    def __init__(
        self,
        repo_root: Path,
        store: JobStore,
        *,
        worker_id: str | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        cancel_grace_seconds: float = DEFAULT_CANCEL_GRACE_SECONDS,
        command_builder: Callable[[Path, Path], list[str]] | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.store = store
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.cancel_grace_seconds = max(0.1, float(cancel_grace_seconds))
        self.command_builder = command_builder or self._default_command
        self._stopping = False

    def _default_command(self, config_path: Path, _attempt_dir: Path) -> list[str]:
        return ["sh", str(self.repo_root / "workbench" / "run.sh"), str(config_path)]

    def request_stop(self, *_args: Any) -> None:
        self._stopping = True

    def recover(self) -> list[str]:
        active_workers = self.store.live_worker_ids(max_age_seconds=max(30.0, self.poll_seconds * 10))
        return self.store.recover_orphaned_jobs(active_workers)

    def run(self, *, once: bool = False) -> int:
        self.store.register_worker(self.worker_id, os.getpid())
        try:
            self.recover()
            while not self._stopping:
                claimed = self.store.claim_next(self.worker_id)
                if claimed is None:
                    if once:
                        return 0
                    self.store.heartbeat(self.worker_id)
                    time.sleep(self.poll_seconds)
                    continue
                self.execute(claimed)
                if once:
                    return 0
            return 0
        finally:
            self.store.unregister_worker(self.worker_id)

    def _attempt_directory(self, job: dict[str, Any]) -> Path:
        run_root = (self.repo_root / str(job["run_root"])).resolve()
        managed_root = (self.repo_root / "workbench-runs").resolve()
        if not _is_under(run_root, managed_root):
            raise RuntimeError("Persistent job run root is outside workbench-runs")
        attempt_dir = run_root / f"attempt-{int(job['attempt']):04d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        if not _is_under(attempt_dir, run_root):
            raise RuntimeError("Persistent job attempt directory escaped its run root")
        return attempt_dir

    def _managed_config(self, job: dict[str, Any], attempt_dir: Path) -> dict[str, Any]:
        config = json.loads(json.dumps(job["config"]))
        config.setdefault("outputs", {})["directory"] = str(attempt_dir.relative_to(self.repo_root))
        metadata = config.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["persistent_job"] = True
            metadata["attempt"] = int(job["attempt"])
        return config

    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = str(job["job_id"])
        attempt_dir = self._attempt_directory(job)
        logs_dir = attempt_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        config_path = attempt_dir / "job-config.json"
        config_path.write_text(
            json.dumps(self._managed_config(job, attempt_dir), indent=2) + "\n",
            encoding="utf-8",
        )
        log_path = logs_dir / "worker-run.log"
        command = self.command_builder(config_path, attempt_dir)
        process: subprocess.Popen[str] | None = None
        cancellation_started: float | None = None
        cancellation_code: str | None = None
        forced = False
        try:
            with log_path.open("w", encoding="utf-8") as log:
                log.write("COMMAND: " + " ".join(self._redacted_command(command, config_path)) + "\n\n")
                log.flush()
                process = subprocess.Popen(
                    command,
                    cwd=self.repo_root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                while process.poll() is None:
                    self.store.heartbeat(self.worker_id)
                    user_cancel = self.store.cancel_requested(job_id)
                    shutdown_cancel = self._stopping
                    if user_cancel or shutdown_cancel:
                        if cancellation_started is None:
                            cancellation_code = (
                                "CANCELLED_BY_USER" if user_cancel else "WORKER_SHUTDOWN"
                            )
                            cancellation_started = time.monotonic()
                            self._terminate(process, signal.SIGTERM)
                        elif time.monotonic() - cancellation_started >= self.cancel_grace_seconds:
                            forced = True
                            self._terminate(process, signal.SIGKILL)
                    time.sleep(min(self.poll_seconds, 0.25))
                exit_code = process.wait()

            self._record_artifacts(job_id, attempt_dir)
            relative_log = str(log_path.relative_to(attempt_dir))
            if cancellation_started is not None:
                shutdown = cancellation_code == "WORKER_SHUTDOWN"
                if forced:
                    message = (
                        "The worker force-stopped the process while shutting down."
                        if shutdown
                        else "The worker force-stopped the process after the cancellation grace period."
                    )
                else:
                    message = (
                        "The worker stopped the process during an orderly worker shutdown."
                        if shutdown
                        else "The worker stopped the process after a cancellation request."
                    )
                return self.store.complete(
                    job_id,
                    state="CANCELLED",
                    exit_code=exit_code,
                    log_path=relative_log,
                    error_code=cancellation_code,
                    error_message=message,
                )
            if exit_code == 0:
                return self.store.complete(
                    job_id,
                    state="SUCCEEDED",
                    exit_code=exit_code,
                    log_path=relative_log,
                )
            return self.store.complete(
                job_id,
                state="FAILED",
                exit_code=exit_code,
                log_path=relative_log,
                error_code="PROCESS_CRASH",
                error_message=f"Workbench runner exited with code {exit_code}.",
            )
        except Exception as exc:
            if process is not None and process.poll() is None:
                self._terminate(process, signal.SIGKILL)
            try:
                relative_log = str(log_path.relative_to(attempt_dir))
            except ValueError:
                relative_log = None
            return self.store.complete(
                job_id,
                state="FAILED",
                exit_code=process.returncode if process is not None else None,
                log_path=relative_log,
                error_code="WORKER_ERROR",
                error_message=str(exc),
            )

    def _terminate(self, process: subprocess.Popen[str], sig: signal.Signals) -> None:
        try:
            os.killpg(process.pid, sig)
        except (AttributeError, OSError):
            try:
                process.send_signal(sig)
            except OSError:
                pass

    @staticmethod
    def _redacted_command(command: list[str], config_path: Path) -> list[str]:
        return ["<server-managed-config>" if Path(item) == config_path else item for item in command]

    def _record_artifacts(self, job_id: str, attempt_dir: Path) -> None:
        for root_name, artifact_type in (
            ("logs", "log"),
            ("outputs", "output"),
            ("visualizations", "visualization"),
        ):
            root = attempt_dir / root_name
            if root.is_symlink() or not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or not path.is_file() or not _is_under(path, attempt_dir):
                    continue
                try:
                    size = path.stat().st_size
                    checksum = _sha256(path)
                except OSError:
                    continue
                self.store.add_artifact(
                    job_id,
                    artifact_type=artifact_type,
                    relative_path=str(path.relative_to(attempt_dir)),
                    size_bytes=size,
                    sha256=checksum,
                )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the persistent WRF Workbench worker")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--database", type=Path, help="Defaults to workbench-runs/jobs.sqlite3")
    parser.add_argument("--worker-id")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--cancel-grace-seconds", type=float, default=DEFAULT_CANCEL_GRACE_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    configured_database = args.database or os.environ.get(
        "WRF_CHAMMER_JOB_DATABASE", "workbench-runs/jobs.sqlite3"
    )
    database_path = Path(configured_database).expanduser()
    if not database_path.is_absolute():
        database_path = repo_root / database_path
    database = database_path.resolve()
    store = JobStore(database)
    worker = JobWorker(
        repo_root,
        store,
        worker_id=args.worker_id,
        poll_seconds=args.poll_seconds,
        cancel_grace_seconds=args.cancel_grace_seconds,
    )
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)
    try:
        return worker.run(once=args.once)
    except JobStoreError as exc:
        print(f"Persistent worker failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
