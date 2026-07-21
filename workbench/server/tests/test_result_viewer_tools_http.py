#!/usr/bin/env python3
"""HTTP contract for integrated result geography and export tools."""

from __future__ import annotations

import unittest

from workbench.server.tests.test_integrated_result_viewer import (
    JOB_ID,
    IntegratedResultViewerTests,
)


class ResultViewerToolsHttpTests(unittest.TestCase):
    def test_integrated_viewer_loads_same_origin_map_tools(self) -> None:
        fixture = IntegratedResultViewerTests(
            methodName="test_manifest_viewer_and_indexed_products_are_served"
        )
        fixture.setUp()
        try:
            headers, body = fixture.request(f"/jobs/{JOB_ID}/results")
            html = body.decode("utf-8")
            self.assertIn(
                '<script src="/web/result-viewer-tools.js"></script>', html
            )
            csp = headers["Content-Security-Policy"]
            self.assertIn("script-src 'self' 'unsafe-inline'", csp)
            self.assertIn("img-src 'self' data: blob:", csp)

            script_headers, script = fixture.request("/web/result-viewer-tools.js")
            self.assertTrue(
                script_headers["Content-Type"].startswith("application/javascript")
            )
            rendered = script.decode("utf-8")
            self.assertIn("buildPointCsv", rendered)
            self.assertIn("buildLayerGeoJson", rendered)
            self.assertIn("northUpGridCell", rendered)
            self.assertNotIn("tile.openstreetmap.org", rendered)
            self.assertNotIn("https://", rendered)
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
