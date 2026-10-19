import unittest
from datetime import date, timedelta
from unittest.mock import patch

from paper_trader.ml_research import MODEL_NAME
from paper_trader.simulation import build_simulation_report


def make_bars(count, base=100.0):
    start = date(2022, 1, 1)
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "open": base + i * 0.1,
            "high": base + i * 0.1 + 1,
            "low": base + i * 0.1 - 1,
            "close": base + i * 0.1,
            "volume": 10000 + i,
        }
        for i in range(count)
    ]


class SimulationReportTests(unittest.TestCase):
    def test_data_gate_blocks_incomplete_history_without_metrics(self):
        report = build_simulation_report({
            "__BENCHMARK__": make_bars(999),
            **{f"S{i}": make_bars(350, 50 + i) for i in range(50)},
        })
        self.assertEqual(report["status"], "blocked_insufficient_market_data")
        self.assertEqual(report["data_quality"]["status"], "blocked")
        self.assertNotIn("total_return_pct", report["result"])

    def test_bad_configuration_is_rejected(self):
        bars = {"__BENCHMARK__": make_bars(1000), **{f"S{i}": make_bars(350, 50 + i) for i in range(50)}}
        with self.assertRaises(ValueError):
            build_simulation_report(bars, strategy="made_up_strategy")
        with self.assertRaises(ValueError):
            build_simulation_report(bars, initial_cash=0)
        with self.assertRaises(ValueError):
            build_simulation_report(bars, evaluation_sessions=123)

    def test_valid_gate_runs_selected_rule_strategy_and_scales_capital(self):
        bars = {"__BENCHMARK__": make_bars(1000), **{f"S{i}": make_bars(1000, 50 + i) for i in range(50)}}
        fake = {
            "status": "ok", "strategy": "trend_momentum_126", "start_equity": 10000,
            "end_equity": 11000, "total_return_pct": 10.0, "benchmark_total_return_pct": 5.0,
            "excess_return_vs_nifty_pct": 5.0, "max_drawdown_pct": 4.0, "trade_count": 7,
        }
        with patch("paper_trader.simulation.backtest_strategy", return_value=fake) as backtest:
            report = build_simulation_report(
                bars, strategy="trend_momentum_126", initial_cash=25000, evaluation_sessions=252
            )
        backtest.assert_called_once()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["result"]["initial_cash"], 25000)
        self.assertEqual(report["result"]["end_equity"], 27500)
        self.assertEqual(report["result"]["total_return_pct"], 10.0)

    def test_lightgbm_choice_routes_to_chronological_model_backtest(self):
        bars = {"__BENCHMARK__": make_bars(1000), **{f"S{i}": make_bars(350, 50 + i) for i in range(50)}}
        fake = {
            "status": "ok", "strategy": MODEL_NAME, "start_equity": 10000,
            "end_equity": 9000, "total_return_pct": -10.0, "trade_count": 3,
            "training_sample_count_available": 3000, "model_refresh_count": 4,
        }
        with patch("paper_trader.simulation.backtest_lightgbm_research", return_value=fake) as model:
            report = build_simulation_report(
                bars, strategy=MODEL_NAME, evaluation_sessions=252, cost_multiplier=2.0
            )
        model.assert_called_once()
        self.assertEqual(report["result"]["status"], "ok")
        self.assertEqual(report["result"]["end_equity"], 9000)
        self.assertEqual(report["configuration"]["cost_multiplier"], 2.0)
        self.assertTrue(report["safety"]["isolated_from_live_paper_portfolio"])
        self.assertFalse(report["safety"]["broker_orders_enabled"])
        self.assertFalse(report["safety"]["active_strategy_changed"])


if __name__ == "__main__":
    unittest.main()
