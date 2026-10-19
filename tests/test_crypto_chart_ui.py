"""Regression checks for the static BTC/ETH TradingView Lightweight Charts UI.

Offline-only: verifies wiring, not a substitute for real browser smoke tests.
"""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "dashboard" / "crypto.html"
JS = ROOT / "dashboard" / "crypto-chart.js"


class CryptoChartUIStaticTests(unittest.TestCase):
    def test_supported_open_source_chart_library_pinned(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js", html)
        self.assertIn('<script src="crypto-chart.js"></script>', html)
        self.assertIn("TradingView Lightweight Charts", html)
        self.assertNotIn("charting_library/", html)
        self.assertIn('<div id="chart"', html)
        self.assertNotIn('<canvas id="chart"', html)

    def test_all_dashboard_control_ids_are_present_and_unique(self):
        html = HTML.read_text(encoding="utf-8")
        javascript = JS.read_text(encoding="utf-8")
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), "Duplicate DOM id in crypto dashboard")
        referenced = set(re.findall(r"\$\('([^']+)'\)", javascript))
        self.assertTrue(referenced)
        self.assertFalse(referenced.difference(ids), "Chart JS references missing controls")

    def test_zoom_pan_indicators_markers_and_export_are_wired(self):
        javascript = JS.read_text(encoding="utf-8")
        for required in (
            "createChart(", "mouseWheel:true", "subscribeCrosshairMove(",
            "getVisibleLogicalRange()", "setVisibleLogicalRange(",
            "createSeriesMarkers(", "coordinateToPrice(",
            "createPriceLine(", "chart.addSeries(", "chart.removeSeries(",
            "takeScreenshot(true)", "show-rsi", "show-volume", "EMA",
            "function compress(", "function render(", "NeonTradingChart",
        ):
            self.assertIn(required, javascript, required)

    def test_chart_uses_stored_paper_fills_and_real_exchange_data(self):
        html = HTML.read_text(encoding="utf-8")
        javascript = JS.read_text(encoding="utf-8")
        self.assertIn("trades:snapshot?.trades||[]", html)
        self.assertIn("rows,symbol,source:selection.interval", html)
        self.assertIn("lastTrades", javascript)
        self.assertIn("item.complete !== false", javascript)
        self.assertIn("exchange", javascript.lower())


if __name__ == "__main__":
    unittest.main()
