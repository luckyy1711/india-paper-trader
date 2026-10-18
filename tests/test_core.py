import unittest
from datetime import date, timedelta

from paper_trader.core import (
    execute_rebalance,
    mark_to_market,
    new_state,
    score_symbol,
    select_targets,
)
from paper_trader.config import ACTIVE_STRATEGY, MAX_POSITION_WEIGHT, MAX_TOTAL_EXPOSURE, STRATEGIES
from paper_trader.symbols import NIFTY50_SYMBOLS


def make_bars(count, price_fn):
    start = date(2024, 1, 1)
    result = []
    for index in range(count):
        day = start + timedelta(days=index)
        price = float(price_fn(index))
        result.append({"date": day.isoformat(), "open": price, "close": price})
    return result


class CoreTests(unittest.TestCase):
    def test_universe_is_50_unique_symbols(self):
        self.assertEqual(len(NIFTY50_SYMBOLS), 50)
        self.assertEqual(len(set(NIFTY50_SYMBOLS)), 50)

    def test_trending_symbol_scores_and_flat_symbol_does_not(self):
        trend = make_bars(320, lambda i: 100 + i * 0.35 + (i % 5) * 0.1)
        flat = make_bars(320, lambda i: 100)
        falling = make_bars(320, lambda i: 500 - i * 0.4)
        self.assertIsNotNone(score_symbol(trend, "trend_momentum_126"))
        self.assertIsNone(score_symbol(flat, "trend_momentum_126"))
        self.assertIsNone(score_symbol(falling, "trend_momentum_126"))


    def test_new_strategy_candidates_are_configured_but_not_auto_promoted(self):
        self.assertIn("relative_strength_12_1", STRATEGIES)
        self.assertIn("dual_momentum_6_12", STRATEGIES)
        self.assertIn("nifty_regime_momentum_126", STRATEGIES)
        self.assertEqual(ACTIVE_STRATEGY, "trend_momentum_126")

    def test_12_1_and_dual_momentum_need_long_enough_positive_history(self):
        rising = make_bars(400, lambda i: 100 + i * 0.2 + (i % 7) * 0.05)
        too_short = rising[:260]
        self.assertIsNotNone(score_symbol(rising, "relative_strength_12_1"))
        self.assertIsNotNone(score_symbol(rising, "dual_momentum_6_12"))
        self.assertIsNone(score_symbol(too_short, "relative_strength_12_1"))
        self.assertIsNone(score_symbol(too_short, "dual_momentum_6_12"))

    def test_benchmark_regime_filter_blocks_long_entries_below_200_sma(self):
        stock = make_bars(320, lambda i: 100 + i * 0.5)
        falling_market = make_bars(320, lambda i: 1000 - i * 1.5)
        rising_market = make_bars(320, lambda i: 500 + i * 2)
        self.assertEqual(
            select_targets({"AAA": stock}, "nifty_regime_momentum_126", benchmark_bars=falling_market),
            [],
        )
        self.assertEqual(
            select_targets({"AAA": stock}, "nifty_regime_momentum_126", benchmark_bars=rising_market),
            ["AAA"],
        )
        self.assertEqual(
            select_targets({"AAA": stock}, "nifty_regime_momentum_126"),
            [],
        )

    def test_target_selection_is_deterministic(self):
        bars = {
            "AAA": make_bars(320, lambda i: 100 + i * 0.35 + (i % 5) * 0.1),
            "BBB": make_bars(320, lambda i: 100),
            "CCC": make_bars(320, lambda i: 500 - i * 0.4),
        }
        self.assertEqual(select_targets(bars, "trend_momentum_126"), ["AAA"])

    def test_rebalance_respects_position_and_total_exposure_limits(self):
        state = new_state()
        execute_rebalance(
            state,
            ["AAA", "BBB", "CCC"],
            {"AAA": 100.0, "BBB": 50.0, "CCC": 200.0},
            "2026-01-02",
            "2026-01-01",
        )
        exposure = sum(quantity * {"AAA": 100.0, "BBB": 50.0, "CCC": 200.0}[symbol] for symbol, quantity in state["positions"].items())
        equity = float(state["cash"]) + exposure
        self.assertGreaterEqual(state["cash"], 0)
        self.assertLessEqual(exposure, equity * MAX_TOTAL_EXPOSURE + 1)
        for symbol, quantity in state["positions"].items():
            self.assertLessEqual(quantity * {"AAA": 100.0, "BBB": 50.0, "CCC": 200.0}[symbol], equity * MAX_POSITION_WEIGHT + 1)

    def test_cost_stress_increases_costs_and_reduces_available_cash(self):
        baseline = new_state()
        stressed = new_state()
        baseline_trades = execute_rebalance(
            baseline, ["AAA"], {"AAA": 100.0}, "2026-01-02", "2026-01-01", cost_multiplier=1.0
        )
        stressed_trades = execute_rebalance(
            stressed, ["AAA"], {"AAA": 100.0}, "2026-01-02", "2026-01-01", cost_multiplier=2.0
        )
        self.assertEqual(len(baseline_trades), 1)
        self.assertEqual(len(stressed_trades), 1)
        self.assertGreater(stressed_trades[0]["costs"], baseline_trades[0]["costs"])
        self.assertLess(stressed["cash"], baseline["cash"])
        with self.assertRaises(ValueError):
            execute_rebalance(new_state(), [], {}, "2026-01-02", "2026-01-01", cost_multiplier=0.5)

    def test_target_exit_sells_and_returns_to_cash(self):
        state = new_state()
        state["cash"] = 9500.0
        state["positions"] = {"AAA": 5}
        trades = execute_rebalance(state, [], {"AAA": 100.0}, "2026-01-02", "2026-01-01")
        self.assertEqual(state["positions"], {})
        self.assertEqual([trade["side"] for trade in trades], ["SELL"])
        self.assertGreater(state["cash"], 9500.0)

    def test_drawdown_guard_locks_trading(self):
        state = new_state()
        state["cash"] = 0.0
        state["positions"] = {"AAA": 100}
        metrics = mark_to_market(state, {"AAA": 89.0})
        self.assertTrue(state["risk_locked"])
        self.assertEqual(metrics["equity"], 8900.0)
        self.assertGreaterEqual(metrics["drawdown_pct"], 10.0)

    def test_missing_position_price_fails_closed(self):
        state = new_state()
        state["positions"] = {"AAA": 1}
        with self.assertRaises(ValueError):
            mark_to_market(state, {})


if __name__ == "__main__":
    unittest.main()
