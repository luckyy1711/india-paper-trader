"""24/7 BTC/ETH one-minute PAPER engine; never sends real exchange orders.

Decisions use *completed* 1-minute OHLC only; pending targets fill virtually
on the next distinct, confirmed 1-minute close. This is an educational
simulation, not an exchange-realistic execution or live-broker interface.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import json
import math
import shutil
import time
from datetime import datetime, timedelta, timezone

from . import crypto_paper as account
from .crypto_preview import SYMBOLS, download_crypto_minute

INTERVAL = "1m"
MIN_HISTORY = 60
LOOP_SECONDS = 15
MAX_CANDLE_AGE = timedelta(seconds=140)


@contextlib.contextmanager
def paper_lock():
    """Only one local minute runner can touch this virtual portfolio at a time."""
    account.DATA.mkdir(parents=True, exist_ok=True)
    with (account.DATA / ".crypto_paper.lock").open("a+") as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another crypto paper runner has the portfolio lock") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def migrate_portfolio(state: dict) -> dict:
    """Move local account to 1m, never reset holdings, cash or filled trades."""
    if state.get("bar_interval") == INTERVAL:
        return state
    if state.get("bar_interval", "1h") != "1h":
        raise RuntimeError("Unsupported existing crypto portfolio interval")
    if state.get("model") != "ema_rsi":
        raise RuntimeError("Minute mode currently supports EMA/RSI only. Change model explicitly before conversion.")
    if account.STATE.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = account.STATE.with_name("crypto_state.hourly-backup-" + stamp + ".json")
        shutil.copy2(account.STATE, backup)
        print("Backed up your original hourly crypto state to", backup, flush=True)
    old_time = state.get("last_bar")
    old_pending = state.get("pending")
    state["bar_interval"] = INTERVAL
    state["last_bar"] = None
    state["pending"] = None
    state["last_fills"] = []
    state["minute_transition"] = {
        "activated_utc": datetime.now(timezone.utc).isoformat(),
        "previous_interval": "1h",
        "previous_last_bar": old_time,
        "cancelled_unfilled_hourly_targets": (old_pending or {}).get("targets", []),
    }
    account.save_json(account.STATE, state)
    print("Switched to 1m: previous unfilled hourly signal cancelled; virtual cash, holdings and fills preserved.", flush=True)
    return state



def reset_virtual_portfolio() -> dict:
    """Archive only crypto paper data and restart at $1,000 with no fills.

    Caller must own paper_lock(), so the runner cannot race the reset.
    Backup copies remain outside the active ledger and are ignored by git.
    This command never fetches data or submits real orders.
    """
    account.DATA.mkdir(parents=True, exist_ok=True)
    # Check any existing state before changing files. Invalid state fails closed.
    if account.STATE.exists():
        account.load_state()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = account.DATA / "crypto_reset_backups" / stamp
    archive.mkdir(parents=True, exist_ok=False)
    existing = [path for path in (account.STATE, account.TRADES, account.CHART) if path.exists()]
    for path in existing:
        shutil.copy2(path, archive / path.name)

    fresh = account.fresh_state()
    fresh.update({
        "bar_interval": INTERVAL, "last_fills": [],
        "last_bar": None, "pending": None,
        "reset_at_utc": datetime.now(timezone.utc).isoformat(),
    })

    # Truncate active fill ledger with an atomic replacement; don't touch its
    # archived copy. The new snapshot is published before the next candle.
    rows_tmp = account.TRADES.with_name(account.TRADES.name + ".reset-tmp")
    with rows_tmp.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time", "side", "symbol", "quantity", "price", "fees", "reason"])
    rows_tmp.replace(account.TRADES)
    account.save_json(account.STATE, fresh)

    # Immediately remove stale P&L, old markers and old chart history.
    account.save_json(account.CHART, {
        "mode": "crypto_paper_only", "currency": "USD",
        "model": fresh["model"], "candle_interval": INTERVAL,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "state": fresh, "signals": {}, "market_sources": {},
        "performance": account.crypto_performance(fresh, {}, []),
        "candles": {}, "trades": [],
        "notes": "Freshly reset virtual account; no paper candles processed or trades placed yet.",
    })
    return {
        "status": "reset", "virtual_starting_cash_usd": fresh["cash"],
        "open_positions": fresh["positions"], "fills": 0,
        "old_data_archive": str(archive),
        "next": "Run python -m paper_trader.crypto_minute --loop when ready.",
    }


def download_confirmed(now: datetime | None = None):
    """Use matching, recent *completed* candles from both markets or fail closed."""
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() != timedelta(0):
        now = now.astimezone(timezone.utc)
    output, sources = {}, {}
    for symbol in SYMBOLS:
        received, source = download_crypto_minute(symbol)
        bars = [bar for bar in received if bar.get("complete") is True]
        if len(bars) < MIN_HISTORY:
            raise RuntimeError("Not enough confirmed minute bars for " + symbol)
        bars = sorted(bars, key=lambda row: row["time"])
        times = [datetime.fromisoformat(bar["time"]) for bar in bars[-MIN_HISTORY:]]
        if any((b - a) != timedelta(minutes=1) for a, b in zip(times, times[1:])):
            raise RuntimeError("Missing recent minute candles for " + symbol + "; no virtual trades")
        age = now - times[-1]
        if age < timedelta(seconds=60) or age > MAX_CANDLE_AGE:
            raise RuntimeError("Stale or incomplete confirmed minute data for " + symbol + "; no virtual trades")
        for row in bars:
            fields = [row.get(key) for key in ("open", "high", "low", "close")]
            if any(not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in fields):
                raise RuntimeError("Invalid OHLC data for " + symbol)
            if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
                raise RuntimeError("Inconsistent OHLC data for " + symbol)
        output[symbol] = bars
        sources[symbol] = source
    latest = {symbol: output[symbol][-1]["time"] for symbol in SYMBOLS}
    if len(set(latest.values())) != 1:
        raise RuntimeError("BTC/ETH latest confirmed minute mismatch: " + str(latest) + "; no trades")
    return output, sources, next(iter(latest.values()))


def journal_missing_fills(events: list[dict]) -> None:
    """Recover a saved-but-not-journaled virtual fill without duplicating it."""
    if not events:
        return
    existing = {
        (trade.get("time"), trade.get("side"), trade.get("symbol"))
        for trade in account.trade_history()
    }
    missing = [
        fill for fill in events
        if (fill["time"], fill["side"], fill["symbol"]) not in existing
    ]
    account.append_trades(missing)


def process_minute(state: dict, bars: dict, sources: dict, bar_time: str) -> dict:
    """Advance one confirmed minute; process old targets then create future ones."""
    last = state.get("last_bar")
    if last:
        gap = datetime.fromisoformat(bar_time) - datetime.fromisoformat(last)
        if gap < timedelta(0):
            raise RuntimeError("Minute data went backwards; no virtual trades")
        if gap == timedelta(0):
            return {"status": "already_processed", "minute_utc": bar_time}
        if gap < timedelta(minutes=1):
            raise RuntimeError("Overlapping minute timestamps; no trades")
    else:
        gap = None
    prices = {s: float(bars[s][-1]["close"]) for s in SYMBOLS}
    decisions = {
        s: dict(zip(("buy", "explanation"), account.signal(bars[s], state["model"])))
        for s in SYMBOLS
    }
    prev = state.get("pending")
    # Old pending targets are only valid for the *immediately next* minute.
    valid_pending = (
        prev is not None and gap == timedelta(minutes=1) and
        prev.get("time") == last
    )
    if prev is not None and not valid_pending:
        print("Old or missed-minute target expired; not filling at a later price.", flush=True)
    events = []
    if valid_pending or state.get("risk_locked"):
        events = account.rebalance(
            state, prev.get("targets", []) if valid_pending else [], prices, bar_time,
        )

    equity = account.nav(state, prices)
    state["peak_equity"] = max(float(state["peak_equity"]), equity)
    if equity <= float(state["peak_equity"]) * (1 - account.MAX_DRAWDOWN):
        state["risk_locked"] = True
        events.extend(account.rebalance(state, [], prices, bar_time))
        equity = account.nav(state, prices)

    state["pending"] = {
        "time": bar_time, "targets": [s for s in SYMBOLS if decisions[s]["buy"]]
    } if not state["risk_locked"] else None
    state["last_bar"] = bar_time
    state["last_fills"] = events  # crash-safe trade journal recovery on next run
    account.save_json(account.STATE, state)
    journal_missing_fills(events)
    account.export_chart(state, bars, decisions, sources)
    performance = account.crypto_performance(state, bars, account.trade_history())
    return {
        "status": "processed",
        "minute_utc": bar_time,
        "interval": INTERVAL,
        "model": state["model"],
        "virtual_equity_usd": round(equity, 2),
        "virtual_cash_usd": round(state["cash"], 2),
        "initial_virtual_capital_usd": round(performance["initial_capital_usd"], 2),
        "open_holdings_value_usd": round(performance["holdings_market_value_usd"], 2),
        "net_paper_profit_usd": round(performance["net_pnl_usd"], 2),
        "net_paper_return_pct": round(performance["net_return_pct"], 3),
        "realized_paper_profit_usd": (
            round(performance["realized_pnl_usd"], 2)
            if performance["realized_pnl_usd"] is not None else None
        ),
        "virtual_fills": events,
        "next_minute_targets": state["pending"],
        "risk_locked": state["risk_locked"],
        "sources": sources,
    }


def run_once() -> dict:
    state = account.load_state()
    # Strictly refuse unsupported modes *before* portfolio conversion.
    if state.get("model") != "ema_rsi":
        raise RuntimeError("Minute strategy requires ema_rsi; other legacy models have different time semantics.")
    if state.get("bar_interval") != INTERVAL:
        state = migrate_portfolio(state)
    # Recover journal after an interruption between the atomic state and CSV writes.
    journal_missing_fills(state.get("last_fills") or [])
    bars, sources, bar_time = download_confirmed()
    result = process_minute(state, bars, sources, bar_time)
    if result["status"] == "already_processed":
        # Do not rewrite a state file or make a virtual trade more than once per minute.
        return result
    return result


def main():
    parser = argparse.ArgumentParser(description="BTC + ETH minute-candle PAPER trading (never real exchange orders)")
    parser.add_argument("--loop", action="store_true", help="Check every 15s; execute at most once per confirmed minute")
    parser.add_argument("--status", action="store_true", help="Show existing virtual portfolio without starting a trade")
    parser.add_argument("--reset-paper-account", action="store_true", help="Archive crypto paper results and reset to 1,000 virtual USD")
    parser.add_argument("--confirm-reset", action="store_true", help="Required safety confirmation for --reset-paper-account")
    args = parser.parse_args()
    if args.reset_paper_account:
        if not args.confirm_reset or args.loop or args.status:
            parser.error("Reset requires --reset-paper-account --confirm-reset and cannot be combined with --loop or --status")
        with paper_lock():
            print(json.dumps(reset_virtual_portfolio(), indent=2), flush=True)
        return
    if args.confirm_reset:
        parser.error("--confirm-reset only works with --reset-paper-account")
    if args.status:
        print(json.dumps(account.load_state(), indent=2))
        return

    with paper_lock():
        if args.loop:
            print("BTC/ETH 1m PAPER trading started. Ctrl+C to stop. Exchange fills are simulated.", flush=True)
        while True:
            try:
                result = run_once()
                if result["status"] != "already_processed":
                    print(json.dumps(result, indent=2), flush=True)
            except (RuntimeError, ValueError, OSError, KeyError, TypeError) as exc:
                print("Minute paper run skipped:", type(exc).__name__, str(exc), flush=True)
                if not args.loop:
                    raise
            if not args.loop:
                break
            try:
                time.sleep(LOOP_SECONDS)
            except KeyboardInterrupt:
                print("Minute paper runner stopped.", flush=True)
                break


if __name__ == "__main__":
    main()
