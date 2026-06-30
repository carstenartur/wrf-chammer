#!/usr/bin/env python3
"""Local HTTP API for the WRF Workbench."""

from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time
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
MAX_REQUEST_BYTES = 1024 * 1024
MAX_LOG_BYTES = 200 * 1024


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class WorkbenchApiServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], repo_root: Path):
        super().__init__(address, handler)
        self.repo_root = repo_root.resolve()
        self.web_dir = self.repo_root / "workbench" / "web"
        self.api_runs_dir = self.repo_root / "workbench-runs" / "api-runs"
        self.api_runs_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.api_runs_dir / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"jobs": {}}, indent=2) + "\n", encoding="utf-8")


class WorkbenchApiHandler(BaseHTTPRequestHandler):
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
            raise ApiError(HTTPStatus.FORBIDDEN, "forbidden_origin", "Origin is not allowed")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8") + b"\n"
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_error(self, status: int, code: str, message: str, details: Any | None = None) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if details is not None:
            error["details"] = details
        self._send_json(status, {"ok": False, "error": error})

    def _send_static_file(self, file_path: Path, content_type: str) -> None:
        if not file_path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Static asset not found")
            return
        self._send_bytes(HTTPStatus.OK, file_path.read_bytes(), content_type)

    def _handle_static_path(self, path: str) -> bool:
        if path in ("/", "/web", "/web/", "/web/index.html"):
            self._send_static_file(self.server.web_dir / "index.html", "text/html; charset=utf-8")
            return True
        if path == "/web/app.js":
            self._send_static_file(self.server.web_dir / "app.js", "application/javascript; charset=utf-8")
            return True
        if path == "/web/styles.css":
            self._send_static_file(self.server.web_dir / "styles.css", "text/css; charset=utf-8")
            return True
        return False

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "bad_content_length", "Invalid Content-Length header") from exc
        if length > MAX_REQUEST_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large", "Request body is too large")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
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
            if self._handle_static_path(path):
                return
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "status": "ok"})
            elif path == "/api/events":
                self._handle_get_events(query)
            elif path.startswith("/api/events/"):
                self._handle_get_event(path)
            elif path.startswith("/api/jobs/"):
                self._handle_get_job_path(path)
            else:
                self._send_error(HTTPStatus.NOT_FOUND, "not_found", f"Unknown endpoint: {path}")
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_local_client()
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/api/jobs/preview":
                self._handle_preview_job()
            elif path == "/api/jobs/validate":
                self._handle_validate_job()
            elif path == "/api/jobs":
                self._handle_create_job()
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self._handle_cancel_job(path)
            else:
                self._send_error(HTTPStatus.NOT_FOUND, "not_found", f"Unknown endpoint: {path}")
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _catalogue(self) -> dict[str, Any]:
        try:
            return load_catalogue(self.server.repo_root)
        except CatalogueError as exc:
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "catalogue_error", str(exc)) from exc

    def _handle_get_events(self, query: dict[str, list[str]]) -> None:
        catalogue = self._catalogue()
        q = query.get("q", [""])[0]
        events = search_events(q, catalogue) if q.strip() else [catalogue["events"][event_id] for event_id in sorted(catalogue["events"])]
        self._send_json(HTTPStatus.OK, {"ok": True, "count": len(events), "events": events})

    def _handle_get_event(self, path: str) -> None:
        catalogue = self._catalogue()
        event_ref = unquote(path.split("/api/events/", 1)[1])
        try:
            event = resolve_event(event_ref, catalogue)
        except EventNotFoundError as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, "event_not_found", str(exc)) from exc
        domains = catalogue["domains"]
        resolutions = catalogue["resolution_presets"]
        self._send_json(HTTPStatus.OK, {
            "ok": True,
            "event": event,
            "domain_presets": [domains[preset_id] for preset_id in event.get("domains", []) if preset_id in domains],
            "resolution_presets": [resolutions[preset_id] for preset_id in event.get("resolution_presets", []) if preset_id in resolutions],
        })

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
                resolution_preset_id=payload.get("resolution") or payload.get("resolution_preset") or payload.get("resolution_preset_id"),
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
        self._send_json(HTTPStatus.OK if not errors else HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": not errors, "valid": not errors, "errors": errors, "config": config})

    def _handle_validate_job(self) -> None:
        payload = self._read_json()
        config = self._extract_config(payload)
        errors = self._validate_config(config)
        self._send_json(HTTPStatus.OK if not errors else HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": not errors, "valid": not errors, "errors": errors})

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
        run_dir = self._create_run_dir()
        config = self._server_managed_config(submitted_config, run_dir)
        sanitized_errors = self._validate_config(config)
        if sanitized_errors:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "valid": False, "errors": sanitized_errors})
            return
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="config-", suffix=".json", dir=str(run_dir), delete=False) as config_file:
            json.dump(config, config_file, indent=2)
            config_file.write("\n")
            config_path = Path(config_file.name).resolve()
        if not self._is_path_under(config_path, run_dir):
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "bad_config_path", "Server-created config file is outside the API run directory")
        self._write_run_metadata(run_dir, job_id, "created")
        self._write_index_record(job_id, run_dir.name, "created")
        if not bool(payload.get("start", True)):
            self._send_json(HTTPStatus.CREATED, {"ok": True, "job": self._job_summary(job_id)})
            return
        result = self._run_workbench(config_path, run_dir)
        self._write_run_metadata(run_dir, job_id, result["status"])
        self._write_index_record(job_id, run_dir.name, result["status"])
        self._send_json(HTTPStatus.CREATED if result["exit_code"] == 0 else HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": result["exit_code"] == 0, "job": self._job_summary(job_id), "run": result})

    def _create_run_dir(self) -> Path:
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=str(self.server.api_runs_dir))).resolve()
        if not self._is_path_under(run_dir, self.server.api_runs_dir):
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "bad_run_dir", "Server-created run directory is outside the API run root")
        return run_dir

    def _server_managed_config(self, submitted_config: dict[str, Any], run_dir: Path) -> dict[str, Any]:
        config = copy.deepcopy(submitted_config)
        config.setdefault("outputs", {})
        config["outputs"]["directory"] = str(run_dir.relative_to(self.server.repo_root))
        metadata = config.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["api_run_name"] = run_dir.name
        return config

    def _run_workbench(self, config_path: Path, run_dir: Path) -> dict[str, Any]:
        start_time = time.time()
        command = ["sh", str(self.server.repo_root / "workbench" / "run.sh"), str(config_path)]
        completed = subprocess.run(command, cwd=str(self.server.repo_root), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "api-run.log").write_text("COMMAND: sh workbench/run.sh <server-managed-config>\n\nSTDOUT:\n" + completed.stdout + "\nSTDERR:\n" + completed.stderr, encoding="utf-8")
        return {"command": ["sh", "workbench/run.sh", "<server-managed-config>"], "exit_code": completed.returncode, "duration_seconds": round(time.time() - start_time, 3), "status": "succeeded" if completed.returncode == 0 else "failed"}

    def _read_index(self) -> dict[str, Any]:
        try:
            index = json.loads(self.server.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            index = {"jobs": {}}
        return index if isinstance(index, dict) and isinstance(index.get("jobs"), dict) else {"jobs": {}}

    def _write_index(self, index: dict[str, Any]) -> None:
        self.server.index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

    def _write_index_record(self, job_id: str, run_name: str, state: str) -> None:
        index = self._read_index()
        index["jobs"][job_id] = {"job_id": job_id, "run_name": run_name, "state": state, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self._write_index(index)

    def _write_run_metadata(self, run_dir: Path, job_id: str, state: str) -> None:
        metadata = {"job_id": job_id, "run_name": run_dir.name, "state": state, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        (run_dir / "api-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    def _run_dir_candidates(self) -> list[Path]:
        if self.server.api_runs_dir.is_symlink() or not self.server.api_runs_dir.is_dir():
            return []
        candidates: list[Path] = []
        for child in sorted(self.server.api_runs_dir.iterdir()):
            if child.is_symlink() or not child.is_dir() or not child.name.startswith("run-"):
                continue
            resolved = child.resolve()
            if self._is_path_under(resolved, self.server.api_runs_dir):
                candidates.append(resolved)
        return candidates

    def _find_run_dir_for_job(self, job_id: str) -> tuple[Path, dict[str, Any]]:
        if not JOB_ID_RE.match(job_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_job_id", f"Invalid job id: {job_id!r}")
        for run_dir in self._run_dir_candidates():
            metadata = self._read_optional_json(run_dir / "api-metadata.json")
            if isinstance(metadata, dict) and metadata.get("job_id") == job_id:
                return run_dir, metadata
        raise ApiError(HTTPStatus.NOT_FOUND, "job_not_found", f"Unknown job: {job_id}")

    def _job_summary(self, job_id: str) -> dict[str, Any]:
        run_dir, metadata = self._find_run_dir_for_job(job_id)
        status = self._read_optional_json(run_dir / "status.json")
        job = self._read_optional_json(run_dir / "job.json")
        logs_dir = run_dir / "logs"
        outputs_dir = run_dir / "outputs"
        visualization_dir = run_dir / "visualizations"
        metadata_file = visualization_dir / "metadata.json"
        return {"job_id": job_id, "run_token": metadata.get("run_name", run_dir.name), "run_dir": str(run_dir), "status": status or {"job_id": job_id, "status": metadata.get("state", "created")}, "job": job, "logs": self._list_files(logs_dir), "outputs": self._list_files(outputs_dir), "visualization": {"available": self._safe_file_exists(metadata_file, visualization_dir), "path": str(visualization_dir), "metadata": "metadata.json" if self._safe_file_exists(metadata_file, visualization_dir) else None}}

    def _read_optional_json(self, path: Path) -> Any | None:
        if not self._safe_file_exists(path, path.parent):
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _safe_file_exists(self, path: Path, base_dir: Path) -> bool:
        if path.is_symlink() or not path.is_file():
            return False
        try:
            return self._is_path_under(path.resolve(), base_dir.resolve())
        except OSError:
            return False

    def _is_path_under(self, path: Path, base_dir: Path) -> bool:
        try:
            return os.path.commonpath([str(base_dir.resolve()), str(path.resolve())]) == str(base_dir.resolve())
        except OSError:
            return False

    def _iter_safe_files(self, directory: Path, recursive: bool = True, limit: int = 100) -> list[Path]:
        if directory.is_symlink() or not directory.is_dir():
            return []
        try:
            base_resolved = directory.resolve()
        except OSError:
            return []
        iterator = directory.rglob("*") if recursive else directory.iterdir()
        files: list[Path] = []
        for path in sorted(iterator):
            if len(files) >= limit:
                break
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if os.path.commonpath([str(base_resolved), str(path.resolve())]) == str(base_resolved):
                    files.append(path)
            except OSError:
                continue
        return files

    def _list_files(self, directory: Path, limit: int = 100) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for path in self._iter_safe_files(directory, recursive=True, limit=limit):
            try:
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
        elif len(parts) == 4 and parts[3] == "logs":
            self._handle_get_logs(job_id)
        elif len(parts) == 4 and parts[3] == "outputs":
            job = self._job_summary(job_id)
            self._send_json(HTTPStatus.OK, {"ok": True, "job_id": job_id, "outputs": job["outputs"]})
        elif len(parts) == 4 and parts[3] == "visualization":
            job = self._job_summary(job_id)
            self._send_json(HTTPStatus.OK, {"ok": True, "job_id": job_id, "visualization": job["visualization"]})
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", f"Unknown endpoint: {path}")

    def _handle_get_logs(self, job_id: str) -> None:
        run_dir, _metadata = self._find_run_dir_for_job(job_id)
        logs = []
        for path in self._iter_safe_files(run_dir / "logs", recursive=False, limit=100):
            content = path.read_text(encoding="utf-8", errors="replace")[:MAX_LOG_BYTES]
            logs.append({"name": path.name, "relative_path": path.name, "size_bytes": path.stat().st_size, "content": content})
        self._send_json(HTTPStatus.OK, {"ok": True, "job_id": job_id, "logs": logs})

    def _handle_cancel_job(self, path: str) -> None:
        parts = [unquote(part) for part in path.split("/") if part]
        job_id = parts[2] if len(parts) >= 3 else "unknown"
        self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "job_id": job_id, "error": {"code": "cancel_not_implemented", "message": "Job cancellation is not implemented yet for the local synchronous runner."}})


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local WRF Workbench API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
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
