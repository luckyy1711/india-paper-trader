"""Offline safety tests; no network and no live orders."""
import unittest
from paper_trader.crypto_paper import fresh_state, rebalance, signal, nav


class CryptoPaperTests(unittest.TestCase):
    def test_initial_balance_is_separate(self):
        s = fresh_state()
        self.assertEqual(s["cash"], 1000.0)
        self.assertEqual(s["positions"], {})
        self.assertEqual(s["model"], "ema_rsi")

    def test_no_trade_on_insufficient_history(self):
        self.assertEqual(signal([{"close": 100}] * 10, "ema_rsi")[0], False)

    def test_position_cap(self):
        s = fresh_state()
        trades = rebalance(s, ["BTC-USD", "ETH-USD"], {"BTC-USD": 100, "ETH-USD": 50}, "2026-10-19T10:00:00+00:00")
        self.assertEqual(len(trades), 2)
        self.assertLessEqual(sum(float(q) * p for (symbol, q), p in zip(s["positions"].items(), [100, 50])), 502)
        self.assertGreater(s["cash"], 490)

    def test_risk_lock_sells_and_prevents_buys(self):
        s = fresh_state()
        rebalance(s, ["BTC-USD"], {"BTC-USD": 100}, "2026-10-19T10:00:00+00:00")
        s["risk_locked"] = True
        trades = rebalance(s, ["BTC-USD"], {"BTC-USD": 95}, "2026-10-19T11:00:00+00:00")
        self.assertEqual([t["side"] for t in trades], ["SELL"])
        self.assertEqual(s["positions"], {})


if __name__ == "__main__":
    unittest.main()
