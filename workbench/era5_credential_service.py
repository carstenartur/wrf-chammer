#!/usr/bin/env python3
"""Persistent, secret-safe orchestration of explicit CDS credential tests."""

from __future__ import annotations

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

_VALIDATION_ID_RE = re.compile(r"^cds-validation-[0-9a-f]{16}$")
_ACTIVE = {"QUEUED", "RUNNING"}
_TERMINAL = {"VALID", "INVALID", "FAILED", "CANCELLED"}
_ALLOWED_RESULT_STATUSES = {"VALID", "INVALID", "FAILED"}


class Era5CredentialValidationError(RuntimeError):
    """A classified, user-facing credential-validation error."""

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
        raise Era5CredentialValidationError(
            "validation_state_invalid",
            "The stored CDS credential-validation state is invalid.",
        ) from exc
    if not isinstance(payload, dict):
        raise Era5CredentialValidationError(
            "validation_state_invalid",
            "The stored CDS credential-validation state is invalid.",
        )
    return payload


class Era5CredentialValidationService:
    """Run one explicit minimal CDS request without exposing credentials."""

    def __init__(
        self,
        repo_root: Path,
        data_service: Era5DataService,
        *,
        validator_path: Path | None = None,
        timeout_seconds: float | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.data_service = data_service
        self.root = data_service.cache_root / ".credential-validation"
        self.root.mkdir(parents=True, exist_ok=True)
        configured_validator = os.environ.get("WRF_CHAMMER_CDS_VALIDATOR")
        if validator_path is None and configured_validator:
            validator_path = Path(configured_validator)
        self.validator_path = (
            validator_path or self.repo_root / "ci" / "validate-cds-credentials.py"
        ).resolve()
        configured_timeout = os.environ.get("WRF_CHAMMER_CDS_VALIDATION_TIMEOUT", "300")
        try:
            parsed_timeout = float(configured_timeout)
        except ValueError:
            parsed_timeout = 300.0
        self.timeout_seconds = max(1.0, min(900.0, timeout_seconds or parsed_timeout))
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._active_job_id: str | None = None
        self._recover_interrupted_validation()

    def close(self) -> None:
        with self._lock:
            process = self._process
            job_id = self._active_job_id
        if process is not None and process.poll() is None:
            self._stop_process(process)
            if job_id:
                try:
                    state = self._read_state(job_id)
                    state.update(
                        status="CANCELLED",
                        finished_at=_utc_now(),
                        pid=None,
                        code="application_shutdown",
                        summary="Credential validation was cancelled because the Workbench stopped.",
                    )
                    self._write_state(state)
                except Era5CredentialValidationError:
                    pass

    def status(self) -> dict[str, Any]:
        credential_status = self.data_service.credential_status()
        latest = self._latest_state()
        return {
            "ok": True,
            "configured": credential_status["configured"],
            "configuration_status": credential_status["status"],
            "configuration_summary": credential_status["summary"],
            "remediation": credential_status.get("remediation"),
            "validation": self._public_state(latest) if latest else None,
        }

    def start(self) -> dict[str, Any]:
        if not self.data_service.credential_status()["configured"]:
            raise Era5CredentialValidationError(
                "credentials_not_configured",
                "Configure local CDS credentials before starting a validation request.",
            )
        with self._lock:
            latest = self._latest_state()
            if latest and latest.get("status") in _ACTIVE:
                raise Era5CredentialValidationError(
                    "validation_in_progress",
                    "A CDS credential validation request is already running.",
                )
            if not self.validator_path.is_file():
                raise Era5CredentialValidationError(
                    "validator_unavailable",
                    "The local CDS credential validator is not available.",
                )
            job_id = f"cds-validation-{uuid.uuid4().hex[:16]}"
            job_directory = self._job_directory(job_id)
            job_directory.mkdir(parents=True, exist_ok=False)
            state = {
                "version": 1,
                "id": job_id,
                "status": "QUEUED",
                "created_at": _utc_now(),
                "started_at": None,
                "finished_at": None,
                "pid": None,
                "code": None,
                "summary": "Waiting to start the minimal CDS validation request.",
                "result": None,
            }
            self._write_state(state)
            _atomic_json(self.root / "latest.json", {"job_id": job_id})

            result_path = job_directory / "result.json"
            log_path = job_directory / "validator.log"
            command = [
                sys.executable,
                str(self.validator_path),
                "--result",
                str(result_path),
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
            except Exception as exc:
                log_handle.close()
                state.update(
                    status="FAILED",
                    finished_at=_utc_now(),
                    code="validator_start_failed",
                    summary="The local CDS credential validator could not be started.",
                )
                self._write_state(state)
                raise Era5CredentialValidationError(
                    "validator_start_failed",
                    "The local CDS credential validator could not be started.",
                ) from exc

            state.update(
                status="RUNNING",
                started_at=_utc_now(),
                pid=process.pid,
                summary="A minimal real ERA5 request is validating the configured credentials.",
            )
            self._write_state(state)
            self._process = process
            self._active_job_id = job_id
            monitor = threading.Thread(
                target=self._monitor,
                args=(job_id, process, log_handle),
                name=f"cds-validation-{job_id}",
                daemon=True,
            )
            monitor.start()
            return self._public_state(state)

    def _monitor(
        self,
        job_id: str,
        process: subprocess.Popen[str],
        log_handle: Any,
    ) -> None:
        timed_out = False
        try:
            return_code = process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._stop_process(process)
            return_code = process.wait()
        finally:
            log_handle.close()

        try:
            state = self._read_state(job_id)
            if timed_out:
                state.update(
                    status="FAILED",
                    finished_at=_utc_now(),
                    pid=None,
                    code="validation_timeout",
                    summary="The CDS validation request exceeded the configured time limit.",
                    result=None,
                )
            else:
                result_path = self._job_directory(job_id) / "result.json"
                result = _load_json(result_path)
                status = result.get("status")
                if status not in _ALLOWED_RESULT_STATUSES:
                    raise Era5CredentialValidationError(
                        "validation_result_invalid",
                        "The CDS validator returned an invalid result.",
                    )
                state.update(
                    status=status,
                    finished_at=_utc_now(),
                    pid=None,
                    code=result.get("code"),
                    summary=result.get("summary"),
                    result=self._sanitize_result(result),
                    exit_code=return_code,
                )
            self._write_state(state)
        except Exception:
            try:
                state = self._read_state(job_id)
                state.update(
                    status="FAILED",
                    finished_at=_utc_now(),
                    pid=None,
                    code="validation_result_invalid",
                    summary="The CDS validator did not produce a valid classified result.",
                    result=None,
                )
                self._write_state(state)
            except Exception:
                pass
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
                    self._process = None

    def _latest_state(self) -> dict[str, Any] | None:
        latest_path = self.root / "latest.json"
        if not latest_path.is_file():
            return None
        latest = _load_json(latest_path)
        job_id = latest.get("job_id")
        if not isinstance(job_id, str) or not _VALIDATION_ID_RE.fullmatch(job_id):
            raise Era5CredentialValidationError(
                "validation_state_invalid",
                "The stored CDS credential-validation index is invalid.",
            )
        return self._read_state(job_id)

    def _recover_interrupted_validation(self) -> None:
        try:
            state = self._latest_state()
        except Era5CredentialValidationError:
            return
        if not state or state.get("status") not in _ACTIVE:
            return
        state.update(
            status="FAILED",
            finished_at=_utc_now(),
            pid=None,
            code="validation_interrupted",
            summary="The Workbench restarted while credential validation was active. Start a new test request.",
            result=None,
        )
        self._write_state(state)

    def _read_state(self, job_id: str) -> dict[str, Any]:
        if not isinstance(job_id, str) or not _VALIDATION_ID_RE.fullmatch(job_id):
            raise Era5CredentialValidationError(
                "validation_not_found", "CDS credential validation not found."
            )
        return _load_json(self._job_directory(job_id) / "state.json")

    def _write_state(self, state: dict[str, Any]) -> None:
        _atomic_json(self._job_directory(state["id"]) / "state.json", state)

    def _job_directory(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not _VALIDATION_ID_RE.fullmatch(job_id):
            raise Era5CredentialValidationError(
                "validation_not_found", "CDS credential validation not found."
            )
        root = self.root.resolve()
        directory = (root / job_id).resolve()
        if directory.parent != root:
            raise Era5CredentialValidationError(
                "validation_not_found", "CDS credential validation not found."
            )
        return directory

    @staticmethod
    def _sanitize_result(result: dict[str, Any]) -> dict[str, Any]:
        request = result.get("request") if isinstance(result.get("request"), dict) else {}
        response = result.get("response") if isinstance(result.get("response"), dict) else {}
        return {
            "checked_at": result.get("checked_at"),
            "duration_seconds": result.get("duration_seconds"),
            "request": {
                "dataset": request.get("dataset"),
                "variable": request.get("variable"),
                "date": request.get("date"),
                "time": request.get("time"),
                "area": request.get("area"),
            },
            "response": {
                "size_bytes": response.get("size_bytes"),
                "sha256": response.get("sha256"),
                "retained": False,
            },
            "artificial_weather_data": False,
        }

    @staticmethod
    def _public_state(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": state.get("id"),
            "status": state.get("status"),
            "created_at": state.get("created_at"),
            "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"),
            "code": state.get("code"),
            "summary": state.get("summary"),
            "result": state.get("result"),
            "running": state.get("status") in _ACTIVE,
            "terminal": state.get("status") in _TERMINAL,
        }

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
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
