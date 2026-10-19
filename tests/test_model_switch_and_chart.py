"""Regression tests for paper model changes, state safety and read-only chart previews."""
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from paper_trader import main as trader_main
from paper_trader import model_select, paper_chart
from paper_trader.config import ACTIVE_STRATEGY, MIN_CURRENT_SYMBOL_COVERAGE, STRATEGIES
from paper_trader.core import new_state
from paper_trader.market_data import BENCHMARK_KEY
from paper_trader.symbols import NIFTY50_SYMBOLS


def sample_bars(days=330):
    start = date(2025, 1, 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    def rows(seed):
        return [{
            "date": d, "open": seed + i * 0.4,
            "high": seed + i * 0.4 + 2, "low": seed + i * 0.4 - 2,
            "close": seed + i * 0.4 + (i % 7) * 0.03,
        } for i, d in enumerate(dates)]
    return {
        BENCHMARK_KEY: rows(1000),
        **{name: rows(110 + i * 12) for i, name in enumerate(NIFTY50_SYMBOLS)},
    }


class ModelSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        data = Path(self.temp.name)
        self.state_path = data / "state.json"
        self.selection_path = data / "selected_strategy.json"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(trader_main, "DATA_DIR", data))
        self.stack.enter_context(patch.object(trader_main, "LOCK_PATH", data / ".paper_session.lock"))
        self.stack.enter_context(patch.object(trader_main, "STATE_PATH", self.state_path))
        self.stack.enter_context(patch.object(model_select, "STATE_PATH", self.state_path))
        self.stack.enter_context(patch.object(model_select, "SELECTION_PATH", self.selection_path))
        self.state = new_state()
        self.state["last_signal_date"] = "2026-10-19"
        self.state["pending_target"] = {
            "signal_date": "2026-10-19", "target_symbols": ["AAA", "BBB"]
        }
        self.state["cash"] = 7200.0
        self.state["positions"] = {"CCC": 4}
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def read(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def test_pending_blocked_without_flag_and_no_state_change(self):
        original = self.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "pending"):
            model_select.switch_strategy("medium_momentum_63")
        self.assertEqual(original, self.state_path.read_bytes())

    def test_explicit_cancellation_keeps_cash_holdings_risk_and_processed_date(self):
        result = model_select.switch_strategy("medium_momentum_63", cancel_pending=True)
        after = self.read()
        self.assertTrue(result["changed"])
        self.assertEqual(after["selected_strategy"], "medium_momentum_63")
        self.assertEqual(model_select.current_strategy(), "medium_momentum_63")
        self.assertIsNone(after["pending_target"])
        for key in ("cash", "positions", "risk_locked", "last_signal_date", "peak_equity"):
            self.assertEqual(after[key], self.state[key])
        self.assertEqual(after["strategy_switch_audit"][-1]["cancelled_pending"], self.state["pending_target"])
        self.assertEqual(after["strategy_switch_audit"][-1]["from_strategy"], ACTIVE_STRATEGY)
        self.assertEqual(after["strategy_switch_audit"][-1]["to_strategy"], "medium_momentum_63")

    def test_same_model_never_cancels_even_with_cancel_flag(self):
        result = model_select.switch_strategy(ACTIVE_STRATEGY, cancel_pending=True)
        self.assertFalse(result["changed"])
        self.assertEqual(self.read()["pending_target"], self.state["pending_target"])
        self.assertNotIn("strategy_switch_audit", self.read())

    def test_risk_lock_prevents_mutations(self):
        data = self.read()
        data["risk_locked"] = True
        self.state_path.write_text(json.dumps(data), encoding="utf-8")
        original = self.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Risk lock"):
            model_select.switch_strategy("medium_momentum_63", cancel_pending=True)
        self.assertEqual(self.state_path.read_bytes(), original)

    def test_two_sequential_changes_add_audit_entries(self):
        model_select.switch_strategy("medium_momentum_63", cancel_pending=True)
        model_select.switch_strategy("dual_momentum_6_12")
        after = self.read()
        self.assertEqual(after["selected_strategy"], "dual_momentum_6_12")
        self.assertEqual(len(after["strategy_switch_audit"]), 2)
        self.assertIsNone(after["strategy_switch_audit"][1]["cancelled_pending"])

    def test_corrupted_state_fails_closed(self):
        self.state_path.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            model_select.switch_strategy("medium_momentum_63", cancel_pending=True)
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), "{not-json")

    def test_selected_strategy_must_be_supported(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            model_select.switch_strategy("lightgbm_relative_alpha", cancel_pending=True)
        self.assertNotIn("selected_strategy", self.read())

    def test_current_strategy_prefers_portfolio_over_legacy_file(self):
        self.selection_path.write_text(json.dumps({"strategy": "trend_momentum_126"}))
        model_select.switch_strategy("medium_momentum_63", cancel_pending=True)
        self.assertEqual(model_select.current_strategy(), "medium_momentum_63")
        self.assertEqual(json.loads(self.selection_path.read_text())["strategy"], "trend_momentum_126")


class ChartPreviewTests(unittest.TestCase):
    def test_six_strategy_previews_explain_scores_without_creating_trades(self):
        bars = sample_bars()
        state = new_state()
        original = copy.deepcopy(state)
        snapshot = paper_chart.build_snapshot(
            bars, state, [], active_strategy=ACTIVE_STRATEGY,
        )
        self.assertEqual(state, original)
        self.assertEqual(len(snapshot["model_signals"]), len(STRATEGIES))
        self.assertEqual(snapshot["trades"], [])
        self.assertEqual(snapshot["market_date"], bars[BENCHMARK_KEY][-1]["date"])
        for name, preview in snapshot["model_signals"].items():
            self.assertEqual(preview["status"], "ok", name)
            self.assertEqual(preview["coverage"], len(NIFTY50_SYMBOLS))
            for detail in preview["details"]:
                self.assertIn(detail["symbol"], preview["targets"])
                self.assertIsInstance(detail["score"], float)
                self.assertIn("trend_sma", detail)
                self.assertTrue(detail["reason"])
                self.assertIn(detail["symbol"], snapshot["candles"])

    def test_stale_coverage_withholds_signals(self):
        bars = sample_bars()
        for symbol in NIFTY50_SYMBOLS[:len(NIFTY50_SYMBOLS) - MIN_CURRENT_SYMBOL_COVERAGE + 1]:
            bars[symbol] = bars[symbol][:-1]
        snapshot = paper_chart.build_snapshot(bars, new_state(), [], active_strategy=ACTIVE_STRATEGY)
        for result in snapshot["model_signals"].values():
            self.assertEqual(result["status"], "withheld_insufficient_current_coverage")
            self.assertEqual(result["targets"], [])

    def test_research_comparison_only_reuses_quality_passed_report(self):
        report = {
            "data_quality": {"status": "ok"},
            "generated_at_utc": "2026-10-18T00:00:00+00:00",
            "results": [{
                "strategy": "trend_momentum_126",
                "status": "ok", "sample_end": "2026-10-16",
                "sample_start": "2023-10-16", "sessions": 756,
                "total_return_pct": -1.0, "max_drawdown_pct": 11.0, "trade_count": 8,
                "robustness": {"period_sensitivity": {
                    "252": {"status": "ok", "total_return_pct": 3.0,
                            "max_drawdown_pct": 4.0, "trade_count": 2}
                }},
            }],
        }
        snapshot = paper_chart.build_snapshot({}, new_state(), [], research=report, active_strategy=ACTIVE_STRATEGY)
        summary = snapshot["research_comparison"]
        self.assertEqual(summary["results"][0]["year_1"]["return_pct"], 3.0)
        self.assertEqual(summary["results"][0]["years_3"]["return_pct"], -1.0)
        self.assertEqual(snapshot["trades"], [])
        withheld = paper_chart.build_snapshot({}, new_state(), [],
            research={"data_quality": {"status": "blocked_insufficient_market_data"}},
            active_strategy=ACTIVE_STRATEGY)
        self.assertEqual(withheld["research_comparison"]["status"], "withheld")


if __name__ == "__main__":
    unittest.main()
