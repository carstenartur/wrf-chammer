#!/usr/bin/env python3
"""Workbench application entry point with durable event streaming."""

from __future__ import annotations

import argparse
import sys
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workbench.job_event_stream import stream_job_events  # noqa: E402
from workbench.persistent_jobs import JobNotFoundError  # noqa: E402
from workbench.server.application import (  # noqa: E402
    WorkbenchApplicationHandler,
    build_arg_parser,
)
from workbench.server.server import ApiError, WorkbenchApiServer  # noqa: E402


class StreamingWorkbenchApplicationHandler(WorkbenchApplicationHandler):
    """Add SSE replay and resource telemetry without duplicating API routing."""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) == 5 and parts[:2] == ["api", "jobs"] and parts[3:] == ["events", "stream"]:
            self._handle_event_stream(parts[2], parse_qs(parsed.query))
            return
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "resources":
            self._handle_resources(parts[2])
            return
        super().do_GET()

    def _handle_resources(self, job_id: str) -> None:
        try:
            self._require_local_client()
            payload = self._persistent_jobs().resources(job_id)
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "job_id": job_id, **payload},
            )
        except JobNotFoundError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, "job_not_found", str(exc))
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "application_error", str(exc))

    def _handle_event_stream(self, job_id: str, query: dict[str, list[str]]) -> None:
        try:
            self._require_local_client()
            service = self._persistent_jobs()
            if not service.exists(job_id):
                raise JobNotFoundError(f"Unknown job: {job_id}")
            raw_after = query.get("after_id", [self.headers.get("Last-Event-ID", "0")])[0]
            raw_timeout = query.get("timeout", ["25"])[0]
            try:
                after_id = max(0, int(raw_after or 0))
                timeout = max(1.0, min(float(raw_timeout), 60.0))
            except ValueError as exc:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_stream_query",
                    "after_id must be an integer and timeout must be a number",
                ) from exc
            stream_job_events(
                self,
                service,
                job_id,
                after_id=after_id,
                timeout_seconds=timeout,
            )
        except JobNotFoundError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, "job_not_found", str(exc))
        except ApiError as exc:
            self._send_error(exc.status, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "application_error", str(exc))


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace = build_arg_parser().parse_args(argv)
    server = WorkbenchApiServer(
        (args.host, args.port),
        StreamingWorkbenchApplicationHandler,
        args.repo_root,
    )
    print(f"WRF Workbench available at http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
