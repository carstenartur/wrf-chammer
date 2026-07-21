#!/usr/bin/env python3
"""Reconnectable Server-Sent Events for persistent simulation jobs."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from workbench.simulation_store import SimulationStore

_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def parse_event_cursor(value: str | None) -> int:
    if value is None or not value.strip():
        return 0
    text = value.strip()
    if not text.isascii() or not text.isdecimal():
        raise ValueError("Last-Event-ID must be a non-negative integer")
    cursor = int(text)
    if cursor < 0:
        raise ValueError("Last-Event-ID must be a non-negative integer")
    return cursor


def encode_sse_event(event: dict[str, Any]) -> bytes:
    sequence = event.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValueError("Persistent simulation event has no valid sequence")
    payload = json.dumps(event, separators=(",", ":"), ensure_ascii=True)
    return (
        f"id: {sequence}\n"
        "event: simulation-event\n"
        f"data: {payload}\n\n"
    ).encode("utf-8")


def stream_simulation_events(
    handler: Any,
    store: SimulationStore,
    job_id: str,
    *,
    after_sequence: int = 0,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Write a finite replay/live SSE response.

    The response closes after terminal completion or the bounded timeout. Native
    ``EventSource`` reconnects with ``Last-Event-ID`` and therefore resumes from
    the next persistent sequence without duplicates.
    """

    timeout = max(0.1, min(300.0, float(timeout_seconds)))
    poll = max(0.05, min(5.0, float(poll_seconds)))
    cursor = max(0, int(after_sequence))
    deadline = monotonic() + timeout
    heartbeat_at = monotonic() + min(10.0, max(1.0, timeout / 3.0))

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()
    handler.close_connection = True
    handler.wfile.write(b"retry: 1000\n\n")
    handler.wfile.flush()

    try:
        while monotonic() < deadline:
            events = store.events_after(
                job_id, after_sequence=cursor, limit=200
            )
            for event in events:
                handler.wfile.write(encode_sse_event(event))
                cursor = int(event["sequence"])
            if events:
                handler.wfile.flush()

            job = store.get_job(job_id)
            if job["status"] in _TERMINAL:
                remaining = store.events_after(
                    job_id, after_sequence=cursor, limit=1
                )
                if not remaining:
                    completion = json.dumps(
                        {
                            "job_id": job_id,
                            "status": job["status"],
                            "last_sequence": cursor,
                        },
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    handler.wfile.write(
                        (
                            "event: simulation-complete\n"
                            f"data: {completion}\n\n"
                        ).encode("utf-8")
                    )
                    handler.wfile.flush()
                    return

            now = monotonic()
            if now >= heartbeat_at:
                handler.wfile.write(b": keep-alive\n\n")
                handler.wfile.flush()
                heartbeat_at = now + 10.0
            sleep(poll)
    except (BrokenPipeError, ConnectionResetError):
        return


__all__ = [
    "encode_sse_event",
    "parse_event_cursor",
    "stream_simulation_events",
]
