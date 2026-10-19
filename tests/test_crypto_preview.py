"""Offline regression tests: crypto minute preview must never place or invent trades."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from paper_trader import crypto_preview


def binance_row(stamp=1_799_999_980, close="102.0", high="103.0", low="99.0"):
    return [stamp * 1000, "100.0", high, low, close, "50", (stamp + 60) * 1000 - 1]


def coinbase_row(stamp=1_799_999_980):
    return [stamp, "99.0", "103.0", "100.0", "102.0", "50"]


class CryptoPreviewTests(unittest.TestCase):
    def test_binance_parser_preserves_actual_prices_and_forming_state(self):
        stamp = 1_799_999_980
        now = datetime.fromtimestamp(stamp + 30, timezone.utc)
        bars = crypto_preview.parse_binance_klines([binance_row(stamp)], now)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["open"], 100.0)
        self.assertEqual(bars[0]["high"], 103.0)
        self.assertEqual(bars[0]["low"], 99.0)
        self.assertEqual(bars[0]["close"], 102.0)
        self.assertFalse(bars[0]["complete"])
        complete = crypto_preview.parse_binance_klines(
            [binance_row(stamp)], datetime.fromtimestamp(stamp + 65, timezone.utc)
        )
        self.assertTrue(complete[0]["complete"])

    def test_coinbase_parser_sorts_bars_and_handles_open_candle(self):
        stamp = 1_799_999_980
        now = datetime.fromtimestamp(stamp + 30, timezone.utc)
        bars = crypto_preview.parse_coinbase_candles(
            [coinbase_row(stamp), coinbase_row(stamp - 60)], now,
        )
        self.assertEqual(len(bars), 2)
        self.assertTrue(bars[0]["complete"])
        self.assertFalse(bars[1]["complete"])
        self.assertEqual(bars[1]["close"], 102.0)

    def test_invalid_candles_are_rejected_not_repaired_or_invented(self):
        now = datetime.fromtimestamp(1_800_000_000, timezone.utc)
        rows = [
            binance_row(high="50"), binance_row(close="NaN"),
            ["not-a-timestamp"], binance_row(close="-100"),
        ]
        self.assertEqual(crypto_preview.parse_binance_klines(rows, now), [])
        self.assertEqual(crypto_preview.parse_coinbase_candles([[1, "bogus"]], now), [])

    def test_unknown_market_does_not_download(self):
        with patch.object(crypto_preview, "_read_json") as fetch:
            with self.assertRaises(ValueError):
                crypto_preview.download_crypto_minute("FAKE-USD")
            fetch.assert_not_called()

    def test_binance_provider_selected_when_recent(self):
        now = int(datetime.now(timezone.utc).timestamp())
        stamp = (now // 60) * 60
        with patch.object(crypto_preview, "_read_json", return_value=[binance_row(stamp)]):
            rows, provider = crypto_preview.download_crypto_minute("BTC-USD")
        self.assertEqual(rows[-1]["close"], 102.0)
        self.assertIn("Binance", provider)

    def test_coinbase_fallback_when_binance_is_unavailable(self):
        now = int(datetime.now(timezone.utc).timestamp())
        stamp = (now // 60) * 60
        def provider(url):
            if "binance" in url:
                raise OSError("upstream blocked")
            return [coinbase_row(stamp)]
        with patch.object(crypto_preview, "_read_json", side_effect=provider) as fetch:
            rows, label = crypto_preview.download_crypto_minute("ETH-USD")
        self.assertIn("Coinbase", label)
        self.assertEqual(rows[-1]["open"], 100.0)
        self.assertEqual(fetch.call_count, 3)

    def test_one_download_per_ten_seconds_across_browser_polls(self):
        calls = []
        def source(symbol):
            calls.append(symbol)
            return ([{"time": "2026-10-19T13:00:00+00:00", "open": 1, "high": 2,
                      "low": 1, "close": 2, "complete": False}], "test feed")
        cache = crypto_preview.CryptoPreviewCache(fetch_fn=source, ttl_seconds=10)
        with patch("paper_trader.crypto_preview.time.monotonic", return_value=0) as clock:
            first = cache.get("BTC-USD")
            clock.return_value=1
            second = cache.get("BTC-USD")
            clock.return_value=9
            third = cache.get("BTC-USD")
            clock.return_value=11
            fourth = cache.get("BTC-USD")
        self.assertEqual(len(calls), 2)
        self.assertEqual(first["status"], "ok")
        self.assertEqual([second["status"], third["status"], fourth["status"]], ["ok"] * 3)
        self.assertEqual(third["cache_age_seconds"], 9)

    def test_stale_label_preserves_real_candles_during_outage(self):
        calls = []
        def source(symbol):
            calls.append(symbol)
            if len(calls) > 1:
                raise OSError("exchange outage")
            return ([{"time": "2026-10-19T13:00:00+00:00", "close": 102.0}], "test feed")
        cache = crypto_preview.CryptoPreviewCache(fetch_fn=source, ttl_seconds=10)
        with patch("paper_trader.crypto_preview.time.monotonic", return_value=0) as clock:
            fresh = cache.get("BTC-USD")
            clock.return_value=11
            stale = cache.get("BTC-USD")
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["bars"], fresh["bars"])
        self.assertEqual(stale["fetched_at_utc"], fresh["fetched_at_utc"])
        self.assertIn("failed", stale["message"])

    def test_initial_provider_error_does_not_return_fake_prices(self):
        cache = crypto_preview.CryptoPreviewCache(fetch_fn=lambda symbol: 1/0)
        response = cache.get("BTC-USD")
        self.assertEqual(response["status"], "unavailable")
        self.assertEqual(response["bars"], [])


if __name__ == "__main__":
    unittest.main()
