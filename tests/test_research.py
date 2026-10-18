import unittest
from datetime import date, timedelta

from paper_trader.config import MIN_RESEARCH_BENCHMARK_BARS, STRATEGIES
from unittest.mock import patch

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

    def test_research_runs_period_and_cost_sensitivity_without_changing_active_rule(self):
        benchmark = make_bars(1000, 100)
        stocks = {f"S{i}": make_bars(350, 50 + i) for i in range(50)}

        def fake_backtest(_bars, strategy, sample_days=756, cost_multiplier=1.0):
            return {
                "status": "ok", "strategy": strategy, "sample_start": "2024-01-01",
                "sample_end": "2026-01-01", "sessions": sample_days,
                "total_return_pct": 5.0 / cost_multiplier, "annualized_return_pct": 2.0,
                "sharpe_approx": 0.5, "max_drawdown_pct": 8.0 * cost_multiplier,
                "benchmark_total_return_pct": 3.0, "excess_return_vs_nifty_pct": 2.0 / cost_multiplier,
                "trade_count": 10,
            }

        with patch("paper_trader.research.backtest_strategy", side_effect=fake_backtest) as mocked:
            report = build_research_report({"__BENCHMARK__": benchmark, **stocks})

        self.assertEqual(report["status"], "ok")
        self.assertEqual(mocked.call_count, len(STRATEGIES) * 9)
        for result in report["results"]:
            self.assertIn("robustness", result)
            self.assertEqual(set(result["robustness"]["period_sensitivity"]), {"252", "504", "756"})
            self.assertEqual(set(result["robustness"]["cost_sensitivity"]), {"1.0", "1.5", "2.0"})
            self.assertEqual(result["robustness"]["cost_scenarios_tested"], 3)
            self.assertEqual(result["walk_forward"]["status"], "ok")
            self.assertEqual(result["walk_forward"]["completed_folds"], 4)
            self.assertEqual(len(result["walk_forward"]["folds"]), 4)
        self.assertEqual(report["active_strategy"], "trend_momentum_126")
        self.assertFalse(report["active_strategy_changes_automatically"])

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
