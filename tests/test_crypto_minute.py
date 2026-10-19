"""Offline checks for 1-minute crypto paper engine; no external exchange calls."""
import csv
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from paper_trader import crypto_minute as bot
from paper_trader import crypto_paper as account


class MinutePaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(account, "STATE", self.root / "crypto_state.json"))
        self.patches.enter_context(patch.object(account, "DATA", self.root))
        self.patches.enter_context(patch.object(account, "TRADES", self.root / "crypto_trades.csv"))
        self.patches.enter_context(patch.object(account, "CHART", self.root / "crypto_chart.json"))
        self.now = datetime(2026, 10, 19, 14, 2, 15, tzinfo=timezone.utc)

    def bars(self, end=None, count=80, symbol="BTC-USD"):
        end = end or (self.now - timedelta(seconds=75))
        result = []
        for i in range(count):
            start = end - timedelta(minutes=count - i - 1)
            close = 100 + i * .1 + (0 if symbol == "BTC-USD" else 10)
            result.append({
                "time": start.isoformat(timespec="seconds"),
                "open": close - .05, "high": close + .3,
                "low": close - .3, "close": close, "complete": True,
            })
        return result

    def both_bars(self, end=None):
        return {symbol: self.bars(end=end, symbol=symbol) for symbol in bot.SYMBOLS}

    def test_migration_preserves_virtual_holdings_cash_and_fills(self):
        state = account.fresh_state()
        state["cash"] = 750
        state["positions"] = {"BTC-USD": .003}
        state["entry_prices"] = {"BTC-USD": 82200}
        state["last_bar"] = "2026-10-19T13:00:00+00:00"
        state["pending"] = {"time": state["last_bar"], "targets": ["ETH-USD"]}
        account.save_json(account.STATE, state)
        converted = bot.migrate_portfolio(account.load_state())
        self.assertEqual(converted["bar_interval"], "1m")
        self.assertEqual(converted["cash"], 750)
        self.assertEqual(converted["positions"], {"BTC-USD": .003})
        self.assertEqual(converted["entry_prices"]["BTC-USD"], 82200)
        self.assertIsNone(converted["pending"])
        self.assertIsNone(converted["last_bar"])
        self.assertEqual(converted["minute_transition"]["cancelled_unfilled_hourly_targets"], ["ETH-USD"])
        backups = list(self.root.glob("crypto_state.hourly-backup-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())["pending"]["targets"], ["ETH-USD"])

    def test_repeated_migration_does_not_wipe_new_pending_or_create_second_backup(self):
        state = account.fresh_state()
        account.save_json(account.STATE, state)
        once = bot.migrate_portfolio(state)
        once["pending"] = {"time": "2026-10-19T14:00:00+00:00", "targets": ["BTC-USD"]}
        again = bot.migrate_portfolio(once)
        self.assertEqual(again["pending"]["targets"], ["BTC-USD"])
        self.assertEqual(len(list(self.root.glob("*hourly-backup-*.json"))), 1)

    def test_migration_refuses_reinterpretation_of_legacy_24h_strategy(self):
        state = account.fresh_state()
        state["model"] = "momentum_24h"
        with self.assertRaisesRegex(RuntimeError, "EMA/RSI"):
            bot.migrate_portfolio(state)
        self.assertFalse(account.STATE.exists())

    def test_minute_download_filters_current_unfinished_candle(self):
        actual = self.both_bars()
        partial_start = self.now.replace(second=0, microsecond=0)
        for symbol in bot.SYMBOLS:
            actual[symbol].append({
                "time": partial_start.isoformat(), "open": 99999.0,
                "high": 100000., "low": 99998., "close": 99999.,
                "complete": False,
            })
        with patch.object(bot, "download_crypto_minute", side_effect=lambda symbol: (actual[symbol], "fake")):
            rows, sources, stamp = bot.download_confirmed(self.now)
        self.assertEqual(stamp, self.bars()[-1]["time"])
        self.assertEqual(rows["BTC-USD"][-1]["close"], self.bars()[-1]["close"])
        self.assertNotEqual(rows["BTC-USD"][-1]["close"], 99999.0)

    def test_missing_recent_minute_rejected_without_virtual_fill(self):
        source = self.both_bars()
        source["ETH-USD"].pop(-10)
        with patch.object(bot, "download_crypto_minute", side_effect=lambda symbol: (source[symbol], "fake")):
            with self.assertRaisesRegex(RuntimeError, "Missing recent minute"):
                bot.download_confirmed(self.now)

    def test_stale_latest_minute_rejected(self):
        old = self.now - timedelta(minutes=8)
        source = self.both_bars(end=old)
        with patch.object(bot, "download_crypto_minute", side_effect=lambda symbol: (source[symbol], "fake")):
            with self.assertRaisesRegex(RuntimeError, "Stale"):
                bot.download_confirmed(self.now)

    def test_unequal_last_crypto_minutes_rejected(self):
        source = self.both_bars()
        source["ETH-USD"] = self.bars(
            end=self.now - timedelta(seconds=135), symbol="ETH-USD",
        )
        with patch.object(bot, "download_crypto_minute", side_effect=lambda symbol: (source[symbol], "fake")):
            with self.assertRaisesRegex(RuntimeError, "mismatch"):
                bot.download_confirmed(self.now)

    def test_next_completed_minute_virtual_buy_and_idempotency(self):
        state = account.fresh_state()
        state["bar_interval"] = "1m"
        sources = {symbol: "fake" for symbol in bot.SYMBOLS}
        bars = self.both_bars()
        bar_time = bars["BTC-USD"][-1]["time"]
        next_time = (datetime.fromisoformat(bar_time) + timedelta(minutes=1)).isoformat()
        with patch.object(account, "signal", return_value=(True, "test signal")):
            first = bot.process_minute(state, bars, sources, bar_time)
            self.assertEqual(first["virtual_fills"], [])
            self.assertEqual(state["cash"], 1000)
            self.assertEqual(set(state["pending"]["targets"]), set(bot.SYMBOLS))
            second_bars = self.both_bars(end=datetime.fromisoformat(next_time))
            second = bot.process_minute(state, second_bars, sources, next_time)
            self.assertEqual(len(second["virtual_fills"]), 2)
            self.assertEqual({row["side"] for row in second["virtual_fills"]}, {"BUY"})
            self.assertLess(state["cash"], 1000)
            third = bot.process_minute(state, second_bars, sources, next_time)
            self.assertEqual(third["status"], "already_processed")
            self.assertEqual(len(account.trade_history()), 2)
            self.assertEqual(json.loads(account.CHART.read_text())["candle_interval"], "1m")

    def test_missing_minute_drops_old_target_instead_of_retroactively_filling(self):
        state = account.fresh_state()
        state["bar_interval"] = "1m"
        sources = {symbol: "fake" for symbol in bot.SYMBOLS}
        bars = self.both_bars()
        state["last_bar"] = "2026-10-19T13:00:00+00:00"
        state["pending"] = {"time": state["last_bar"], "targets": ["BTC-USD"]}
        later = bot.process_minute(state, bars, sources, bars["BTC-USD"][-1]["time"])
        self.assertEqual(later["virtual_fills"], [])
        self.assertEqual(state["positions"], {})
        self.assertEqual(account.trade_history(), [])

    def test_missing_pending_does_not_liquidate_existing_btc_holding(self):
        state = account.fresh_state()
        state["bar_interval"] = "1m"
        state["cash"] = 750
        state["positions"]["BTC-USD"] = .003
        state["entry_prices"]["BTC-USD"] = 101
        sources = {symbol: "fake" for symbol in bot.SYMBOLS}
        bars = self.both_bars()
        result = bot.process_minute(state, bars, sources, bars["BTC-USD"][-1]["time"])
        self.assertEqual(result["virtual_fills"], [])
        self.assertEqual(state["positions"], {"BTC-USD": .003})

    def test_journal_recovery_is_idempotent(self):
        fill = {
            "time": "2026-10-19T14:00:00+00:00", "side": "BUY", "symbol": "BTC-USD",
            "quantity": 1.0, "price": 100, "fees": 0.2, "reason": "test",
        }
        bot.journal_missing_fills([fill])
        bot.journal_missing_fills([fill])
        self.assertEqual(len(account.trade_history()), 1)

    def test_legacy_hourly_runner_refuses_migrated_account_before_network(self):
        state = account.fresh_state()
        state["bar_interval"] = "1m"
        account.save_json(account.STATE, state)
        with patch.object(account, "download") as fetch:
            with self.assertRaisesRegex(RuntimeError, "minute-mode"):
                account.run_once()
            fetch.assert_not_called()

    def test_minute_runner_lock_cannot_be_held_twice(self):
        with bot.paper_lock():
            with self.assertRaisesRegex(RuntimeError, "portfolio lock"):
                with bot.paper_lock():
                    pass


if __name__ == "__main__":
    unittest.main()
