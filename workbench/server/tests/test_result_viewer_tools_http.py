#!/usr/bin/env python3
"""HTTP contract for integrated result geography and export tools."""

from __future__ import annotations

import unittest

from workbench.server.tests.test_integrated_result_viewer import (
    JOB_ID,
    REPO_ROOT,
    IntegratedResultViewerTests,
)

TOOLS_SCRIPT = '<script src="/web/result-viewer-tools.js"></script>'


class ResultViewerToolsHttpTests(unittest.TestCase):
    def test_integrated_viewer_loads_same_origin_map_tools(self) -> None:
        fixture = IntegratedResultViewerTests(
            methodName="test_manifest_viewer_and_indexed_products_are_served"
        )
        fixture.setUp()
        try:
            target = fixture.root / "workbench" / "web" / "result-viewer-tools.js"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                (REPO_ROOT / "workbench" / "web" / "result-viewer-tools.js").read_bytes()
            )

            headers, body = fixture.request(f"/jobs/{JOB_ID}/results")
            html = body.decode("utf-8")
            self.assertIn(TOOLS_SCRIPT, html)
            self.assertEqual(1, html.count(TOOLS_SCRIPT))
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

            viewer_path = fixture.service.viewer_path
            viewer_html = viewer_path.read_text(encoding="utf-8")
            viewer_path.write_text(
                viewer_html.replace("</body>", f"{TOOLS_SCRIPT}\n</body>", 1),
                encoding="utf-8",
            )
            already_integrated = fixture.service.viewer_html(JOB_ID).decode("utf-8")
            self.assertEqual(1, already_integrated.count(TOOLS_SCRIPT))
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
