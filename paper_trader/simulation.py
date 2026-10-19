"""On-demand historical market simulator for rule strategies and the ML candidate.

This replays historical data in chronological order using the existing backtest engines.
It never reads or writes the live paper portfolio state and never places broker orders.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import INITIAL_CASH, MIN_RESEARCH_BENCHMARK_BARS, MIN_RESEARCH_SYMBOL_COVERAGE, MIN_RESEARCH_SYMBOLS_WITH_300_BARS, STRATEGIES
from .market_data import BENCHMARK_KEY, download_market_data
from .ml_research import MODEL_NAME, backtest_lightgbm_research
from .research import backtest_strategy

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "simulation.json"
ALLOWED_SESSIONS = {252, 504, 756, 1008}
ALLOWED_COST_MULTIPLIERS = {1.0, 1.5, 2.0}
ALL_STRATEGIES = set(STRATEGIES) | {MODEL_NAME}


def _summary(result: dict[str, Any], initial_cash: float) -> dict[str, Any]:
    keys = (
        "status", "strategy", "model_family", "sample_start", "sample_end", "sessions",
        "evaluation_sessions", "total_return_pct", "annualized_return_pct",
        "annualized_volatility_pct", "sharpe_approx", "max_drawdown_pct",
        "benchmark_total_return_pct", "excess_return_vs_nifty_pct", "trade_count",
        "risk_lock_triggered", "risk_lock_date", "risk_lock_active_at_end",
        "model_refresh_count", "training_sample_count_available",
        "training_sample_count_last_fit", "prediction_diagnostics", "walk_forward",
        "message", "methodology", "limitations",
    )
    summary = {key: result[key] for key in keys if key in result}
    scale = initial_cash / INITIAL_CASH
    if "start_equity" in result:
        summary["start_equity"] = round(float(result["start_equity"]) * scale, 2)
    if "end_equity" in result:
        summary["end_equity"] = round(float(result["end_equity"]) * scale, 2)
    summary["initial_cash"] = round(initial_cash, 2)
    return summary


def build_simulation_report(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    strategy: str = "trend_momentum_126",
    initial_cash: float = INITIAL_CASH,
    evaluation_sessions: int = 756,
    cost_multiplier: float = 1.0,
) -> dict[str, Any]:
    """Build one isolated historical simulation report with a strict data gate."""
    if strategy not in ALL_STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    if not math.isfinite(float(initial_cash)) or not 1 <= float(initial_cash) <= 1_000_000_000:
        raise ValueError("initial_cash must be between 1 and 1,000,000,000")
    if evaluation_sessions not in ALLOWED_SESSIONS:
        raise ValueError(f"evaluation_sessions must be one of {sorted(ALLOWED_SESSIONS)}")
    if float(cost_multiplier) not in ALLOWED_COST_MULTIPLIERS:
        raise ValueError(f"cost_multiplier must be one of {sorted(ALLOWED_COST_MULTIPLIERS)}")

    benchmark = bars_by_symbol.get(BENCHMARK_KEY, [])
    benchmark_dates = sorted(str(row.get("date")) for row in benchmark if row.get("date"))
    stocks = {symbol: rows for symbol, rows in bars_by_symbol.items() if symbol != BENCHMARK_KEY}
    long_history = sum(len(rows) >= 300 for rows in stocks.values())
    checks = {
        "stock_histories": len(stocks),
        "stock_histories_with_300_bars": long_history,
        "benchmark_bars": len(benchmark_dates),
        "minimum_stock_histories": MIN_RESEARCH_SYMBOL_COVERAGE,
        "minimum_long_histories": MIN_RESEARCH_SYMBOLS_WITH_300_BARS,
        "minimum_benchmark_bars": MIN_RESEARCH_BENCHMARK_BARS,
    }
    failures = []
    if len(stocks) < MIN_RESEARCH_SYMBOL_COVERAGE:
        failures.append(f"Only {len(stocks)} stock histories; need at least {MIN_RESEARCH_SYMBOL_COVERAGE}.")
    if long_history < MIN_RESEARCH_SYMBOLS_WITH_300_BARS:
        failures.append(f"Only {long_history} stocks have 300+ bars; need at least {MIN_RESEARCH_SYMBOLS_WITH_300_BARS}.")
    if len(benchmark_dates) < MIN_RESEARCH_BENCHMARK_BARS:
        failures.append(f"Only {len(benchmark_dates)} benchmark bars; need at least {MIN_RESEARCH_BENCHMARK_BARS}.")

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "historical_walk_forward_simulation",
        "status": "blocked_insufficient_market_data" if failures else "running",
        "configuration": {
            "strategy": strategy,
            "initial_cash": round(float(initial_cash), 2),
            "evaluation_sessions": evaluation_sessions,
            "cost_multiplier": float(cost_multiplier),
            "data_source": "Yahoo Finance via yfinance (unofficial historical data)",
        },
        "data_quality": {
            "status": "blocked" if failures else "ok",
            "checks": checks,
            "failed_checks": failures,
        },
        "safety": {
            "isolated_from_live_paper_portfolio": True,
            "uses_future_data_for_training": False,
            "broker_orders_enabled": False,
            "active_strategy_changed": False,
        },
    }
    if failures:
        report["result"] = {"status": "blocked_insufficient_market_data", "message": "No performance metrics were calculated because the data-quality gate failed."}
        return report

    if strategy == MODEL_NAME:
        result = backtest_lightgbm_research(
            bars_by_symbol, cost_multiplier=float(cost_multiplier), evaluation_sessions=evaluation_sessions
        )
    else:
        result = backtest_strategy(
            bars_by_symbol, strategy, sample_days=evaluation_sessions, cost_multiplier=float(cost_multiplier)
        )
    report["result"] = _summary(result, float(initial_cash))
    if result.get("status") == "ok" and "replay_equity_points" in result:
        scale = float(initial_cash) / INITIAL_CASH
        report["replay"] = {
            "benchmark_candles": [
                {"date": str(row["date"]), **{k: round(float(row[k]), 2) for k in ("open", "high", "low", "close")}}
                for row in benchmark
                if str(row.get("date")) in {p["date"] for p in result["replay_equity_points"]}
                and all(row.get(k) is not None and math.isfinite(float(row[k])) and float(row[k]) > 0 for k in ("open", "high", "low", "close"))
            ],
            "equity_points": [{"date": p["date"], "equity": round(float(p["equity"]) * scale, 2)} for p in result["replay_equity_points"]],
            "trades": [{**t, **{key: round(float(t[key]) * scale, 2) for key in ("gross_value", "costs", "cash_after") if key in t}, "quantity": t["quantity"]} for t in result["replay_trades"]],
            "note": "Trades are generated at the original ₹10,000 sizing. Monetary totals are scaled for display when initial cash differs; share quantities remain original.",
        }
    report["status"] = "ok" if result.get("status") == "ok" else str(result.get("status") or "simulation_incomplete")
    report["data_quality"]["message"] = (
        "Historical coverage gate passed. This does not remove survivorship bias or guarantee data accuracy."
    )
    report["interpretation"] = (
        "This is a historical replay, not a live market simulator. Rule strategies use only bars available by each signal date "
        "and simulate next-session-open fills with configured costs/slippage. The LightGBM option trains on chronological "
        "historical samples with matured labels purged before each signal date. Results are out-of-sample historical estimates, "
        "not forecasts or proof of future profitability."
    )
    return report


def run_simulation(
    *,
    strategy: str,
    initial_cash: float,
    evaluation_sessions: int,
    cost_multiplier: float,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    bars = download_market_data()
    report = build_simulation_report(
        bars,
        strategy=strategy,
        initial_cash=initial_cash,
        evaluation_sessions=evaluation_sessions,
        cost_multiplier=cost_multiplier,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=sorted(ALL_STRATEGIES), default="trend_momentum_126")
    parser.add_argument("--initial-cash", type=float, default=INITIAL_CASH)
    parser.add_argument("--sessions", type=int, choices=sorted(ALLOWED_SESSIONS), default=756)
    parser.add_argument("--cost-multiplier", type=float, choices=sorted(ALLOWED_COST_MULTIPLIERS), default=1.0)
    args = parser.parse_args()
    run_simulation(
        strategy=args.strategy,
        initial_cash=args.initial_cash,
        evaluation_sessions=args.sessions,
        cost_multiplier=args.cost_multiplier,
    )
    # A failed data gate is a valid, saved outcome, not a software crash.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
