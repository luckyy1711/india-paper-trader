import unittest
from datetime import date, timedelta

from paper_trader.config import STRATEGIES
from paper_trader.research import build_research_report


def make_bars(count, price):
    start = date(2025, 1, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "open": float(price + index * 0.1),
            "close": float(price + index * 0.1),
        }
        for index in range(count)
    ]


class ResearchQualityGateTests(unittest.TestCase):
    def test_incomplete_universe_withholds_backtest_metrics(self):
        report = build_research_report({
            "__BENCHMARK__": make_bars(320, 100),
            "RELIANCE": make_bars(320, 200),
        })

        self.assertEqual(report["status"], "blocked_insufficient_market_data")
        self.assertEqual(report["data_quality"]["status"], "blocked_insufficient_market_data")
        self.assertEqual(report["market_data_coverage"]["stock_symbols_downloaded"], 1)
        self.assertEqual(len(report["results"]), len(STRATEGIES))
        for result in report["results"]:
            self.assertEqual(result["status"], "blocked_insufficient_market_data")
            self.assertNotIn("total_return_pct", result)
            self.assertNotIn("sharpe_approx", result)
        self.assertIn("metrics are withheld", report["data_quality"]["message"])


if __name__ == "__main__":
    unittest.main()
