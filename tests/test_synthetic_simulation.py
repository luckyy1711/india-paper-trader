import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_trader.synthetic_simulation import generate_synthetic_bars, run_monte_carlo


class SyntheticSimulationTests(unittest.TestCase):
    def test_generator_is_seeded_positive_and_aligned(self):
        first = generate_synthetic_bars(seed=7, symbols=3, warmup_sessions=220, evaluation_sessions=20)
        second = generate_synthetic_bars(seed=7, symbols=3, warmup_sessions=220, evaluation_sessions=20)
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"__BENCHMARK__", "SYN01", "SYN02", "SYN03"})
        dates = [row["date"] for row in first["__BENCHMARK__"]]
        self.assertEqual(len(dates), 240)
        self.assertEqual(len(set(dates)), 240)
        for rows in first.values():
            self.assertEqual([row["date"] for row in rows], dates)
            self.assertTrue(all(row["close"] > 0 and row["high"] >= row["low"] for row in rows))

    def test_generator_rejects_too_few_symbols(self):
        with self.assertRaises(ValueError):
            generate_synthetic_bars(seed=1, symbols=2)

    def test_generated_year_can_run_through_real_rule_backtest(self):
        bars = generate_synthetic_bars(
            seed=21, symbols=3, warmup_sessions=300, evaluation_sessions=20
        )
        from paper_trader.research import backtest_strategy
        result = backtest_strategy(bars, "trend_momentum_126", sample_days=20)
        self.assertEqual(result.get("status"), "ok")
        self.assertIn("total_return_pct", result)
        self.assertIn("benchmark_total_return_pct", result)

    def test_monte_carlo_checkpoints_and_isolates_portfolio(self):
        fake_result = {
            "status": "ok", "total_return_pct": 3.0, "benchmark_total_return_pct": 1.0,
            "max_drawdown_pct": 2.0, "sharpe_approx": 0.5, "trade_count": 4,
        }
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "synthetic.json"
            with patch("paper_trader.synthetic_simulation.backtest_strategy", return_value=fake_result):
                report = run_monte_carlo(
                    hours=0.01, max_paths=2, checkpoint_every=1, symbols=3,
                    warmup_sessions=220, evaluation_sessions=20,
                    report_path=report_path, seed=9,
                )
            self.assertEqual(report["progress"]["completed_paths"], 2)
            self.assertEqual(report["results"]["strategy_return_pct"]["median"], 3.0)
            self.assertTrue(report["safety"]["isolated_from_live_paper_portfolio"])
            self.assertFalse(report["safety"]["broker_orders_enabled"])
            self.assertTrue(report_path.exists())

    def test_bad_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_monte_carlo(hours=13, max_paths=1, report_path=Path(tmp) / "out.json")


if __name__ == "__main__":
    unittest.main()
