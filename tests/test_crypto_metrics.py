"""Offline money-math regressions for saved virtual BTC/ETH trade ledgers."""
import math
import unittest

from paper_trader.crypto_metrics import crypto_performance


def market(price=110.0, symbol="BTC-USD"):
    return {symbol: [{"time": "2026-10-19T14:00:00+00:00", "close": price}]}


def fill(side, qty, price, fee=0, symbol="BTC-USD", stamp="2026-10-19T13:00:00+00:00"):
    return {
        "time": stamp, "side": side, "symbol": symbol,
        "quantity": str(qty), "price": str(price), "fees": str(fee),
        "reason": "Regression test fill",
    }


def virtual_state(cash, qty=0.0, interval="1m"):
    return {
        "initial_cash": 1000.0,
        "cash": cash,
        "positions": {"BTC-USD": qty} if qty else {},
        "model": "ema_rsi",
        "bar_interval": interval,
        "last_bar": "2026-10-19T14:00:00+00:00",
    }


class CryptoPerformanceTests(unittest.TestCase):
    def test_initial_1000_usd_account_has_no_phantom_profit(self):
        p = crypto_performance(virtual_state(1000), market(), [])
        self.assertEqual(p["initial_capital_usd"], 1000)
        self.assertEqual(p["available_cash_usd"], 1000)
        self.assertEqual(p["holdings_market_value_usd"], 0)
        self.assertEqual(p["net_pnl_usd"], 0)
        self.assertEqual(p["net_return_pct"], 0)
        self.assertEqual(p["realized_pnl_usd"], 0)
        self.assertEqual(p["unrealized_pnl_usd"], 0)
        self.assertTrue(p["fifo_reconciled"])

    def test_first_btc_buy_pnl_counts_buy_fee_slippage_in_fill_price(self):
        qty = 0.003035
        price = 82207.9759
        fee = .499
        cash = 750.0
        rows = [fill("BUY", qty, price, fee)]
        p = crypto_performance(virtual_state(cash, qty), market(82000), rows)
        self.assertAlmostEqual(p["holdings_market_value_usd"], qty * 82000)
        self.assertAlmostEqual(p["portfolio_equity_usd"], cash + qty * 82000)
        self.assertAlmostEqual(p["net_pnl_usd"], cash + qty * 82000 - 1000)
        self.assertEqual(p["buy_count"], 1)
        self.assertEqual(p["sell_count"], 0)
        self.assertEqual(p["last_recorded_trade"]["side"], "BUY")
        self.assertAlmostEqual(p["lifetime_buy_spend_usd"], qty * price + fee)
        self.assertTrue(p["fifo_reconciled"])
        self.assertEqual(p["realized_pnl_usd"], 0)
        self.assertAlmostEqual(p["unrealized_pnl_usd"], p["net_pnl_usd"], places=1)

    def test_fifo_partial_sell_realized_gain_plus_unrealized_reconciles(self):
        # Buy 1 @100 + $1 fee, buy 1 @110 + $1, sell 1.5 @120 - $1.5.
        rows = [
            fill("BUY", 1, 100, 1, stamp="2026-10-19T13:00:00+00:00"),
            fill("BUY", 1, 110, 1, stamp="2026-10-19T13:01:00+00:00"),
            fill("SELL", 1.5, 120, 1.5, stamp="2026-10-19T13:02:00+00:00"),
        ]
        cash = 1000 - 101 - 111 + (180 - 1.5)
        p = crypto_performance(virtual_state(cash, .5), market(125), rows)
        self.assertTrue(p["fifo_reconciled"])
        self.assertAlmostEqual(p["realized_pnl_usd"], 22.0)
        self.assertAlmostEqual(p["unrealized_pnl_usd"], 7.0)
        self.assertAlmostEqual(p["open_cost_basis_usd"], 55.5)
        self.assertAlmostEqual(p["net_pnl_usd"], 29.0)
        self.assertEqual(p["last_recorded_trade"]["side"], "SELL")
        self.assertAlmostEqual(p["last_recorded_trade"]["realized_pnl_usd"], 22.0)
        self.assertEqual(p["winning_sells"], 1)
        self.assertEqual(p["losing_sells"], 0)
        self.assertEqual(p["recorded_trade_count"], 3)
        self.assertEqual(len(p["recent_fills"]), 3)
        self.assertAlmostEqual(p["lifetime_sell_proceeds_usd"], 178.5)
        self.assertAlmostEqual(p["total_recorded_fees_usd"], 3.5)

    def test_unprofitable_sell_is_negative_after_both_fees(self):
        rows = [fill("BUY", 1, 100, 1), fill("SELL", 1, 90, 1,
                                           stamp="2026-10-19T13:01:00+00:00")]
        p = crypto_performance(virtual_state(988), {}, rows)
        self.assertAlmostEqual(p["net_pnl_usd"], -12)
        self.assertAlmostEqual(p["realized_pnl_usd"], -12)
        self.assertEqual(p["unrealized_pnl_usd"], 0)
        self.assertEqual(p["winning_sells"], 0)
        self.assertEqual(p["losing_sells"], 1)

    def test_missing_buy_history_with_open_position_hides_unverified_fifo_profit(self):
        p = crypto_performance(virtual_state(900, 1), market(105), [])
        self.assertAlmostEqual(p["net_pnl_usd"], 5)
        self.assertFalse(p["fifo_reconciled"])
        self.assertIsNone(p["realized_pnl_usd"])
        self.assertIsNone(p["unrealized_pnl_usd"])
        self.assertIsNone(p["open_cost_basis_usd"])

    def test_unaccounted_cash_flow_does_not_get_fabricated_as_realized_pnl(self):
        p = crypto_performance(virtual_state(950), {}, [])
        self.assertAlmostEqual(p["net_pnl_usd"], -50)
        self.assertFalse(p["fifo_reconciled"])
        self.assertIsNone(p["realized_pnl_usd"])
        self.assertIsNone(p["unrealized_pnl_usd"])

    def test_missing_initial_buy_for_sold_coins_hides_unsupported_trade_profit(self):
        p = crypto_performance(
            virtual_state(1000),
            {},
            [fill("SELL", 1, 100, 0)],
        )
        self.assertFalse(p["fifo_reconciled"])
        self.assertIsNone(p["last_recorded_trade"]["realized_pnl_usd"])

    def test_invalid_prices_or_cash_fail_closed(self):
        with self.assertRaises(ValueError):
            crypto_performance(virtual_state(float("nan")), market(), [])
        with self.assertRaises(ValueError):
            crypto_performance(virtual_state(900, 1), {}, [])
        with self.assertRaises(ValueError):
            crypto_performance(virtual_state(900, 1), market(float("nan")), [])

    def test_hold_latest_decision_has_no_fake_transaction(self):
        state = virtual_state(1000)
        state["last_fills"] = []
        p = crypto_performance(state, market(), [])
        self.assertEqual(p["last_processed_action"], "HOLD")
        self.assertIsNone(p["last_recorded_trade"])


if __name__ == "__main__":
    unittest.main()
