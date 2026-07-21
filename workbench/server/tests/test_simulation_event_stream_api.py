#!/usr/bin/env python3
"""HTTP integration test for persistent simulation event replay and SSE."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workbench.server.application import WorkbenchApplicationHandler
from workbench.server.server import WorkbenchApiServer
from workbench.server.tests.test_simulation_worker import SPEC_KEY, prepare_environment


class SimulationEventStreamApiTests(unittest.TestCase):
    def test_json_cursor_and_sse_last_event_id(self) -> None:
        with tempfile.TemporaryDirectory(prefix="simulation-stream-api-") as temporary:
            root = Path(temporary)
            _data, _specifications, store = prepare_environment(root)
            job = store.create_job(SPEC_KEY)
            store.enqueue_job(job["id"])
            store.request_cancel(job["id"])

            server = WorkbenchApiServer(
                ("127.0.0.1", 0), WorkbenchApplicationHandler, root
            )
            server.simulation_store = store
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urlopen(
                    f"{base}/api/simulations/{job['id']}/events?after=1",
                    timeout=5,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(200, response.status)
                self.assertEqual(1, payload["after"])
                self.assertTrue(payload["events"])
                self.assertTrue(
                    all(event["sequence"] > 1 for event in payload["events"])
                )

                request = Request(
                    f"{base}/api/simulations/{job['id']}/events/stream",
                    headers={"Accept": "text/event-stream", "Last-Event-ID": "1"},
                )
                with urlopen(request, timeout=5) as response:
                    rendered = response.read().decode("utf-8")
                    content_type = response.headers.get_content_type()
                self.assertEqual("text/event-stream", content_type)
                self.assertNotIn("id: 1\n", rendered)
                self.assertIn("event: simulation-event", rendered)
                self.assertIn("event: simulation-complete", rendered)
                self.assertIn('"status":"CANCELLED"', rendered)

                with self.assertRaises(HTTPError) as context:
                    urlopen(
                        f"{base}/api/simulations/{job['id']}/events?after=invalid",
                        timeout=5,
                    )
                self.assertEqual(400, context.exception.code)
                error = json.loads(context.exception.read().decode("utf-8"))
                self.assertEqual("invalid_event_cursor", error["error"]["code"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
