import sys
import types
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from paper_trader.market_data import BENCHMARK_KEY, download_market_data, _extract_ticker_frame
from paper_trader.symbols import BENCHMARK_TICKER, NIFTY50_SYMBOLS, YAHOO_TICKERS


class FakeYahooFinance:
    """Return a yfinance-like MultiIndex frame and record requested tickers."""

    def __init__(self):
        self.calls = []

    def download(self, tickers, **kwargs):
        requested = [tickers] if isinstance(tickers, str) else list(tickers)
        self.calls.append(requested)
        columns = pd.MultiIndex.from_tuples(
            [(ticker, field) for ticker in requested for field in ("Open", "High", "Low", "Close", "Volume")]
        )
        index = pd.to_datetime([date(2025, 1, day).isoformat() for day in (2, 3, 6)])
        values = []
        for day in range(len(index)):
            row = []
            for ticker_index, _ticker in enumerate(requested):
                price = 100.0 + ticker_index + day
                row.extend([price, price + 1.0, price - 1.0, price + 0.5, 1000.0 + day])
            values.append(row)
        return pd.DataFrame(values, index=index, columns=columns)


class MarketDataTests(unittest.TestCase):
    def test_ambiguous_single_index_batch_response_is_skipped_safely(self):
        frame = pd.DataFrame({"Open": [100.0], "Close": [101.0]})
        self.assertIsNone(_extract_ticker_frame(frame, "RELIANCE.NS", pd))

    def test_yahoo_tickers_are_used_and_mapped_back_to_internal_symbols(self):
        provider = FakeYahooFinance()
        fake_module = types.SimpleNamespace(download=provider.download)
        with patch.dict(sys.modules, {"yfinance": fake_module}), patch("paper_trader.market_data.time.sleep"):
            bars = download_market_data()

        requested = {ticker for batch in provider.calls for ticker in batch}
        expected_yahoo_tickers = set(YAHOO_TICKERS.values()) | {BENCHMARK_TICKER}
        self.assertEqual(requested, expected_yahoo_tickers)
        self.assertTrue(all(ticker.endswith(".NS") or ticker == BENCHMARK_TICKER for ticker in requested))
        self.assertEqual(set(bars), set(NIFTY50_SYMBOLS) | {BENCHMARK_KEY})
        self.assertNotIn("RELIANCE.NS", bars)
        self.assertEqual(len(bars["RELIANCE"]), 3)
        self.assertEqual(len(bars[BENCHMARK_KEY]), 3)
        self.assertIn("high", bars["RELIANCE"][0])
        self.assertIn("low", bars["RELIANCE"][0])
        self.assertIn("volume", bars["RELIANCE"][0])


if __name__ == "__main__":
    unittest.main()
