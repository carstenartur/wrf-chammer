#!/usr/bin/env python3
"""Persistent, process-isolated orchestration for real ERA5 downloads."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench.era5_service import Era5DataService

_ACTIVE_STATUSES = {"QUEUED", "RUNNING", "CANCELLING"}
_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
_RETRYABLE_STATUSES = {"FAILED", "CANCELLED"}
_DOWNLOAD_ID_RE = re.compile(r"^era5-[0-9a-f]{12}-[0-9a-f]{10}$")


class Era5DownloadManagerError(RuntimeError):
    """A safe error that can be returned by the local API."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Era5DownloadManagerError(
            "download_state_invalid",
            "The stored ERA5 download state is missing or invalid.",
        ) from exc
    if not isinstance(payload, dict):
        raise Era5DownloadManagerError(
            "download_state_invalid",
            "The stored ERA5 download state is invalid.",
        )
    return payload


class Era5DownloadManager:
    """Queue and supervise downloader subprocesses with persistent state."""

    def __init__(
        self,
        repo_root: Path,
        data_service: Era5DataService,
        *,
        downloader_path: Path | None = None,
        max_concurrent: int | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.data_service = data_service
        self.cache_root = data_service.cache_root
        self.downloader_path = (
            downloader_path or self.repo_root / "ci" / "download-era5.py"
        ).resolve()
        configured_workers = os.environ.get("WRF_CHAMMER_ERA5_DOWNLOAD_WORKERS", "1")
        try:
            configured_count = int(configured_workers)
        except ValueError:
            configured_count = 1
        self.max_concurrent = max(1, min(4, max_concurrent or configured_count))
        self._condition = threading.Condition()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._stopping = False
        self._recover_interrupted_jobs()
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop,
            name="era5-download-dispatcher",
            daemon=True,
        )
        self._dispatcher.start()

    def close(self) -> None:
        with self._condition:
            self._stopping = True
            processes = list(self._processes.items())
            self._condition.notify_all()
        for job_id, process in processes:
            self._request_process_stop(job_id, process)
        self._dispatcher.join(timeout=5)

    def start(
        self,
        request: dict[str, Any],
        latest_preview: dict[str, Any] | None,
    ) -> dict[str, Any]:
        prepared = self.data_service.prepare(request, latest_preview)
        plan = prepared["plan"]
        if self.data_service.requires_credentials(plan["plan_key"]) and not self.data_service.credential_status()["configured"]:
            raise Era5DownloadManagerError(
                "credentials_required",
                "CDS credentials are required because the ERA5 plan contains missing files.",
            )
        return self._enqueue(plan["plan_key"], retry_of=None)

    def retry(self, job_id: str) -> dict[str, Any]:
        state = self.get(job_id)
        if state["status"] not in _RETRYABLE_STATUSES:
            raise Era5DownloadManagerError(
                "download_not_retryable",
                "Only failed or cancelled ERA5 downloads can be retried.",
            )
        plan = self.data_service.load_prepared_plan(state["plan_key"])
        if self.data_service.requires_credentials(plan["plan_key"]) and not self.data_service.credential_status()["configured"]:
            raise Era5DownloadManagerError(
                "credentials_required",
                "CDS credentials are required because the ERA5 plan still contains missing files.",
            )
        return self._enqueue(state["plan_key"], retry_of=job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._condition:
            state = self._read_state(job_id)
            status = state["status"]
            if status in _TERMINAL_STATUSES:
                return self._public_state(state)
            if status == "QUEUED":
                state.update(
                    status="CANCELLED",
                    finished_at=_utc_now(),
                    message="The queued ERA5 download was cancelled before it started.",
                )
                self._write_state(state)
                self._append_event(state, "status", "ERA5 download cancelled.")
                self._condition.notify_all()
                return self._public_state(state)
            state.update(
                status="CANCELLING",
                message="Stopping the ERA5 downloader process…",
            )
            self._write_state(state)
            self._append_event(state, "status", "Cancellation requested.")
            process = self._processes.get(job_id)
        if process is not None:
            self._request_process_stop(job_id, process)
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        return self._public_state(self._read_state(job_id))

    def list(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        if not self.cache_root.is_dir():
            return jobs
        for state_path in self.cache_root.glob("*/downloads/*/state.json"):
            try:
                state = _load_json(state_path)
                jobs.append(self._public_state(state))
            except Era5DownloadManagerError:
                continue
        jobs.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return jobs

    def events(self, job_id: str) -> list[dict[str, Any]]:
        state = self._read_state(job_id)
        events_path = self._job_directory(state["plan_key"], job_id) / "events.jsonl"
        events: list[dict[str, Any]] = []
        if not events_path.is_file():
            return events
        try:
            for line in events_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
        except (OSError, json.JSONDecodeError):
            return []
        return events

    def _enqueue(self, plan_key: str, retry_of: str | None) -> dict[str, Any]:
        self.data_service.load_prepared_plan(plan_key)
        job_id = f"era5-{plan_key[:12]}-{uuid.uuid4().hex[:10]}"
        now = _utc_now()
        state: dict[str, Any] = {
            "version": 1,
            "id": job_id,
            "plan_key": plan_key,
            "status": "QUEUED",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "pid": None,
            "retry_of": retry_of,
            "message": "Waiting for an ERA5 download worker.",
            "error": None,
            "progress": {
                "total_requests": 0,
                "completed_requests": 0,
                "current_request": None,
                "current_attempt": None,
            },
            "artifacts": {
                "plan": self.data_service.display_plan_path(plan_key, "era5-plan.json"),
                "download_config": self.data_service.display_plan_path(plan_key, "era5-download-config.json"),
                "manifest": None,
            },
        }
        job_directory = self._job_directory(plan_key, job_id)
        job_directory.mkdir(parents=True, exist_ok=False)
        self._write_state(state)
        self._append_event(state, "status", "ERA5 download queued.")
        with self._condition:
            self._condition.notify_all()
        return self._public_state(state)

    def _dispatch_loop(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
                available = self.max_concurrent - len(self._processes)
                queued = self._queued_states() if available > 0 else []
                if not queued:
                    self._condition.wait(timeout=0.5)
                    continue
                selected = queued[:available]
            for state in selected:
                try:
                    self._start_process(state)
                except Exception:
                    failed = self._read_state(state["id"])
                    failed.update(
                        status="FAILED",
                        finished_at=_utc_now(),
                        message="The ERA5 downloader process could not be started.",
                        error={
                            "code": "worker_start_failed",
                            "message": "The local ERA5 downloader process could not be started.",
                        },
                    )
                    self._write_state(failed)
                    self._append_event(failed, "error", failed["error"]["message"])

    def _queued_states(self) -> list[dict[str, Any]]:
        queued = [state for state in self._all_states() if state.get("status") == "QUEUED"]
        queued.sort(key=lambda item: str(item.get("created_at", "")))
        return queued

    def _all_states(self) -> list[dict[str, Any]]:
        states: list[dict[str, Any]] = []
        if not self.cache_root.is_dir():
            return states
        for path in self.cache_root.glob("*/downloads/*/state.json"):
            try:
                states.append(_load_json(path))
            except Era5DownloadManagerError:
                continue
        return states

    def _start_process(self, state: dict[str, Any]) -> None:
        job_id = state["id"]
        with self._condition:
            if self._stopping or job_id in self._processes:
                return
            current = self._read_state(job_id)
            if current["status"] != "QUEUED":
                return
            plan_directory = self.data_service.plan_directory(current["plan_key"])
            job_directory = self._job_directory(current["plan_key"], job_id)
            progress_path = job_directory / "progress.json"
            manifest_path = job_directory / "era5-manifest.json"
            log_path = job_directory / "worker.log"
            command = [
                sys.executable,
                str(self.downloader_path),
                "--config",
                str(plan_directory / "era5-download-config.json"),
                "--output-dir",
                str(plan_directory),
                "--manifest",
                str(manifest_path),
                "--progress",
                str(progress_path),
            ]
            log_handle = log_path.open("a", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.repo_root,
                    env=os.environ.copy(),
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=(os.name == "posix"),
                )
            except Exception:
                log_handle.close()
                raise
            current.update(
                status="RUNNING",
                started_at=_utc_now(),
                pid=process.pid,
                message="Downloading or verifying ERA5 request files.",
                error=None,
            )
            self._write_state(current)
            self._append_event(current, "status", "ERA5 downloader process started.")
            self._processes[job_id] = process
            monitor = threading.Thread(
                target=self._monitor_process,
                args=(job_id, process, log_handle),
                name=f"era5-download-{job_id}",
                daemon=True,
            )
            monitor.start()

    def _monitor_process(
        self,
        job_id: str,
        process: subprocess.Popen[str],
        log_handle: Any,
    ) -> None:
        return_code = process.wait()
        log_handle.close()
        try:
            state = self._read_state(job_id)
            progress = self._read_progress(state)
            if return_code == 0:
                manifest = self._validate_manifest(state)
                self._persist_cache_metadata(state, manifest)
                self.data_service.load_prepared_plan(state["plan_key"], persist_refresh=True)
                state.update(
                    status="SUCCEEDED",
                    finished_at=_utc_now(),
                    pid=None,
                    message="All ERA5 request files are available and verified.",
                    error=None,
                )
                state["artifacts"]["manifest"] = self._display_job_path(
                    state["plan_key"], job_id, "era5-manifest.json"
                )
                event_message = "ERA5 download completed successfully."
                event_type = "status"
            elif state["status"] == "CANCELLING" or progress.get("status") == "cancelled" or return_code in (130, -signal.SIGTERM):
                state.update(
                    status="CANCELLED",
                    finished_at=_utc_now(),
                    pid=None,
                    message="The ERA5 download was cancelled. Completed cache files remain reusable.",
                    error=None,
                )
                event_message = "ERA5 download cancelled."
                event_type = "status"
            else:
                state.update(
                    status="FAILED",
                    finished_at=_utc_now(),
                    pid=None,
                    message="The ERA5 download failed. Completed cache files remain reusable for retry.",
                    error={
                        "code": "download_failed",
                        "message": "The ERA5 downloader exited unsuccessfully. Inspect the local worker log for diagnostics.",
                        "exit_code": return_code,
                    },
                )
                event_message = state["error"]["message"]
                event_type = "error"
            state["progress"] = self._public_progress(progress)
            self._write_state(state)
            self._append_event(state, event_type, event_message)
        except Exception:
            try:
                state = self._read_state(job_id)
                state.update(
                    status="FAILED",
                    finished_at=_utc_now(),
                    pid=None,
                    message="The ERA5 download ended with invalid worker output.",
                    error={
                        "code": "worker_output_invalid",
                        "message": "The ERA5 worker did not produce valid progress or manifest data.",
                    },
                )
                self._write_state(state)
                self._append_event(state, "error", state["error"]["message"])
            except Exception:
                pass
        finally:
            with self._condition:
                self._processes.pop(job_id, None)
                self._condition.notify_all()

    def _request_process_stop(self, job_id: str, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
        try:
            state = self._read_state(job_id)
            self._append_event(state, "status", "ERA5 downloader stop signal sent.")
        except Era5DownloadManagerError:
            pass

    def _recover_interrupted_jobs(self) -> None:
        for state in self._all_states():
            if state.get("status") not in _ACTIVE_STATUSES:
                continue
            state.update(
                status="FAILED",
                finished_at=_utc_now(),
                pid=None,
                message="The Workbench restarted while this ERA5 download was active.",
                error={
                    "code": "worker_interrupted",
                    "message": "Retry the download; completed cache files will be reused.",
                },
            )
            self._write_state(state)
            self._append_event(state, "recovery", "Interrupted ERA5 download marked for safe retry.")

    def _validate_manifest(self, state: dict[str, Any]) -> dict[str, Any]:
        job_directory = self._job_directory(state["plan_key"], state["id"])
        manifest_path = job_directory / "era5-manifest.json"
        manifest = _load_json(manifest_path)
        outputs = manifest.get("outputs")
        config = self.data_service.load_download_config(state["plan_key"])
        requests = config.get("requests")
        if not isinstance(outputs, list) or not isinstance(requests, dict) or len(outputs) != len(requests):
            raise Era5DownloadManagerError(
                "manifest_invalid", "The ERA5 downloader manifest is incomplete."
            )
        plan_directory = self.data_service.plan_directory(state["plan_key"]).resolve()
        expected: dict[str, Path] = {}
        for name, request in requests.items():
            if not isinstance(name, str) or not isinstance(request, dict):
                raise Era5DownloadManagerError("manifest_invalid", "The ERA5 download configuration is invalid.")
            target_name = request.get("target")
            if not isinstance(target_name, str):
                raise Era5DownloadManagerError("manifest_invalid", "The ERA5 download configuration is invalid.")
            expected[name] = (plan_directory / target_name).resolve()

        seen: set[str] = set()
        for output in outputs:
            if not isinstance(output, dict):
                raise Era5DownloadManagerError("manifest_invalid", "The ERA5 downloader manifest is invalid.")
            name = output.get("name")
            target = output.get("target")
            digest = output.get("sha256")
            size_bytes = output.get("size_bytes")
            if not isinstance(name, str) or name not in expected or name in seen:
                raise Era5DownloadManagerError("manifest_invalid", "The ERA5 downloader manifest has unexpected requests.")
            if not isinstance(target, str) or not isinstance(digest, str) or len(digest) != 64:
                raise Era5DownloadManagerError("manifest_invalid", "The ERA5 downloader manifest is invalid.")
            target_path = Path(target).resolve()
            if target_path != expected[name]:
                raise Era5DownloadManagerError("manifest_invalid", "The ERA5 downloader manifest references an unexpected path.")
            if plan_directory != target_path and plan_directory not in target_path.parents:
                raise Era5DownloadManagerError("manifest_invalid", "The ERA5 downloader manifest references an unsafe path.")
            if not target_path.is_file() or target_path.stat().st_size <= 0:
                raise Era5DownloadManagerError("manifest_invalid", "An ERA5 manifest file is missing or empty.")
            actual_size = target_path.stat().st_size
            if not isinstance(size_bytes, int) or size_bytes != actual_size:
                raise Era5DownloadManagerError("manifest_invalid", "An ERA5 manifest file size does not match the stored file.")
            actual_digest = self._sha256_file(target_path)
            if not hmac.compare_digest(digest.lower(), actual_digest):
                raise Era5DownloadManagerError("manifest_invalid", "An ERA5 file checksum does not match its manifest.")
            seen.add(name)
        if seen != set(expected):
            raise Era5DownloadManagerError("manifest_invalid", "The ERA5 downloader manifest is incomplete.")
        return manifest

    def _persist_cache_metadata(self, state: dict[str, Any], manifest: dict[str, Any]) -> None:
        plan_directory = self.data_service.plan_directory(state["plan_key"])
        checksums: dict[str, Any] = {"version": 1, "plan_key": state["plan_key"], "files": {}}
        for output in manifest["outputs"]:
            target_path = Path(output["target"]).resolve()
            relative = target_path.relative_to(plan_directory.resolve()).as_posix()
            checksums["files"][relative] = {
                "sha256": output["sha256"],
                "size_bytes": output["size_bytes"],
                "request_name": output["name"],
            }
        plan = self.data_service.load_prepared_plan(state["plan_key"])
        provenance = {
            "version": 1,
            "plan_key": state["plan_key"],
            "source": plan.get("provenance", {}).get(
                "source", "Copernicus Climate Data Store ERA5 reanalysis"
            ),
            "datasets": plan.get("provenance", {}).get("datasets", []),
            "artificial_weather_data": False,
            "verified_at": _utc_now(),
            "download_job_id": state["id"],
            "request_count": len(manifest["outputs"]),
        }
        _atomic_json(plan_directory / "checksums.json", checksums)
        _atomic_json(plan_directory / "provenance.json", provenance)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _read_progress(self, state: dict[str, Any]) -> dict[str, Any]:
        progress_path = self._job_directory(state["plan_key"], state["id"]) / "progress.json"
        if not progress_path.is_file():
            return state.get("progress") if isinstance(state.get("progress"), dict) else {}
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return state.get("progress") if isinstance(state.get("progress"), dict) else {}
        return progress if isinstance(progress, dict) else {}

    @staticmethod
    def _public_progress(progress: dict[str, Any]) -> dict[str, Any]:
        return {
            "total_requests": int(progress.get("total_requests") or 0),
            "completed_requests": int(progress.get("completed_requests") or 0),
            "current_request": progress.get("current_request") if isinstance(progress.get("current_request"), str) else None,
            "current_attempt": int(progress["current_attempt"]) if isinstance(progress.get("current_attempt"), int) else None,
            "updated_at": progress.get("updated_at") if isinstance(progress.get("updated_at"), str) else None,
        }

    def _public_state(self, state: dict[str, Any]) -> dict[str, Any]:
        public = {
            key: state.get(key)
            for key in (
                "version",
                "id",
                "plan_key",
                "status",
                "created_at",
                "started_at",
                "finished_at",
                "retry_of",
                "message",
                "error",
                "artifacts",
            )
        }
        public["progress"] = self._public_progress(self._read_progress(state))
        public["retryable"] = state.get("status") in _RETRYABLE_STATUSES
        public["cancellable"] = state.get("status") in _ACTIVE_STATUSES
        return public

    def _read_state(self, job_id: str) -> dict[str, Any]:
        if not isinstance(job_id, str) or not _DOWNLOAD_ID_RE.fullmatch(job_id):
            raise Era5DownloadManagerError("download_not_found", "ERA5 download job not found.")
        matches = list(self.cache_root.glob(f"*/downloads/{job_id}/state.json"))
        if len(matches) != 1:
            raise Era5DownloadManagerError("download_not_found", "ERA5 download job not found.")
        return _load_json(matches[0])

    def _write_state(self, state: dict[str, Any]) -> None:
        path = self._job_directory(state["plan_key"], state["id"]) / "state.json"
        _atomic_json(path, state)

    def _append_event(self, state: dict[str, Any], event_type: str, message: str) -> None:
        path = self._job_directory(state["plan_key"], state["id"]) / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        sequence = 1
        if path.is_file():
            try:
                sequence = len(path.read_text(encoding="utf-8").splitlines()) + 1
            except OSError:
                sequence = 1
        event = {
            "sequence": sequence,
            "timestamp": _utc_now(),
            "type": event_type,
            "status": state.get("status"),
            "message": message,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _job_directory(self, plan_key: str, job_id: str) -> Path:
        return self.data_service.plan_directory(plan_key) / "downloads" / job_id

    def _display_job_path(self, plan_key: str, job_id: str, filename: str) -> str:
        base = self.data_service.display_plan_path(plan_key, f"downloads/{job_id}/{filename}")
        return base
