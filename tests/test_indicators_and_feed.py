"""Offline regressions for daily indicator confirmation and cached intraday preview."""
import math
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from paper_trader.config import ACTIVE_STRATEGY, STRATEGIES
from paper_trader.core import score_symbol, select_targets
from paper_trader.indicators import confirmation_reason, indicator_snapshot
from paper_trader.intraday_feed import QuoteCache
from paper_trader.paper_chart import _symbol_reason


def rising_ohlc(count=400):
    start = date(2024, 1, 1)
    rows = []
    for index in range(count):
        close = 120 + index * 0.25 + 4.0 * math.sin(index / 4.0)
        rows.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close - 0.2,
            "high": close + 3.0,
            "low": close - 3.0,
            "close": close,
        })
    return rows


class IndicatorsTests(unittest.TestCase):
    def test_indicators_are_finite_and_expose_expected_fields(self):
        indicators = indicator_snapshot(rising_ohlc())
        self.assertIsNotNone(indicators)
        for field in ("ema20", "ema50", "rsi14", "macd", "macd_signal", "macd_hist", "atr14", "atr_pct"):
            self.assertTrue(math.isfinite(indicators[field]), field)
        self.assertTrue(0 <= indicators["rsi14"] <= 100)
        self.assertGreater(indicators["atr14"], 0)

    def test_uses_only_supplied_history_and_no_future_rows(self):
        bars = rising_ohlc()
        before = indicator_snapshot(bars[:300])
        altered = [dict(item) for item in bars]
        for item in altered[300:]:
            item["high"] += 500
            item["close"] = item["high"] - 1
        self.assertEqual(before, indicator_snapshot(altered[:300]))

    def test_missing_high_low_invalid_ohlc_and_insufficient_bars_fail_closed(self):
        rows = rising_ohlc()
        self.assertIsNone(indicator_snapshot(rows[:40]))
        no_high = [dict(row) for row in rows]
        del no_high[5]["high"]
        self.assertIsNone(indicator_snapshot(no_high))
        bad = [dict(row) for row in rows]
        bad[-1]["high"] = bad[-1]["low"] - 1
        self.assertIsNone(indicator_snapshot(bad))
        self.assertIsNotNone(confirmation_reason(None))

    def test_only_new_strategy_requires_confirmation(self):
        bars = rising_ohlc()
        without_high = [{key: value for key, value in bar.items() if key not in ("high", "low")} for bar in bars]
        self.assertIsNotNone(score_symbol(without_high, "trend_momentum_126"))
        self.assertIsNone(score_symbol(without_high, "confirmed_regime_momentum_126"))
        self.assertEqual(ACTIVE_STRATEGY, "nifty_regime_momentum_126")
        self.assertIn("confirmed_regime_momentum_126", STRATEGIES)

    def test_new_model_can_generate_positive_scores_but_remains_regime_gated(self):
        bars = rising_ohlc()
        # Some signal dates in a rising but noisy market should satisfy all confirmations.
        qualifying = [
            bars[:end] for end in range(290, len(bars) + 1)
            if score_symbol(bars[:end], "confirmed_regime_momentum_126") is not None
        ]
        self.assertTrue(qualifying)
        rising_market = rising_ohlc()
        falling_market = [
            {**bar, "close": 1200 - i, "high": 1205 - i, "low": 1195 - i}
            for i, bar in enumerate(rising_market)
        ]
        self.assertEqual(select_targets({"AAA": qualifying[-1]},
            "confirmed_regime_momentum_126", benchmark_bars=falling_market), [])
        self.assertEqual(select_targets({"AAA": qualifying[-1]},
            "confirmed_regime_momentum_126", benchmark_bars=rising_market), ["AAA"])

    def test_preview_contains_matching_indicator_reason(self):
        rows = rising_ohlc()
        result = _symbol_reason(rows, "confirmed_regime_momentum_126",
                                score_symbol(rows, "confirmed_regime_momentum_126"))
        self.assertIn("indicators", result)
        self.assertTrue(result["reason"])
        self.assertIsNotNone(result["indicators"])


class IntradayCacheTests(unittest.TestCase):
    def test_second_by_second_reads_share_one_upstream_fetch(self):
        calls = []
        def provider(symbol):
            calls.append(symbol)
            return [{"date": "2026-10-19T09:16+05:30", "close": 101.0}]
        cache = QuoteCache(fetch_fn=provider, ttl=60)
        with patch("paper_trader.intraday_feed.time.monotonic", side_effect=[0, 0, 0, 1, 1, 59, 59, 61, 61, 61]):
            a = cache.get("RELIANCE")
            b = cache.get("RELIANCE")
            c = cache.get("RELIANCE")
            d = cache.get("RELIANCE")
        self.assertEqual(len(calls), 2)
        self.assertEqual([a["status"], b["status"], c["status"], d["status"]], ["ok"] * 4)
        self.assertEqual(a["interval"], "1m")

    def test_unknown_symbol_is_rejected_without_provider_call(self):
        calls = []
        cache = QuoteCache(fetch_fn=lambda symbol: calls.append(symbol), ttl=60)
        with self.assertRaises(ValueError):
            cache.get("NOT_ON_NIFTY")
        self.assertEqual(calls, [])

    def test_failed_source_is_cached_and_no_prices_are_invented(self):
        calls = []
        def provider(symbol):
            calls.append(symbol)
            raise RuntimeError("upstream blocked")
        cache = QuoteCache(fetch_fn=provider, ttl=60)
        self.assertEqual(cache.get("RELIANCE")["bars"], [])
        self.assertEqual(cache.get("RELIANCE")["status"], "unavailable")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
