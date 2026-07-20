#!/usr/bin/env python3
"""Server-Sent Events adapter for the durable persistent job event log."""

from __future__ import annotations

import json
import time
from typing import Any

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


def _write(handler: Any, text: str) -> None:
    handler.wfile.write(text.encode("utf-8"))
    handler.wfile.flush()


def stream_job_events(
    handler: Any,
    service: Any,
    job_id: str,
    *,
    after_id: int = 0,
    timeout_seconds: float = 25.0,
    heartbeat_seconds: float = 3.0,
) -> None:
    """Stream durable events, then return so EventSource can reconnect safely."""

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache, no-transform")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "close")
    handler.end_headers()

    current_id = max(0, int(after_id))
    deadline = time.monotonic() + max(1.0, min(float(timeout_seconds), 60.0))
    next_heartbeat = time.monotonic()
    try:
        _write(handler, "retry: 1000\n\n")
        while time.monotonic() < deadline:
            events = service.events(job_id, after_id=current_id, limit=200)
            for event in events:
                current_id = int(event["id"])
                _write(handler, f"id: {current_id}\n")
                _write(handler, "event: job-event\n")
                _write(handler, f"data: {json.dumps(event, separators=(',', ':'))}\n\n")

            job = service.get(job_id)
            if job["state"] in TERMINAL_STATES:
                payload = {
                    "job_id": job_id,
                    "state": job["state"],
                    "attempt": job["attempt"],
                    "last_event_id": current_id,
                }
                _write(handler, "event: job-complete\n")
                _write(handler, f"data: {json.dumps(payload, separators=(',', ':'))}\n\n")
                return

            now = time.monotonic()
            if now >= next_heartbeat:
                _write(handler, f": heartbeat {int(time.time())}\n\n")
                next_heartbeat = now + max(1.0, heartbeat_seconds)
            time.sleep(0.2)
    except (BrokenPipeError, ConnectionResetError, OSError):
        return
