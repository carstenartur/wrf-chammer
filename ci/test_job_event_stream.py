#!/usr/bin/env python3
"""Unit tests for durable Server-Sent Events replay semantics."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from workbench.job_event_stream import stream_job_events  # noqa: E402


class FakeHandler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


class FakeService:
    def __init__(self, terminal_state="SUCCEEDED"):
        self.terminal_state = terminal_state
        self.all_events = [
            {
                "id": 1,
                "job_id": "stream-job",
                "created_at": "2026-01-01T00:00:00Z",
                "event_type": "job-created",
                "state": "QUEUED",
                "step_name": None,
                "message": "created",
                "details": {},
            },
            {
                "id": 2,
                "job_id": "stream-job",
                "created_at": "2026-01-01T00:00:01Z",
                "event_type": "job-finished",
                "state": terminal_state,
                "step_name": "workbench-run",
                "message": "finished",
                "details": {},
            },
        ]

    def events(self, _job_id, after_id=0, limit=200):
        return [event for event in self.all_events if event["id"] > after_id][:limit]

    def get(self, _job_id):
        return {"job_id": "stream-job", "state": self.terminal_state, "attempt": 1}


def parse_data_blocks(rendered: str):
    payloads = []
    for block in rendered.split("\n\n"):
        data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), None)
        if data:
            payloads.append(json.loads(data))
    return payloads


def main() -> int:
    handler = FakeHandler()
    service = FakeService()
    stream_job_events(handler, service, "stream-job", after_id=0, timeout_seconds=1)
    rendered = handler.wfile.getvalue().decode("utf-8")
    assert handler.status == 200
    assert handler.headers["Content-Type"].startswith("text/event-stream")
    assert "retry: 1000" in rendered
    assert "id: 1" in rendered
    assert "id: 2" in rendered
    assert "event: job-complete" in rendered
    payloads = parse_data_blocks(rendered)
    assert payloads[0]["event_type"] == "job-created"
    assert payloads[1]["event_type"] == "job-finished"
    assert payloads[2]["state"] == "SUCCEEDED"

    reconnect = FakeHandler()
    stream_job_events(reconnect, service, "stream-job", after_id=1, timeout_seconds=1)
    reconnect_rendered = reconnect.wfile.getvalue().decode("utf-8")
    assert "id: 1" not in reconnect_rendered
    assert "id: 2" in reconnect_rendered
    assert "event: job-complete" in reconnect_rendered

    print("Job event stream replay tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
