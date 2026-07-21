#!/usr/bin/env python3
"""Tests for simulation SSE replay, admission and atomic concurrency limits."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from workbench.server.tests.test_simulation_worker import (
    SPEC_KEY,
    prepare_environment,
)
from workbench.simulation_event_stream import (
    parse_event_cursor,
    stream_simulation_events,
)
from workbench.simulation_resources import evaluate_resource_admission
from workbench.simulation_worker import ExternalStepExecutor, SimulationWorker


class FakeSseHandler:
    def __init__(self) -> None:
        self.status = None
        self.headers: dict[str, str] = {}
        self.wfile = io.BytesIO()

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.headers[name] = value

    def end_headers(self) -> None:
        pass


def event_ids(rendered: str) -> list[int]:
    return [
        int(line.removeprefix("id: "))
        for line in rendered.splitlines()
        if line.startswith("id: ")
    ]


class SimulationStreamAndPreflightTests(unittest.TestCase):
    def test_event_cursor_and_terminal_sse_reconnect(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-sse-") as temporary:
            root = Path(temporary)
            _data, _specifications, store = prepare_environment(root)
            job = store.create_job(SPEC_KEY)
            store.enqueue_job(job["id"])
            store.request_cancel(job["id"])

            all_events = store.events_after(job["id"])
            self.assertGreaterEqual(len(all_events), 3)
            self.assertEqual(
                list(range(1, len(all_events) + 1)),
                [event["sequence"] for event in all_events],
            )
            cursor = all_events[0]["sequence"]
            replay = store.events_after(job["id"], after_sequence=cursor)
            self.assertNotIn(cursor, [event["sequence"] for event in replay])

            handler = FakeSseHandler()
            stream_simulation_events(
                handler,
                store,
                job["id"],
                after_sequence=cursor,
                timeout_seconds=0.5,
                poll_seconds=0.01,
            )
            rendered = handler.wfile.getvalue().decode("utf-8")
            self.assertEqual(200, handler.status)
            self.assertEqual(
                "text/event-stream; charset=utf-8",
                handler.headers["Content-Type"],
            )
            self.assertEqual("close", handler.headers["Connection"])
            self.assertTrue(handler.close_connection)
            self.assertNotIn(cursor, event_ids(rendered))
            self.assertEqual(
                [event["sequence"] for event in replay], event_ids(rendered)
            )
            self.assertIn("event: simulation-complete", rendered)
            self.assertIn('"status":"CANCELLED"', rendered)

            self.assertEqual(0, parse_event_cursor(None))
            self.assertEqual(12, parse_event_cursor("12"))
            for invalid in ("-1", "1.5", "abc"):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        parse_event_cursor(invalid)

    def test_record_event_returns_the_new_event_after_large_history(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-event-page-") as temporary:
            root = Path(temporary)
            _data, _specifications, store = prepare_environment(root)
            job = store.create_job(SPEC_KEY)
            for index in range(505):
                appended = store.record_event(
                    job["id"],
                    event_type="test_event",
                    status="READY",
                    message=f"event {index}",
                    details={"index": index},
                )
            self.assertEqual(506, appended["sequence"])
            self.assertEqual("event 504", appended["message"])
            self.assertEqual(504, appended["details"]["index"])

    def test_resource_estimate_rejects_insufficient_host(self) -> None:
        specification = {
            "identity": {
                "job": {
                    "metadata": {
                        "resource_estimate": {
                            "estimated_ram_gb": {
                                "minimum": 8,
                                "recommended": 12,
                            },
                            "estimated_storage_gb": {"working_total": 20},
                        }
                    }
                }
            }
        }
        assessment = evaluate_resource_admission(
            specification,
            {
                "memory_available_bytes": 4 * 1024**3,
                "disk_free_bytes": 10 * 1024**3,
            },
            memory_headroom_fraction=0,
            disk_headroom_fraction=0,
        )
        self.assertFalse(assessment["admitted"])
        self.assertTrue(assessment["estimate_available"])
        self.assertEqual(
            {"memory", "disk"},
            {reason["resource"] for reason in assessment["reasons"]},
        )

    def test_worker_rejects_before_any_step_starts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-preflight-reject-") as temporary:
            root = Path(temporary)
            data, specifications, store = prepare_environment(root)
            specifications.specification["identity"]["job"]["metadata"] = {
                "resource_estimate": {
                    "estimated_ram_gb": {"minimum": 1000, "recommended": 1000},
                    "estimated_storage_gb": {"working_total": 1000},
                }
            }
            job = store.create_job(SPEC_KEY)
            store.enqueue_job(job["id"])
            worker = SimulationWorker(
                root,
                store,
                data,
                specifications,  # type: ignore[arg-type]
                worker_id="preflight-reject",
                executor=ExternalStepExecutor(None),
                poll_seconds=0.01,
                resource_provider=lambda: {
                    "memory_available_bytes": 1024**3,
                    "disk_free_bytes": 1024**3,
                },
            )
            worker.run(once=True)
            result = store.get_job(job["id"])
            self.assertEqual("FAILED", result["status"])
            self.assertEqual("INSUFFICIENT_RESOURCES", result["error"]["code"])
            self.assertEqual({"PENDING"}, {step["status"] for step in result["steps"]})
            self.assertIsNone(result["started_at"])
            self.assertTrue(
                any(
                    event["type"] == "resource_preflight_failed"
                    for event in result["events"]
                )
            )
            self.assertEqual("preflight", result["resource_measurements"][0]["metadata"]["phase"])

    def test_missing_estimate_is_admitted_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-preflight-admit-") as temporary:
            root = Path(temporary)
            data, specifications, store = prepare_environment(root)
            job = store.create_job(SPEC_KEY)
            store.enqueue_job(job["id"])
            worker = SimulationWorker(
                root,
                store,
                data,
                specifications,  # type: ignore[arg-type]
                worker_id="preflight-admit",
                executor=ExternalStepExecutor(None),
                poll_seconds=0.01,
                resource_provider=lambda: {
                    "memory_available_bytes": 8 * 1024**3,
                    "disk_free_bytes": 20 * 1024**3,
                },
            )
            worker.run(once=True)
            result = store.get_job(job["id"])
            self.assertEqual("FAILED", result["status"])
            self.assertEqual("EXECUTOR_UNAVAILABLE", result["error"]["code"])
            event_types = [event["type"] for event in result["events"]]
            self.assertEqual(1, event_types.count("resource_preflight_passed"))
            self.assertLess(
                event_types.index("resource_preflight_passed"),
                event_types.index("step_started"),
            )
            preflight_event = next(
                event
                for event in result["events"]
                if event["type"] == "resource_preflight_passed"
            )
            self.assertFalse(
                preflight_event["details"]["assessment"]["estimate_available"]
            )

    def test_atomic_claim_respects_active_job_limit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-concurrency-") as temporary:
            root = Path(temporary)
            _data, _specifications, store = prepare_environment(root)
            first = store.create_job(SPEC_KEY)
            second = store.create_job(SPEC_KEY)
            store.enqueue_job(first["id"])
            store.enqueue_job(second["id"])
            claimed = store.claim_job(first["id"], "worker-one", max_active_jobs=1)
            self.assertIsNotNone(claimed)
            self.assertEqual(1, store.active_job_count())
            self.assertIsNone(
                store.claim_job(second["id"], "worker-two", max_active_jobs=1)
            )
            self.assertEqual("QUEUED", store.get_job(second["id"])["status"])


if __name__ == "__main__":
    unittest.main()
