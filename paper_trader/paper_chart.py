"""Read-only paper-trading terminal snapshot.

This exporter never runs the trader, mutates portfolio state, or simulates fills.
Research comparisons are read from an existing research report, not recomputed.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import MIN_CURRENT_SYMBOL_COVERAGE, STRATEGIES
from .core import score_symbol, select_targets
from .main import STATE_PATH, TRADES_PATH, _atomic_json, _load_state
from .market_data import BENCHMARK_KEY, download_market_data
from .model_select import current_strategy
from .symbols import NIFTY50_SYMBOLS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "paper_chart.json"
RESEARCH_PATH = ROOT / "data" / "research.json"


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _symbol_reason(rows: list[dict], strategy: str, score: float | None) -> dict:
    """Human-readable close-known features; eligibility itself uses core.score_symbol."""
    params = STRATEGIES[strategy]
    closes = [_finite_positive(row.get("close")) for row in rows]
    closes = [close for close in closes if close is not None]
    window = int(params["trend_window"])
    skip = int(params.get("skip_recent_days", 0))
    periods = [int(x) for x in params.get("momentum_windows", [params["momentum_window"]])]
    latest = closes[-1] if closes else None
    sma = sum(closes[-window:]) / window if len(closes) >= window else None
    momentum = None
    if len(closes) > max(periods) + skip:
        j = len(closes) - 1 - skip
        values = [closes[j] / closes[j - period] - 1 for period in periods]
        momentum = sum(values) / len(values) * 100

    reason = "Qualified: above trend average, positive momentum and usable volatility"
    if score is None:
        if sma is None or momentum is None:
            reason = "Insufficient usable price history"
        elif latest is not None and latest <= sma:
            reason = "Closing price is at or below the trend average"
        elif params["filter"] == "crossover" and len(closes) >= 50 and sum(closes[-50:]) / 50 <= sma:
            reason = "50-session average is not above the 200-session average"
        elif momentum <= 0:
            reason = "Required momentum is not positive"
        else:
            reason = "Did not meet all momentum/volatility checks"

    return {
        "score": round(score, 4) if score is not None else None,
        "close": round(latest, 2) if latest is not None else None,
        "trend_sma": round(sma, 2) if sma is not None else None,
        "trend_window": window,
        "momentum_pct": round(momentum, 2) if momentum is not None else None,
        "momentum_windows": periods,
        "skip_recent_sessions": skip,
        "reason": reason,
    }


def _research_summary(report: dict | None) -> dict:
    if not isinstance(report, dict):
        return {"status": "missing", "message": "Run the separate research workflow for historical comparisons."}
    quality = (report.get("data_quality") or {}).get("status")
    if quality != "ok":
        return {"status": "withheld", "message": "Historical comparison withheld: research data-quality gate did not pass."}
    results = []
    for item in report.get("results", []):
        if not isinstance(item, dict) or item.get("strategy") not in STRATEGIES:
            continue
        periods = (item.get("robustness") or {}).get("period_sensitivity") or {}
        def metrics(obj):
            if not isinstance(obj, dict) or obj.get("status") != "ok":
                return {"status": "unavailable"}
            return {
                "status": "ok",
                "sample_start": obj.get("sample_start"),
                "sample_end": obj.get("sample_end"),
                "sessions": obj.get("sessions"),
                "return_pct": obj.get("total_return_pct"),
                "drawdown_pct": obj.get("max_drawdown_pct"),
                "benchmark_return_pct": obj.get("benchmark_total_return_pct"),
                "trades": obj.get("trade_count"),
            }
        results.append({
            "strategy": item["strategy"],
            "year_1": metrics(periods.get("252")),
            "years_3": metrics(item),
            "risk_lock_triggered": bool(item.get("risk_lock_triggered")),
        })
    return {
        "status": "ok" if results else "missing",
        "generated_at_utc": report.get("generated_at_utc"),
        "results": results,
        "warning": "Backtests are historical simulations, NOT current portfolio P&L or forecasts. Current constituents cause survivorship bias. Periods overlap, dividends are excluded from the benchmark, and a permanent drawdown lock is modeled.",
    }


def build_snapshot(bars: dict, state: dict, trades: list, *, research: dict | None = None, active_strategy: str | None = None) -> dict:
    benchmark = sorted(bars.get(BENCHMARK_KEY, []), key=lambda row: str(row.get("date", "")))
    latest_date = str(benchmark[-1]["date"]) if benchmark else None
    valid = {}
    if latest_date:
        for name in NIFTY50_SYMBOLS:
            rows = bars.get(name) or []
            if (len(rows) >= 201 and str(rows[-1].get("date", "")) == latest_date):
                valid[name] = rows
    coverage_ok = len(valid) >= MIN_CURRENT_SYMBOL_COVERAGE

    model_signals = {}
    for name in STRATEGIES:
        params = STRATEGIES[name]
        market_window = int(params.get("market_regime_window", params["trend_window"]))
        idx_closes = [_finite_positive(row.get("close")) for row in benchmark]
        idx_closes = [x for x in idx_closes if x is not None]
        index_sma = sum(idx_closes[-market_window:]) / market_window if len(idx_closes) >= market_window else None
        index_close = idx_closes[-1] if idx_closes else None
        regime = not params.get("market_regime_filter") or (
            index_sma is not None and index_close is not None and index_close > index_sma
        )
        result = {
            "as_of": latest_date,
            "status": "ok" if coverage_ok else "withheld_insufficient_current_coverage",
            "coverage": len(valid),
            "required_coverage": MIN_CURRENT_SYMBOL_COVERAGE,
            "targets": [],
            "details": [],
            "rejected_examples": [],
            "market_filter_passed": bool(regime),
            "market_filter_enabled": bool(params.get("market_regime_filter")),
            "index_close": round(index_close, 2) if index_close is not None else None,
            "index_trend_sma": round(index_sma, 2) if index_sma is not None else None,
        }
        if coverage_ok:
            targets = select_targets(valid, name, benchmark_bars=benchmark)
            result["targets"] = targets
            for symbol in targets:
                score = score_symbol(valid[symbol], name)
                result["details"].append({"symbol": symbol, **_symbol_reason(valid[symbol], name, score)})
            if not targets:
                result["reason"] = (
                    "Nifty benchmark failed its trend filter: no stock entries"
                    if not regime else "No stocks qualified for the strategy"
                )
            for symbol in sorted(valid):
                if len(result["rejected_examples"]) >= 4:
                    break
                if symbol in targets:
                    continue
                score = score_symbol(valid[symbol], name)
                if score is None:
                    result["rejected_examples"].append({"symbol": symbol, **_symbol_reason(valid[symbol], name, score)})
        else:
            result["reason"] = "Not enough stocks have a full bar for the latest benchmark date; signals withheld"
        model_signals[name] = result

    referenced = {t.get("symbol") for t in trades if t.get("symbol")}
    referenced.update(state.get("positions", {}).keys())
    referenced.update((state.get("pending_target") or {}).get("target_symbols", []))
    referenced.update(s for v in model_signals.values() for s in v["targets"])
    symbols = sorted(s for s in referenced if s in bars and s != BENCHMARK_KEY)
    if not symbols:
        symbols = sorted(s for s in bars if s != BENCHMARK_KEY)[:5]

    return {
        "mode": "actual_paper_portfolio_read_only",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance daily OHLC (unofficial and delayed)",
        "active_strategy": active_strategy if active_strategy is not None else current_strategy(),
        "market_date": latest_date,
        "model_signals": model_signals,
        "research_comparison": _research_summary(research),
        "state": {
            "cash": state.get("cash"),
            "positions": state.get("positions", {}),
            "risk_locked": state.get("risk_locked"),
            "pending_target": state.get("pending_target"),
            "last_signal_date": state.get("last_signal_date"),
            "switch_audit": (state.get("strategy_switch_audit") or [])[-20:],
        },
        "trades": trades,
        "candles": {
            s: [{key: row[key] for key in ("date", "open", "high", "low", "close") if key in row}
                for row in bars[s][-260:]]
            for s in symbols
        },
        "benchmark": [{key: row[key] for key in ("date", "open", "high", "low", "close") if key in row}
                      for row in benchmark[-260:]],
    }


def main() -> None:
    if not STATE_PATH.exists():
        print("No paper portfolio exists. Run the paper trader first.")
        return
    state = _load_state()
    trades = []
    if TRADES_PATH.exists():
        with TRADES_PATH.open(newline="", encoding="utf-8") as file:
            trades = list(csv.DictReader(file))
    research = None
    if RESEARCH_PATH.exists():
        try:
            research = json.loads(RESEARCH_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            print("Research report unreadable; displaying charts without performance comparison.")
    print("Fetching read-only daily bars and calculating strategy explanations...", flush=True)
    snapshot = build_snapshot(download_market_data(), state, trades, research=research)
    _atomic_json(OUT, snapshot)
    print(f"Saved {OUT} | {len(trades)} actual recorded fills | {len(snapshot['candles'])} symbols")


if __name__ == "__main__":
    main()
