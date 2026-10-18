import unittest
from datetime import date, timedelta

from paper_trader.config import MIN_RESEARCH_BENCHMARK_BARS, STRATEGIES
from unittest.mock import patch

from paper_trader.research import (
    RISK_POLICY_NIFTY_REENTRY,
    RISK_POLICY_PERMANENT,
    backtest_strategy,
    build_research_report,
)


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
        self.assertEqual(report["open_source_models"][0]["status"], "blocked_insufficient_market_data")
        self.assertFalse(report["open_source_models"][0]["auto_promoted"])
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

        def fake_backtest(
            _bars, strategy, sample_days=756, cost_multiplier=1.0, risk_policy=RISK_POLICY_PERMANENT
        ):
            return {
                "status": "ok", "strategy": strategy, "risk_policy": risk_policy,
                "sample_start": "2024-01-01", "sample_end": "2026-01-01", "sessions": sample_days,
                "total_return_pct": 5.0 / cost_multiplier, "annualized_return_pct": 2.0,
                "sharpe_approx": 0.5, "max_drawdown_pct": 8.0 * cost_multiplier,
                "benchmark_total_return_pct": 3.0, "excess_return_vs_nifty_pct": 2.0 / cost_multiplier,
                "trade_count": 10, "risk_lock_triggered": True, "risk_lock_date": "2024-06-01",
                "risk_lock_events": 1,
                "risk_reentry_dates": ["2025-03-01"] if risk_policy == RISK_POLICY_NIFTY_REENTRY else [],
                "risk_reentry_count": 1 if risk_policy == RISK_POLICY_NIFTY_REENTRY else 0,
                "risk_lock_active_at_end": False if risk_policy == RISK_POLICY_NIFTY_REENTRY else True,
            }

        with patch("paper_trader.research.backtest_strategy", side_effect=fake_backtest) as mocked:
            report = build_research_report({"__BENCHMARK__": benchmark, **stocks})

        self.assertEqual(report["status"], "ok")
        self.assertEqual(mocked.call_count, len(STRATEGIES) * 9 + 6)
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
        comparison = report["risk_recovery_comparison"]
        self.assertEqual(comparison["status"], "ok")
        self.assertFalse(comparison["applies_to_scheduled_paper_trader"])
        self.assertEqual(
            [item["policy"] for item in comparison["policies"]],
            [RISK_POLICY_PERMANENT, RISK_POLICY_NIFTY_REENTRY],
        )
        self.assertEqual(comparison["policies"][1]["walk_forward"]["completed_folds"], 4)
        self.assertEqual(len(comparison["policies"][1]["walk_forward"]["folds"]), 4)

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


    def test_reentry_policy_waits_for_nifty_reclaim_and_leaves_default_lock_unchanged(self):
        count = 550
        start = date(2025, 1, 1)
        benchmark = []
        stock = []
        for index in range(count):
            day = (start + timedelta(days=index)).isoformat()
            index_close = 100.0 if index < 430 else (99.0 if index < 460 else 160.0)
            stock_close = 100.0 if index < 435 else max(40.0, 100.0 - 15.0 * (index - 434))
            benchmark.append({"date": day, "open": index_close, "close": index_close})
            stock.append({"date": day, "open": stock_close, "close": stock_close})

        bars = {"__BENCHMARK__": benchmark, "S0": stock}
        with patch("paper_trader.research.select_targets", return_value=["S0"]):
            permanent = backtest_strategy(
                bars, "trend_momentum_126", sample_days=120, risk_policy=RISK_POLICY_PERMANENT
            )
        with patch("paper_trader.research.select_targets", return_value=["S0"]):
            recovery = backtest_strategy(
                bars, "trend_momentum_126", sample_days=120, risk_policy=RISK_POLICY_NIFTY_REENTRY
            )

        self.assertTrue(permanent["risk_lock_triggered"])
        self.assertEqual(permanent["risk_lock_events"], 1)
        self.assertEqual(permanent["risk_reentry_count"], 0)
        self.assertTrue(permanent["risk_lock_active_at_end"])
        self.assertTrue(recovery["risk_lock_triggered"])
        self.assertGreaterEqual(recovery["risk_reentry_count"], 1)
        self.assertGreater(recovery["risk_reentry_dates"][0], recovery["risk_lock_date"])
        self.assertGreater(recovery["sessions_with_positions"], permanent["sessions_with_positions"])
        self.assertEqual(permanent["risk_policy"], RISK_POLICY_PERMANENT)
        self.assertEqual(recovery["risk_policy"], RISK_POLICY_NIFTY_REENTRY)

        # If the market stays above its 200-session average through the lock, that is
        # not a recovery event: the rule must not unlock immediately or re-enter.
        above_benchmark = []
        for index in range(count):
            day = (start + timedelta(days=index)).isoformat()
            index_close = 100.0 if index < 430 else 160.0
            above_benchmark.append({"date": day, "open": index_close, "close": index_close})
        above_bars = {"__BENCHMARK__": above_benchmark, "S0": stock}
        with patch("paper_trader.research.select_targets", return_value=["S0"]):
            no_recovery = backtest_strategy(
                above_bars, "trend_momentum_126", sample_days=120, risk_policy=RISK_POLICY_NIFTY_REENTRY
            )
        self.assertTrue(no_recovery["risk_lock_triggered"])
        self.assertEqual(no_recovery["risk_reentry_count"], 0)
        self.assertTrue(no_recovery["risk_lock_active_at_end"])


if __name__ == "__main__":
    unittest.main()
