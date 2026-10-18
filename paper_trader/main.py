"""Entry point for one scheduled, end-of-day paper-trading run."""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from .config import ACTIVE_STRATEGY, MIN_CURRENT_SYMBOL_COVERAGE
from .core import benchmark_dates, execute_rebalance, first_session_after, mark_to_market, new_state, select_targets
from .market_data import BENCHMARK_KEY, download_market_data
from .symbols import NIFTY50_SYMBOLS

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"
TRADES_PATH = DATA_DIR / "trades.csv"
RUNS_PATH = DATA_DIR / "runs.jsonl"
IST = ZoneInfo("Asia/Kolkata")
TRADE_FIELDS = ["date", "signal_date", "side", "symbol", "quantity", "price", "gross_value", "costs", "reason", "cash_after"]


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return new_state()
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError("data/state.json is unreadable; refusing to reset the virtual portfolio") from exc
    required = {"schema_version", "initial_cash", "cash", "positions", "pending_target", "last_signal_date", "peak_equity", "risk_locked"}
    if not isinstance(state, dict) or not required.issubset(state):
        raise RuntimeError("data/state.json is missing required fields; refusing to reset the virtual portfolio")
    if not isinstance(state["positions"], dict) or not isinstance(state["cash"], (int, float)):
        raise RuntimeError("data/state.json has an invalid portfolio structure; refusing to trade")
    cash = float(state["cash"])
    if not math.isfinite(cash) or cash < 0:
        raise RuntimeError("data/state.json has invalid cash; refusing to trade")
    for symbol, quantity in state["positions"].items():
        if not isinstance(symbol, str) or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise RuntimeError("data/state.json has an invalid holding; refusing to trade")
    pending = state.get("pending_target")
    if pending is not None:
        if not isinstance(pending, dict) or not isinstance(pending.get("signal_date"), str) or not isinstance(pending.get("target_symbols"), list):
            raise RuntimeError("data/state.json has an invalid pending target; refusing to trade")
    return state


def _append_run(record: dict[str, Any]) -> None:
    RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def _append_trades(trades: list[dict[str, Any]]) -> None:
    if not trades:
        return
    TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not TRADES_PATH.exists() or TRADES_PATH.stat().st_size == 0
    with TRADES_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(trades)


def _row_by_date(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["date"]): row for row in rows if row.get("date")}


def _skip(status: str, details: dict[str, Any] | None = None) -> int:
    record = {
        "timestamp_ist": datetime.now(IST).isoformat(timespec="seconds"),
        "status": status,
        **(details or {}),
    }
    _append_run(record)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


