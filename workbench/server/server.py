#!/usr/bin/env python3
"""Local HTTP API for the WRF Workbench.

The server is deliberately small and dependency-free.  It is a local bridge
between browser UI code and the existing Workbench core/runner scripts.

Security model: local development only.  The default bind address is
127.0.0.1 and the API executes local Workbench scripts.
"""

from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workbench.core.catalogue import (  # noqa: E402
    CatalogueError,
    EventNotFoundError,
    PresetNotFoundError,
    build_job_config,
    load_catalogue,
    resolve_event,
    search_events,
)
from workbench.validate import validate_config  # noqa: E402

JOB_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
RUN_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_REQUEST_BYTES = 1024 * 1024
MAX_LOG_BYTES = 200 * 1024


class ApiError(Exception):
    """HTTP-facing API error with structured details."""

    def __init__(self, status: int, code: str, message: str, details: Any | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class WorkbenchApiServer(ThreadingHTTPServer):
    """Threading HTTP server carrying Workbench paths/configuration."""

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], repo_root: Path):
        super().__init__(server_address, handler_class)
        self.repo_root = repo_root.resolve()
        self.api_runs_dir = self.repo_root / "workbench-runs" / "api-runs"
        self.api_runs_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.api_runs_dir / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"jobs": {}}, indent=2) + "\n", encoding="utf-8")


