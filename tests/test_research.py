import unittest
from datetime import date, timedelta

from paper_trader.config import MIN_RESEARCH_BENCHMARK_BARS, STRATEGIES
from paper_trader.research import backtest_strategy, build_research_report


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


    def test_research_gate_requires_multi_year_history(self):
        self.assertEqual(MIN_RESEARCH_BENCHMARK_BARS, 1000)
        report = build_research_report({
            "__BENCHMARK__": make_bars(999, 100),
            **{f"S{i}": make_bars(350, 50 + i) for i in range(50)},
        })
        self.assertEqual(report["status"], "blocked_insufficient_market_data")
        self.assertTrue(any("benchmark has only 999" in item for item in report["data_quality"]["failed_checks"]))

    def test_backtest_compares_to_benchmark_and_emits_yearly_breakdown(self):
        benchmark = make_bars(420, 100)
        stocks = {f"S{i}": make_bars(420, 70 + i * 3 + i * 0.1) for i in range(50)}
        report = backtest_strategy({"__BENCHMARK__": benchmark, **stocks}, "trend_momentum_126", sample_days=40)
        self.assertEqual(report["status"], "ok")
        self.assertIn("benchmark_total_return_pct", report)
        self.assertIn("excess_return_vs_nifty_pct", report)
        self.assertTrue(report["yearly_results"])
        self.assertTrue(all("benchmark_total_return_pct" in item for item in report["yearly_results"]))
        self.assertIn("risk_lock_triggered", report)
        self.assertFalse(report["risk_lock_triggered"])
        self.assertIsNone(report["risk_lock_date"])
        self.assertEqual(report["post_lock_flat_sessions"], 0)


if __name__ == "__main__":
    unittest.main()