def _equity_prices(bars: dict[str, list[dict[str, Any]]], field: str, date: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol, rows in bars.items():
        row = _row_by_date(rows).get(date)
        if row is not None:
            try:
                price = float(row[field])
                if math.isfinite(price) and price > 0:
                    prices[symbol] = price
            except (TypeError, ValueError, KeyError):
                continue
    return prices


def run_once() -> int:
    now = datetime.now(IST)
    today = now.date().isoformat()
    print(f"India Paper Trader run started at {now.isoformat(timespec='seconds')}")
    try:
        bars = download_market_data()
    except Exception as exc:
        record = {
            "timestamp_ist": now.isoformat(timespec="seconds"),
            "status": "market_data_error",
            "error_type": type(exc).__name__,
            "message": str(exc)[:350],
            "trades": 0,
        }
        _append_run(record)
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return 1

    benchmark = bars.get(BENCHMARK_KEY, [])
    if not benchmark:
        return _skip("skipped_no_benchmark")
    latest_market_date = max(str(row["date"]) for row in benchmark)
    if latest_market_date != today:
        return _skip("skipped_no_current_session_data", {
            "today_ist": today,
            "latest_market_date": latest_market_date,
            "reason": "No benchmark bar dated today; market may be closed or provider data may be late. No portfolio trades were made.",
        })

    current_bars: dict[str, list[dict[str, Any]]] = {}
    for symbol in NIFTY50_SYMBOLS:
        rows = bars.get(symbol, [])
        if len(rows) >= 201 and rows[-1]["date"] == latest_market_date:
            current_bars[symbol] = rows
    if len(current_bars) < MIN_CURRENT_SYMBOL_COVERAGE:
        return _skip("skipped_insufficient_symbol_coverage", {
            "market_date": latest_market_date,
            "valid_symbols": len(current_bars),
            "minimum_required": MIN_CURRENT_SYMBOL_COVERAGE,
            "reason": "Insufficient current data coverage; no portfolio trades were made.",
        })

    state = _load_state()
    # Fail closed if even one existing holding is missing the current session's price.
    close_prices = _equity_prices(bars, "close", latest_market_date)
    open_prices_today = _equity_prices(bars, "open", latest_market_date)
    for symbol in state["positions"]:
        if symbol not in close_prices or symbol not in open_prices_today:
            return _skip("skipped_missing_held_position_price", {
                "market_date": latest_market_date,
                "symbol": symbol,
                "reason": "A held position has no current price; no portfolio changes were made.",
            })

    all_dates = benchmark_dates(bars)
    completed_trades: list[dict[str, Any]] = []
    pending = state.get("pending_target")
    if pending and str(pending.get("signal_date", "")) < latest_market_date:
        fill_date = first_session_after(str(pending["signal_date"]), all_dates)
        if fill_date is not None and fill_date <= latest_market_date:
            fill_open_prices = _equity_prices(bars, "open", fill_date)
            needed_symbols = set(state["positions"]) | set(pending.get("target_symbols", []))
            if not needed_symbols.issubset(fill_open_prices):
                return _skip("skipped_pending_order_missing_price", {
                    "signal_date": pending["signal_date"],
                    "fill_date": fill_date,
                    "missing_symbols": sorted(needed_symbols - set(fill_open_prices)),
                    "reason": "Pending order could not be filled safely; it remains pending.",
                })
            completed_trades = execute_rebalance(
                state,
                list(pending.get("target_symbols", [])),
                fill_open_prices,
                fill_date,
                str(pending["signal_date"]),
            )
            state["pending_target"] = None
            _append_trades(completed_trades)

    snapshot = mark_to_market(state, close_prices)
    state["last_mark_date"] = latest_market_date

    if state.get("last_signal_date") != latest_market_date:
        if state.get("risk_locked", False):
            targets: list[str] = []
            decision = "risk_lock_exit_to_cash"
        else:
            targets = select_targets(current_bars, ACTIVE_STRATEGY, benchmark_bars=benchmark)
            decision = "signal_created"
        state["pending_target"] = {"signal_date": latest_market_date, "target_symbols": targets}
        state["last_signal_date"] = latest_market_date
    else:
        targets = list((state.get("pending_target") or {}).get("target_symbols", []))
        decision = "already_processed_this_session"

    # Heavy historical and LightGBM research runs in research.yml, never in the daily trader.
    # Do not overwrite the research report with a rule-only or partial report here.
    research_status = "separate_research_workflow"

    _atomic_json(STATE_PATH, state)
    record = {
        "timestamp_ist": now.isoformat(timespec="seconds"),
        "status": "ok",
        "market_date": latest_market_date,
        "data_source": "Yahoo Finance via yfinance (unofficial; research/paper simulation only)",
        "symbols_with_current_bars": len(current_bars),
        "active_strategy": ACTIVE_STRATEGY,
        "decision": decision,
        "target_symbols_for_next_session": targets,
        "trades_filled_this_run": len(completed_trades),
        "cash": snapshot["cash"],
        "position_value": snapshot["position_value"],
        "equity": snapshot["equity"],
        "drawdown_pct": snapshot["drawdown_pct"],
        "exposure_pct": snapshot["exposure_pct"],
        "risk_locked": bool(state.get("risk_locked", False)),
        "research_report": research_status,
    }
    _append_run(record)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return run_once()


if __name__ == "__main__":
    sys.exit(main())