class WorkbenchApiHandler(BaseHTTPRequestHandler):
    """Request handler for the local Workbench API."""

    server: WorkbenchApiServer

    def _is_loopback_client(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _is_loopback_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        if parsed.scheme != "http" or parsed.hostname is None:
            return False
        if parsed.hostname == "localhost":
            return True
        try:
            return ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            return False

    def _require_local_client(self) -> None:
        if not self._is_loopback_client():
            raise ApiError(HTTPStatus.FORBIDDEN, "forbidden", "Workbench API only accepts loopback clients")
        if not self._is_loopback_origin():
            raise ApiError(HTTPStatus.FORBIDDEN, "forbidden_origin", "Origin is not allowed for the local Workbench API")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, code: str, message: str, details: Any | None = None) -> None:
        payload: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
        if details is not None:
            payload["error"]["details"] = details
        self._send_json(status, payload)

    def _read_json(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "bad_content_length", "Invalid Content-Length header") from exc
        if length > MAX_REQUEST_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large", "Request body is too large")
        try:
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_json", f"Request body is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_body", "Request body must be a JSON object")
        return payload

    def do_OPTIONS(self) -> None:  # noqa: N802
        try:
            self._require_local_client()
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._require_local_client()
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)

            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "status": "ok"})
                return
            if path == "/api/events":
                self._handle_get_events(query)
                return
            if path.startswith("/api/events/"):
                self._handle_get_event(path)
                return
            if path.startswith("/api/jobs/"):
                self._handle_get_job_path(path)
                return

            self._send_error(HTTPStatus.NOT_FOUND, "not_found", f"Unknown endpoint: {path}")
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_local_client()
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/api/jobs/preview":
                self._handle_preview_job()
                return
            if path == "/api/jobs/validate":
                self._handle_validate_job()
                return
            if path == "/api/jobs":
                self._handle_create_job()
                return
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self._handle_cancel_job(path)
                return

            self._send_error(HTTPStatus.NOT_FOUND, "not_found", f"Unknown endpoint: {path}")
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _catalogue(self) -> dict[str, Any]:
        try:
            return load_catalogue(self.server.repo_root)
        except CatalogueError as exc:
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "catalogue_error", str(exc)) from exc

    def _event_detail(self, event: dict[str, Any], catalogue: dict[str, Any]) -> dict[str, Any]:
        domains = catalogue["domains"]
        resolutions = catalogue["resolution_presets"]
        return {
            "event": event,
            "domain_presets": [domains[preset_id] for preset_id in event.get("domains", []) if preset_id in domains],
            "resolution_presets": [resolutions[preset_id] for preset_id in event.get("resolution_presets", []) if preset_id in resolutions],
        }

    def _handle_get_events(self, query: dict[str, list[str]]) -> None:
        catalogue = self._catalogue()
        q = query.get("q", [""])[0]
        if q.strip():
            events = search_events(q, catalogue)
        else:
            events = [catalogue["events"][event_id] for event_id in sorted(catalogue["events"])]
        self._send_json(HTTPStatus.OK, {"ok": True, "count": len(events), "events": events})

    def _handle_get_event(self, path: str) -> None:
        catalogue = self._catalogue()
        event_ref = unquote(path.split("/api/events/", 1)[1])
        try:
            event = resolve_event(event_ref, catalogue)
        except EventNotFoundError as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, "event_not_found", str(exc)) from exc
        self._send_json(HTTPStatus.OK, {"ok": True, **self._event_detail(event, catalogue)})

    def _extract_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = payload.get("config", payload)
        if not isinstance(config, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_config", "Field 'config' must be a JSON object")
        return config

    def _validate_config(self, config: dict[str, Any]) -> list[str]:
        return validate_config(config)

    def _handle_preview_job(self) -> None:
        payload = self._read_json()
        event_ref = payload.get("event") or payload.get("event_id") or payload.get("event_ref")
        if not isinstance(event_ref, str) or not event_ref.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, "missing_event", "Field 'event' is required")
        try:
            config = build_job_config(
                event_ref,
                domain_id=payload.get("domain") or payload.get("domain_id"),
                resolution_preset_id=(payload.get("resolution") or payload.get("resolution_preset") or payload.get("resolution_preset_id")),
                mode=payload.get("mode", "dry-run"),
                job_id=payload.get("job_id"),
                output_directory=payload.get("output_directory"),
                input_source=payload.get("input_source", "era5"),
                catalogue=self._catalogue(),
            )
        except EventNotFoundError as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, "event_not_found", str(exc)) from exc
        except PresetNotFoundError as exc:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "preset_not_found", str(exc)) from exc
        except CatalogueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "catalogue_error", str(exc)) from exc

        errors = self._validate_config(config)
        if errors:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "valid": False, "errors": errors, "config": config})
        else:
            self._send_json(HTTPStatus.OK, {"ok": True, "valid": True, "errors": [], "config": config})

    def _handle_validate_job(self) -> None:
        payload = self._read_json()
        config = self._extract_config(payload)
        errors = self._validate_config(config)
        if errors:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "valid": False, "errors": errors})
        else:
            self._send_json(HTTPStatus.OK, {"ok": True, "valid": True, "errors": []})

    def _handle_create_job(self) -> None:
        payload = self._read_json()
        submitted_config = self._extract_config(payload)
        errors = self._validate_config(submitted_config)
        if errors:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "valid": False, "errors": errors})
            return

        job_id = submitted_config["id"]
        if not JOB_ID_RE.match(job_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_job_id", f"Invalid job id: {job_id!r}")

        run_token = self._new_run_token()
        run_dir = self._run_dir_from_token(run_token)
        run_dir.mkdir(parents=True, exist_ok=False)

        config = self._server_managed_config(submitted_config, run_token)
        sanitized_errors = self._validate_config(config)
        if sanitized_errors:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "valid": False, "errors": sanitized_errors})
            return

        config_path = run_dir / "api-config.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self._write_index_record(job_id, run_token, state="created")

        start = bool(payload.get("start", True))
        if not start:
            self._send_json(HTTPStatus.CREATED, {"ok": True, "job": self._job_summary(job_id)})
            return

        result = self._run_workbench(config_path, run_dir)
        self._write_index_record(job_id, run_token, state=result["status"])
        status_code = HTTPStatus.CREATED if result["exit_code"] == 0 else HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_json(status_code, {"ok": result["exit_code"] == 0, "job": self._job_summary(job_id), "run": result})

    def _server_managed_config(self, submitted_config: dict[str, Any], run_token: str) -> dict[str, Any]:
        config = copy.deepcopy(submitted_config)
        config.setdefault("outputs", {})
        config["outputs"]["directory"] = f"workbench-runs/api-runs/{run_token}"
        metadata = config.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["api_run_token"] = run_token
        return config

    def _new_run_token(self) -> str:
        return uuid.uuid4().hex

    def _run_dir_from_token(self, run_token: str) -> Path:
        if not RUN_TOKEN_RE.match(run_token):
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "bad_run_token", "Stored run token is invalid")
        return self.server.api_runs_dir / run_token

    def _run_workbench(self, config_path: Path, run_dir: Path) -> dict[str, Any]:
        start_time = time.time()
        command = ["sh", str(self.server.repo_root / "workbench" / "run.sh"), str(config_path)]
        completed = subprocess.run(command, cwd=str(self.server.repo_root), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "api-run.log").write_text(
            "COMMAND: sh workbench/run.sh <server-managed-config>\n\n" + "STDOUT:\n" + completed.stdout + "\nSTDERR:\n" + completed.stderr,
            encoding="utf-8",
        )
        return {
            "command": ["sh", "workbench/run.sh", "<server-managed-config>"],
            "exit_code": completed.returncode,
            "duration_seconds": round(time.time() - start_time, 3),
            "status": "succeeded" if completed.returncode == 0 else "failed",
        }

    def _read_index(self) -> dict[str, Any]:
        try:
            index = json.loads(self.server.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            index = {"jobs": {}}
        if not isinstance(index, dict) or not isinstance(index.get("jobs"), dict):
            index = {"jobs": {}}
        return index

    def _write_index(self, index: dict[str, Any]) -> None:
        self.server.index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

    def _write_index_record(self, job_id: str, run_token: str, state: str) -> None:
        index = self._read_index()
        index["jobs"][job_id] = {
            "job_id": job_id,
            "run_token": run_token,
            "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._write_index(index)

    def _load_index_record(self, job_id: str) -> dict[str, Any]:
        if not JOB_ID_RE.match(job_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_job_id", f"Invalid job id: {job_id!r}")
        record = self._read_index().get("jobs", {}).get(job_id)
        if not isinstance(record, dict):
            raise ApiError(HTTPStatus.NOT_FOUND, "job_not_found", f"Unknown job: {job_id}")
        run_token = record.get("run_token")
        if not isinstance(run_token, str) or not RUN_TOKEN_RE.match(run_token):
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "bad_run_token", "Stored run token is invalid")
        return record

    def _job_summary(self, job_id: str) -> dict[str, Any]:
        record = self._load_index_record(job_id)
        run_token = record["run_token"]
        run_dir = self._run_dir_from_token(run_token)
        status = self._read_optional_json(run_dir / "status.json")
        job = self._read_optional_json(run_dir / "job.json")
        logs_dir = run_dir / "logs"
        outputs_dir = run_dir / "outputs"
        visualization_dir = run_dir / "visualizations"
        metadata_file = visualization_dir / "metadata.json"
        return {
            "job_id": job_id,
            "run_token": run_token,
            "run_dir": str(run_dir),
            "status": status or {"job_id": job_id, "status": record.get("state", "created")},
            "job": job,
            "logs": self._list_files(logs_dir),
            "outputs": self._list_files(outputs_dir),
            "visualization": {
                "available": self._safe_file_exists(metadata_file, visualization_dir),
                "path": str(visualization_dir),
                "metadata": "metadata.json" if self._safe_file_exists(metadata_file, visualization_dir) else None,
            },
        }

    def _read_optional_json(self, path: Path) -> Any | None:
        parent = path.parent
        if not self._safe_file_exists(path, parent):
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _safe_file_exists(self, path: Path, base_dir: Path) -> bool:
        if path.is_symlink() or not path.is_file():
            return False
        try:
            base_resolved = base_dir.resolve()
            resolved = path.resolve()
            return os.path.commonpath([str(base_resolved), str(resolved)]) == str(base_resolved)
        except OSError:
            return False

    def _list_files(self, directory: Path, limit: int = 100) -> list[dict[str, Any]]:
        if directory.is_symlink() or not directory.is_dir():
            return []
        try:
            base_resolved = directory.resolve()
        except OSError:
            return []
        files: list[dict[str, Any]] = []
        for path in sorted(p for p in directory.rglob("*") if not p.is_symlink() and p.is_file())[:limit]:
            try:
                resolved = path.resolve()
                if os.path.commonpath([str(base_resolved), str(resolved)]) != str(base_resolved):
                    continue
                files.append({"name": path.name, "relative_path": str(path.relative_to(directory)), "size_bytes": path.stat().st_size})
            except OSError:
                continue
        return files

    def _handle_get_job_path(self, path: str) -> None:
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "jobs":
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", f"Unknown endpoint: {path}")
            return
        job_id = parts[2]
        if len(parts) == 3:
            self._send_json(HTTPStatus.OK, {"ok": True, "job": self._job_summary(job_id)})
            return
        if len(parts) == 4 and parts[3] == "logs":
            self._handle_get_logs(job_id)
            return
        if len(parts) == 4 and parts[3] == "outputs":
            job = self._job_summary(job_id)
            self._send_json(HTTPStatus.OK, {"ok": True, "job_id": job_id, "outputs": job["outputs"]})
            return
        if len(parts) == 4 and parts[3] == "visualization":
            job = self._job_summary(job_id)
            self._send_json(HTTPStatus.OK, {"ok": True, "job_id": job_id, "visualization": job["visualization"]})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not_found", f"Unknown endpoint: {path}")

    def _handle_get_logs(self, job_id: str) -> None:
        record = self._load_index_record(job_id)
        run_dir = self._run_dir_from_token(record["run_token"])
        logs_dir = run_dir / "logs"
        logs = []
        for entry in self._list_files(logs_dir):
            relative_path = entry["relative_path"]
            if "/" in relative_path or "\\" in relative_path:
                continue
            path = logs_dir / relative_path
            if not self._safe_file_exists(path, logs_dir):
                continue
            content = path.read_text(encoding="utf-8", errors="replace")[:MAX_LOG_BYTES]
            logs.append({**entry, "content": content})
        self._send_json(HTTPStatus.OK, {"ok": True, "job_id": job_id, "logs": logs})

    def _handle_cancel_job(self, path: str) -> None:
        parts = [unquote(part) for part in path.split("/") if part]
        job_id = parts[2] if len(parts) >= 3 else "unknown"
        self._send_json(
            HTTPStatus.NOT_IMPLEMENTED,
            {"ok": False, "job_id": job_id, "error": {"code": "cancel_not_implemented", "message": "Job cancellation is not implemented yet for the local synchronous runner."}},
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local WRF Workbench API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]), help="Repository root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    os.chdir(repo_root)
    server = WorkbenchApiServer((args.host, args.port), WorkbenchApiHandler, repo_root)
    print(f"Workbench API listening on http://{args.host}:{args.port}", flush=True)
    print("Security: local development server only; do not expose this to the public internet.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping Workbench API", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
