#!/usr/bin/env python3
"""Local HTTP API for the WRF Workbench.

The server is deliberately small and dependency-free.  It is a local bridge
between browser UI code and the existing Workbench core/runner scripts.

Security model: local development only.  The default bind address is
127.0.0.1 and the API executes local Workbench scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
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
        self.index_dir = self.repo_root / "workbench-runs" / ".api-index"
        self.index_dir.mkdir(parents=True, exist_ok=True)


class WorkbenchApiHandler(BaseHTTPRequestHandler):
    """Request handler for the local Workbench API."""

    server: WorkbenchApiServer

    # ── HTTP plumbing ────────────────────────────────────────────────────────

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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
        try:
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_json", f"Request body is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_body", "Request body must be a JSON object")
        return payload

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
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

    # ── Events ────────────────────────────────────────────────────────────────

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

    # ── Job validation, preview and execution ────────────────────────────────

    def _extract_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = payload.get("config", payload)
        if not isinstance(config, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_config", "Field 'config' must be a JSON object")
        return config

    def _validate_config(self, config: dict[str, Any]) -> list[str]:
        # workbench.validate resolves relative ERA5 paths from cwd, so the server
        # process is started with cwd=repo_root and main() also chdirs there.
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
                resolution_preset_id=(
                    payload.get("resolution")
                    or payload.get("resolution_preset")
                    or payload.get("resolution_preset_id")
                ),
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
        config = self._extract_config(payload)
        start = bool(payload.get("start", True))
        errors = self._validate_config(config)
        if errors:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "valid": False, "errors": errors})
            return

        job_id = config["id"]
        if not JOB_ID_RE.match(job_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_job_id", f"Invalid job id: {job_id!r}")

        run_dir = self._resolve_run_dir(config)
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = run_dir / "api-config.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self._write_index(job_id, run_dir, config_path, state="created")

        if not start:
            self._send_json(HTTPStatus.CREATED, {"ok": True, "job": self._job_summary(job_id, run_dir)})
            return

        result = self._run_workbench(config_path, run_dir)
        self._write_index(job_id, run_dir, config_path, state=result["status"])
        status_code = HTTPStatus.CREATED if result["exit_code"] == 0 else HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_json(status_code, {"ok": result["exit_code"] == 0, "job": self._job_summary(job_id, run_dir), "run": result})

    def _resolve_run_dir(self, config: dict[str, Any]) -> Path:
        outputs_dir = config["outputs"]["directory"]
        path = Path(outputs_dir)
        if path.is_absolute():
            return path
        return self.server.repo_root / path

    def _run_workbench(self, config_path: Path, run_dir: Path) -> dict[str, Any]:
        start_time = time.time()
        command = ["sh", str(self.server.repo_root / "workbench" / "run.sh"), str(config_path)]
        completed = subprocess.run(
            command,
            cwd=str(self.server.repo_root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        (run_dir / "logs" / "api-run.log").write_text(
            "COMMAND: " + " ".join(command) + "\n\n" +
            "STDOUT:\n" + completed.stdout + "\nSTDERR:\n" + completed.stderr,
            encoding="utf-8",
        )
        return {
            "command": command,
            "exit_code": completed.returncode,
            "duration_seconds": round(time.time() - start_time, 3),
            "status": "succeeded" if completed.returncode == 0 else "failed",
        }

    # ── Job lookup, logs and outputs ─────────────────────────────────────────

    def _index_path(self, job_id: str) -> Path:
        if not JOB_ID_RE.match(job_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_job_id", f"Invalid job id: {job_id!r}")
        return self.server.index_dir / f"{job_id}.json"

    def _write_index(self, job_id: str, run_dir: Path, config_path: Path, state: str) -> None:
        self.server.index_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "job_id": job_id,
            "run_dir": str(run_dir),
            "config_path": str(config_path),
            "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._index_path(job_id).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    def _load_index(self, job_id: str) -> dict[str, Any]:
        path = self._index_path(job_id)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))

        # Best-effort fallback for manually created run directories under
        # workbench-runs/.  This keeps GET status useful even if the server was
        # restarted and the index is missing.
        runs_root = self.server.repo_root / "workbench-runs"
        if runs_root.is_dir():
            for status_path in runs_root.glob("*/status.json"):
                try:
                    status = json.loads(status_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if status.get("job_id") == job_id:
                    run_dir = status_path.parent
                    return {"job_id": job_id, "run_dir": str(run_dir), "state": status.get("status", "unknown")}
        raise ApiError(HTTPStatus.NOT_FOUND, "job_not_found", f"Unknown job: {job_id}")

    def _job_summary(self, job_id: str, run_dir: Path | None = None) -> dict[str, Any]:
        record = self._load_index(job_id) if run_dir is None else {"job_id": job_id, "run_dir": str(run_dir)}
        resolved_run_dir = Path(record["run_dir"])
        status = self._read_optional_json(resolved_run_dir / "status.json")
        job = self._read_optional_json(resolved_run_dir / "job.json")
        logs_dir = resolved_run_dir / "logs"
        outputs_dir = resolved_run_dir / "outputs"
        visualization_dir = resolved_run_dir / "visualizations"
        return {
            "job_id": job_id,
            "run_dir": str(resolved_run_dir),
            "status": status or {"job_id": job_id, "status": record.get("state", "created")},
            "job": job,
            "logs": self._list_files(logs_dir),
            "outputs": self._list_files(outputs_dir),
            "visualization": {
                "available": (visualization_dir / "metadata.json").is_file(),
                "path": str(visualization_dir),
                "metadata": str(visualization_dir / "metadata.json") if (visualization_dir / "metadata.json").is_file() else None,
            },
        }

    def _read_optional_json(self, path: Path) -> Any | None:
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def _list_files(self, directory: Path, limit: int = 100) -> list[dict[str, Any]]:
        if not directory.is_dir():
            return []
        files: list[dict[str, Any]] = []
        for path in sorted(p for p in directory.rglob("*") if p.is_file())[:limit]:
            files.append({
                "name": path.name,
                "path": str(path),
                "relative_path": str(path.relative_to(directory)),
                "size_bytes": path.stat().st_size,
            })
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
        record = self._load_index(job_id)
        logs_dir = Path(record["run_dir"]) / "logs"
        logs = []
        for entry in self._list_files(logs_dir):
            path = Path(entry["path"])
            content = path.read_text(encoding="utf-8", errors="replace")
            logs.append({**entry, "content": content})
        self._send_json(HTTPStatus.OK, {"ok": True, "job_id": job_id, "logs": logs})

    def _handle_cancel_job(self, path: str) -> None:
        parts = [unquote(part) for part in path.split("/") if part]
        job_id = parts[2] if len(parts) >= 3 else "unknown"
        self._send_json(
            HTTPStatus.NOT_IMPLEMENTED,
            {
                "ok": False,
                "job_id": job_id,
                "error": {
                    "code": "cancel_not_implemented",
                    "message": "Job cancellation is not implemented yet for the local synchronous runner.",
                },
            },
        )


# ── Command line ─────────────────────────────────────────────────────────────


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
