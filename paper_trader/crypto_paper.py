"""Isolated 24/7 BTC/ETH hourly PAPER trading. No exchange credentials or orders.

Uses confirmed hourly Yahoo Finance bars, delayed simulated fills at the
most recently confirmed close, and a separate USD virtual portfolio.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATE = DATA / "crypto_state.json"
TRADES = DATA / "crypto_trades.csv"
CHART = DATA / "crypto_chart.json"
SYMBOLS = ("BTC-USD", "ETH-USD")
MODELS = ("ema_rsi", "momentum_24h")
START_CASH = 1000.0
MAX_WEIGHT = 0.25
FEE_RATE = 0.002
SLIPPAGE_RATE = 0.001
MAX_DRAWDOWN = 0.10


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def fresh_state():
    return {
        "schema_version": 1, "currency": "USD", "initial_cash": START_CASH,
        "cash": START_CASH, "positions": {}, "entry_prices": {},
        "peak_equity": START_CASH, "risk_locked": False,
        "last_bar": None, "pending": None, "model": "ema_rsi",
    }


def load_state():
    if not STATE.exists():
        return fresh_state()
    value = json.loads(STATE.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError("Invalid crypto state; refusing to reset")
    if value.get("model") not in MODELS or not isinstance(value.get("positions"), dict):
        raise RuntimeError("Invalid crypto strategy/positions; refusing to trade")
    if not math.isfinite(float(value.get("cash", -1))) or float(value["cash"]) < 0:
        raise RuntimeError("Invalid crypto cash")
    return value


def _binance_klines(symbol):
    """Read-only hourly klines from public market data endpoints, no credentials."""
    pair = symbol.replace("-USD", "USDT").replace("-", "")
    hosts = ("https://data-api.binance.vision", "https://api.binance.com")
    last_error = None
    for host in hosts:
        url = host + "/api/v3/klines?symbol=" + pair + "&interval=1h&limit=500"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "NEON-PaperTrader/1.0"})
            with urllib.request.urlopen(request, timeout=12) as response:
                raw = json.loads(response.read(2_000_000).decode("utf-8"))
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            rows = []
            for item in raw:
                open_ms, close_ms = int(item[0]), int(item[6])
                if close_ms + 5 * 60 * 1000 > now_ms:
                    continue
                op, hi, lo, cl = [float(item[i]) for i in (1, 2, 3, 4)]
                if not all(math.isfinite(v) and v > 0 for v in (op, hi, lo, cl)):
                    continue
                rows.append({"time": datetime.fromtimestamp(open_ms / 1000, timezone.utc).isoformat(),
                             "open": op, "high": hi, "low": lo, "close": cl})
            if len(rows) >= 60:
                return rows
            last_error = "insufficient hourly candles"
        except (OSError, ValueError, IndexError, TypeError, KeyError) as exc:
            last_error = str(exc)
    raise RuntimeError(f"Binance public candles unavailable for {symbol}: {last_error}")


def _yahoo_hourly(symbol):
    import pandas as pd
    import yfinance as yf
    now = datetime.now(timezone.utc)
    frame = yf.download(symbol, period="60d", interval="1h", auto_adjust=True,
                        actions=False, progress=False, threads=False, timeout=12)
    if frame is None or frame.empty:
        raise RuntimeError("Yahoo returned empty price history")
    if isinstance(frame.columns, pd.MultiIndex):
        for level in range(frame.columns.nlevels):
            if {"Open", "Close"}.issubset(set(frame.columns.get_level_values(level))):
                frame.columns = frame.columns.get_level_values(level)
                break
    rows = []
    for index, r in frame.iterrows():
        ts = pd.Timestamp(index)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        ts = ts.to_pydatetime()
        if ts + timedelta(hours=1, minutes=5) > now:
            continue
        try:
            o, h, l, close = [float(r[k]) for k in ("Open", "High", "Low", "Close")]
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(v) and v > 0 for v in (o, h, l, close)):
            continue
        rows.append({"time": ts.isoformat(), "open": o, "high": h, "low": l, "close": close})
    if len(rows) < 60:
        raise RuntimeError("Insufficient Yahoo hourly data")
    return rows


def download():
    """Prefer crypto-native public BTC/USDT and ETH/USDT candles.

    USDT is treated as an approximate USD quote for the virtual USD ledger;
    a peg deviation can cause a valuation difference. Never silently join
    bars from multiple sources within one market series.
    """
    output = {}
    sources = {}
    for symbol in SYMBOLS:
        failures = []
        try:
            rows = _binance_klines(symbol)
            source = "Binance public USDT market candles (~USD proxy)"
        except (RuntimeError, OSError, ValueError) as exc:
            failures.append("Binance: " + str(exc))
            try:
                rows = _yahoo_hourly(symbol)
                source = "Yahoo Finance USD market candles"
            except (RuntimeError, OSError, ValueError) as yahoo_exc:
                failures.append("Yahoo: " + str(yahoo_exc))
                raise RuntimeError(f"No crypto market data for {symbol}. Check DNS/internet access. " + "; ".join(failures)) from yahoo_exc
        output[symbol] = rows
        sources[symbol] = source
    return output, sources


def rsi14(closes):
    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - 14, len(closes))]
    gain = sum(max(x, 0) for x in changes) / 14
    loss = sum(max(-x, 0) for x in changes) / 14
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def signal(rows, model):
    closes = [r["close"] for r in rows]
    if len(closes) < 55:
        return False, "Insufficient history"
    if model == "ema_rsi":
        ema20 = ema50 = closes[0]
        for price in closes[1:]:
            ema20 += (2 / 21) * (price - ema20)
            ema50 += (2 / 51) * (price - ema50)
        rsi = rsi14(closes)
        yes = closes[-1] > ema20 > ema50 and 50 <= rsi <= 70
        return yes, f"EMA20={ema20:.2f}, EMA50={ema50:.2f}, RSI={rsi:.1f}"
    prior_high = max(closes[-25:-1])
    yes = closes[-1] > prior_high and closes[-1] > sum(closes[-50:]) / 50
    return yes, f"24-hour close breakout above {prior_high:.2f}"


def nav(state, prices):
    return float(state["cash"]) + sum(float(qty) * prices[s] for s, qty in state["positions"].items())


def rebalance(state, targets, prices, bar_time):
    events = []
    positions = state["positions"]
    # Sell first; all prices are observed completed closes, with adverse slippage.
    for symbol in list(positions):
        if symbol not in targets or state["risk_locked"]:
            qty = float(positions.pop(symbol))
            price = prices[symbol] * (1 - SLIPPAGE_RATE)
            gross = qty * price
            fees = gross * FEE_RATE
            state["cash"] += gross - fees
            state["entry_prices"].pop(symbol, None)
            events.append({"time": bar_time, "side": "SELL", "symbol": symbol,
                           "quantity": round(qty, 8), "price": round(price, 4),
                           "fees": round(fees, 4), "reason": "Exit signal / risk lock"})
    equity = nav(state, prices)
    for symbol in targets:
        if symbol in positions or state["risk_locked"]:
            continue
        price = prices[symbol] * (1 + SLIPPAGE_RATE)
        budget = min(equity * MAX_WEIGHT, state["cash"] / (1 + FEE_RATE))
        if budget < 5:
            continue
        qty = budget / (price * (1 + FEE_RATE))
        cost = qty * price * (1 + FEE_RATE)
        state["cash"] = max(0.0, state["cash"] - cost)
        positions[symbol] = qty
        state["entry_prices"][symbol] = price
        events.append({"time": bar_time, "side": "BUY", "symbol": symbol,
                       "quantity": round(qty, 8), "price": round(price, 4),
                       "fees": round(qty * price * FEE_RATE, 4),
                       "reason": "Previous confirmed hourly signal"})
    return events


def append_trades(events):
    if not events:
        return
    TRADES.parent.mkdir(parents=True, exist_ok=True)
    exists = TRADES.exists() and TRADES.stat().st_size > 0
    with TRADES.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "side", "symbol", "quantity", "price", "fees", "reason"])
        if not exists:
            writer.writeheader()
        writer.writerows(events)


def trade_history():
    if not TRADES.exists():
        return []
    with TRADES.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def export_chart(state, bars, decisions, sources=None):
    save_json(CHART, {
        "mode": "crypto_paper_only", "currency": "USD", "model": state["model"],
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "state": state, "signals": decisions, "market_sources": sources or {},
        "candles": {s: rows[-160:] for s, rows in bars.items()},
        "trades": trade_history(),
        "notes": "Confirmed hourly candles, delayed Yahoo data; no broker/exchange orders.",
    })


def run_once():
    state = load_state()
    bars, sources = download()  # No portfolio changes if either market is unavailable.
    times = {s: rows[-1]["time"] for s, rows in bars.items()}
    if len(set(times.values())) != 1:
        raise RuntimeError(f"Crypto candles are not aligned: {times}; no trades")
    bar_time = next(iter(times.values()))
    prices = {s: bars[s][-1]["close"] for s in SYMBOLS}
    decisions = {s: dict(zip(("buy", "explanation"), signal(bars[s], state["model"]))) for s in SYMBOLS}
    if state.get("last_bar") == bar_time:
        print("Already processed confirmed hour:", bar_time)
        export_chart(state, bars, decisions, sources)
        return
    if state.get("last_bar") and bar_time < state["last_bar"]:
        raise RuntimeError("Market data went backwards; no trades")
    # A missing run must not fill an old signal against a distant later price.
    prior = state.get("pending")
    if prior and state.get("last_bar"):
        age = (datetime.fromisoformat(bar_time) - datetime.fromisoformat(state["last_bar"])).total_seconds()
        if age > 2 * 3600:
            prior = None
            print("Skipped stale pending signal after missed hourly runs")
    # No pending signal means HOLD, not SELL. Especially important after a model switch
    # or missed timer run: do not liquidate holdings merely because a signal expired.
    if prior is not None or state["risk_locked"]:
        events = rebalance(state, prior.get("targets", []) if prior else [], prices, bar_time)
    else:
        events = []
    equity = nav(state, prices)
    state["peak_equity"] = max(float(state["peak_equity"]), equity)
    if equity <= state["peak_equity"] * (1 - MAX_DRAWDOWN):
        state["risk_locked"] = True
        # Liquidate on this observed close if lock triggers.
        events.extend(rebalance(state, [], prices, bar_time))
        equity = nav(state, prices)
    state["pending"] = {"time": bar_time, "targets": [s for s in SYMBOLS if decisions[s]["buy"]]} if not state["risk_locked"] else None
    state["last_bar"] = bar_time
    # Persist before journal; if interrupted, no duplicated trading on rerun.
    save_json(STATE, state)
    append_trades(events)
    export_chart(state, bars, decisions, sources)
    print(json.dumps({"hour_utc": bar_time, "model": state["model"], "equity_usd": round(equity, 2),
                      "cash_usd": round(state["cash"], 2), "market_sources": sources, "paper_fills": events,
                      "next_hour_targets": state["pending"], "risk_locked": state["risk_locked"]}, indent=2))


def start_btc_now():
    """Explicitly bootstrap one small virtual BTC position on the current
    confirmed close. Never runs on an existing portfolio or repeats a buy.
    """
    if STATE.exists():
        raise RuntimeError("Existing crypto portfolio detected. --start-now cannot modify it. Use normal hourly run.")
    bars, sources = download()
    times = {s: rows[-1]["time"] for s, rows in bars.items()}
    if len(set(times.values())) != 1:
        raise RuntimeError(f"Crypto candles are not aligned: {times}; no trades")
    t = next(iter(times.values()))
    state = fresh_state()
    # A 5% virtual starting position; normal strategy takes over at the next
    # observed confirmed hourly candle. This entry is user-requested, not
    # described as model-generated or profitable.
    price = bars["BTC-USD"][-1]["close"] * (1 + SLIPPAGE_RATE)
    budget = START_CASH * 0.05
    quantity = budget / (price * (1 + FEE_RATE))
    fee = quantity * price * FEE_RATE
    state["positions"]["BTC-USD"] = quantity
    state["entry_prices"]["BTC-USD"] = price
    state["cash"] -= budget
    state["last_bar"] = t
    state["pending"] = {"time": t, "targets": ["BTC-USD"]}
    event = {"time": t, "side": "BUY", "symbol": "BTC-USD",
             "quantity": round(quantity, 8), "price": round(price, 4),
             "fees": round(fee, 4), "reason": "User-requested 5% virtual BTC bootstrap (not a model signal)"}
    save_json(STATE, state)
    append_trades([event])
    decisions = {s: dict(zip(("buy", "explanation"), signal(bars[s], state["model"]))) for s in SYMBOLS}
    export_chart(state, bars, decisions, sources)
    print(json.dumps({"status": "paper_started_now", "market_hour_utc": t,
                      "virtual_cash_usd": round(state["cash"], 2),
                      "user_requested_virtual_btc_buy": event,
                      "note": "Not a historical fill or model recommendation; no real BTC bought."}, indent=2))


def main():
    parser = argparse.ArgumentParser(description="24/7 hourly crypto PAPER trading, never real exchange orders")
    parser.add_argument("--set-model", choices=MODELS)
    parser.add_argument("--cancel-pending", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--start-now", action="store_true", help="Start the paper portfolio now; only a conservative 5 percent BTC entry, not a guarantee of profit")
    args = parser.parse_args()
    if args.start_now:
        if args.set_model or args.cancel_pending or args.status:
            parser.error("--start-now cannot be combined with other switches")
        start_btc_now()
        return
    if args.set_model:
        state = load_state()
        if state["risk_locked"]:
            parser.error("Risk lock active; refusing strategy switch")
        if state.get("pending") and not args.cancel_pending:
            parser.error("Pending crypto signal exists. Add --cancel-pending to discard it.")
        state["pending"] = None
        state["model"] = args.set_model
        save_json(STATE, state)
        print("Selected crypto paper model:", args.set_model, "(no orders executed)")
        return
    if args.status:
        print(json.dumps(load_state(), indent=2))
        return
    run_once()


if __name__ == "__main__":
    main()
