"""CPU-only Monte Carlo stress testing on synthetic OHLCV paths.

This is a robustness experiment, not a historical backtest or a forecast. It
reuses the existing rule-strategy backtest engine but never downloads data,
changes paper portfolio state, or places broker orders. One path is held in
memory at a time to keep RAM use modest.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from typing import Any

from .config import INITIAL_CASH, STRATEGIES
from .research import backtest_strategy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "synthetic_simulation.json"
DEFAULT_WARMUP = 260
DEFAULT_SESSIONS = 252


def _trading_dates(count: int, end: date | None = None) -> list[str]:
    current = end or date.today()
    result: list[str] = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current -= timedelta(days=1)
    return list(reversed(result))


def generate_synthetic_bars(
    *, seed: int, symbols: int = 10, warmup_sessions: int = DEFAULT_WARMUP,
    evaluation_sessions: int = DEFAULT_SESSIONS,
) -> dict[str, list[dict[str, Any]]]:
    """Generate a seeded market-correlated path with occasional stress regimes."""
    if not 3 <= symbols <= 50:
        raise ValueError("symbols must be between 3 and 50")
    if not 220 <= warmup_sessions <= 1000:
        raise ValueError("warmup_sessions must be between 220 and 1000")
    if not 20 <= evaluation_sessions <= 756:
        raise ValueError("evaluation_sessions must be between 20 and 756")

    rng = random.Random(seed)
    count = warmup_sessions + evaluation_sessions
    dates = _trading_dates(count)
    market_price = 1000.0
    stock_prices = [50.0 + i * 7.0 for i in range(symbols)]
    market_returns: list[float] = []
    stock_returns: list[list[float]] = [[] for _ in range(symbols)]
    benchmark_rows: list[dict[str, Any]] = []
    stock_rows: dict[str, list[dict[str, Any]]] = {f"SYN{i + 1:02d}": [] for i in range(symbols)}
    stressed = False
    stress_left = 0

    for day in dates:
        if stress_left <= 0 and rng.random() < 0.025:
            stressed = not stressed
            stress_left = rng.randint(5, 25) if stressed else rng.randint(10, 45)
        stress_left -= 1
        market_vol = 0.027 if stressed else 0.011
        market_drift = -0.0007 if stressed else 0.00025
        market_r = max(-0.22, min(0.18, rng.gauss(market_drift, market_vol)))
        market_returns.append(market_r)
        previous_market = market_price
        market_price *= math.exp(market_r)
        market_open = previous_market * math.exp(rng.gauss(0, market_vol * 0.25))
        market_high = max(market_open, market_price) * (1 + abs(rng.gauss(0, market_vol * 0.35)))
        market_low = min(market_open, market_price) * (1 - min(0.35, abs(rng.gauss(0, market_vol * 0.35))))
        benchmark_rows.append({
            "date": day, "open": round(market_open, 4), "high": round(market_high, 4),
            "low": round(market_low, 4), "close": round(market_price, 4),
            "volume": rng.randint(1_000_000, 10_000_000),
        })

        for index, symbol in enumerate(stock_rows):
            beta = 0.65 + (index % 8) * 0.13
            idio_vol = 0.008 + (index % 5) * 0.003
            drift = (index - (symbols - 1) / 2) * 0.000035
            stock_r = beta * market_r + drift + rng.gauss(0, idio_vol * (2.0 if stressed else 1.0))
            stock_r = max(-0.35, min(0.30, stock_r))
            previous = stock_prices[index]
            close = max(0.5, previous * math.exp(stock_r))
            open_price = previous * math.exp(rng.gauss(0, idio_vol * 0.25))
            intraday = abs(rng.gauss(0, idio_vol * 0.7))
            high = max(open_price, close) * (1 + intraday)
            low = min(open_price, close) * (1 - min(0.4, intraday))
            stock_rows[symbol].append({
                "date": day, "open": round(open_price, 4), "high": round(high, 4),
                "low": round(max(0.01, low), 4), "close": round(close, 4),
                "volume": rng.randint(100_000, 3_000_000),
            })
            stock_prices[index] = close
            stock_returns[index].append(stock_r)

    return {"__BENCHMARK__": benchmark_rows, **stock_rows}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return round(ordered[lower], 3)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 3)


def _save(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_monte_carlo(
    *, hours: float = 10.0, strategy: str = "trend_momentum_126",
    initial_cash: float = INITIAL_CASH, seed: int = 20261019, symbols: int = 10,
    warmup_sessions: int = DEFAULT_WARMUP, evaluation_sessions: int = DEFAULT_SESSIONS,
    max_paths: int = 10000, report_path: Path = DEFAULT_REPORT, checkpoint_every: int = 10,
) -> dict[str, Any]:
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of: {', '.join(sorted(STRATEGIES))}")
    if not math.isfinite(hours) or not 0.01 <= hours <= 12:
        raise ValueError("hours must be between 0.01 and 12")
    if not math.isfinite(initial_cash) or not 1 <= initial_cash <= 1_000_000_000:
        raise ValueError("initial_cash must be between 1 and 1,000,000,000")
    if not 1 <= max_paths <= 1_000_000 or not 1 <= checkpoint_every <= 1000:
        raise ValueError("max_paths must be 1..1,000,000 and checkpoint_every 1..1000")

    started = time.monotonic()
    deadline = started + hours * 3600
    returns: list[float] = []
    benchmark_returns: list[float] = []
    drawdowns: list[float] = []
    sharpes: list[float] = []
    trades: list[int] = []
    positive = 0
    failures = 0
    completed = 0
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "synthetic_monte_carlo_stress_test",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "running",
        "configuration": {
            "strategy": strategy, "initial_cash": round(initial_cash, 2), "seed": seed,
            "symbols": symbols, "warmup_sessions": warmup_sessions,
            "evaluation_sessions": evaluation_sessions, "target_hours": hours,
            "max_paths": max_paths, "model": "seeded market-correlated random OHLCV with regime shifts and idiosyncratic volatility",
        },
        "safety": {
            "synthetic_data_only": True, "historical_data_downloaded": False,
            "isolated_from_live_paper_portfolio": True, "broker_orders_enabled": False,
            "active_strategy_changed": False, "deployable_model_trained": False,
        },
        "results": {},
        "note": "Synthetic paths are for robustness testing only. They are not real-market history, forecasts, or proof of profitability.",
    }
    _save(report_path, report)

    while completed < max_paths and time.monotonic() < deadline:
        try:
            bars = generate_synthetic_bars(
                seed=seed + completed, symbols=symbols,
                warmup_sessions=warmup_sessions, evaluation_sessions=evaluation_sessions,
            )
            result = backtest_strategy(
                bars, strategy, sample_days=evaluation_sessions, cost_multiplier=1.0,
            )
            if result.get("status") != "ok":
                failures += 1
            else:
                ret = float(result.get("total_return_pct", 0.0))
                returns.append(ret)
                benchmark_returns.append(float(result.get("benchmark_total_return_pct", 0.0)))
                drawdowns.append(float(result.get("max_drawdown_pct", 0.0)))
                sharpes.append(float(result.get("sharpe_approx", 0.0)))
                trades.append(int(result.get("trade_count", 0)))
                positive += ret > 0
        except (ValueError, KeyError, TypeError, ArithmeticError):
            failures += 1
        completed += 1

        if completed % checkpoint_every == 0 or completed >= max_paths or time.monotonic() >= deadline:
            elapsed = max(time.monotonic() - started, 1e-9)
            report["status"] = "running"
            report["progress"] = {
                "completed_paths": completed, "successful_paths": len(returns),
                "failed_paths": failures, "elapsed_seconds": round(elapsed, 1),
                "paths_per_second": round(completed / elapsed, 4),
            }
            report["results"] = {
                "positive_return_rate_pct": round(100 * positive / len(returns), 2) if returns else None,
                "strategy_return_pct": {
                    "p05": _percentile(returns, 0.05), "p25": _percentile(returns, 0.25),
                    "median": _percentile(returns, 0.50), "p75": _percentile(returns, 0.75),
                    "p95": _percentile(returns, 0.95),
                },
                "benchmark_return_pct": {
                    "median": _percentile(benchmark_returns, 0.50),
                    "p05": _percentile(benchmark_returns, 0.05),
                    "p95": _percentile(benchmark_returns, 0.95),
                },
                "max_drawdown_pct": {
                    "median": _percentile(drawdowns, 0.50), "p95": _percentile(drawdowns, 0.95),
                },
                "sharpe_approx_median": _percentile(sharpes, 0.50),
                "median_trade_count": _percentile([float(x) for x in trades], 0.50),
                "excess_return_vs_synthetic_benchmark_median_pct": (
                    round(_percentile(returns, 0.50) - _percentile(benchmark_returns, 0.50), 3)
                    if returns and benchmark_returns else None
                ),
            }
            _save(report_path, report)
            print(f"paths={completed}/{max_paths} success={len(returns)} failures={failures} elapsed={elapsed / 3600:.2f}h", flush=True)

    elapsed = max(time.monotonic() - started, 1e-9)
    report["status"] = "completed" if completed >= max_paths else "time_limit_reached"
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["progress"] = {
        "completed_paths": completed, "successful_paths": len(returns),
        "failed_paths": failures, "elapsed_seconds": round(elapsed, 1),
        "paths_per_second": round(completed / elapsed, 4),
    }
    _save(report_path, report)
    print(f"Saved {report_path}; status={report['status']}; paths={completed}; elapsed={elapsed / 3600:.2f}h", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a synthetic Monte Carlo paper-strategy stress test.")
    parser.add_argument("--hours", type=float, default=10.0, help="Wall-clock runtime, 0.01 to 12 hours (default: 10)")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="trend_momentum_126")
    parser.add_argument("--initial-cash", type=float, default=INITIAL_CASH)
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--symbols", type=int, default=10)
    parser.add_argument("--warmup-sessions", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS)
    parser.add_argument("--max-paths", type=int, default=10000)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    run_monte_carlo(
        hours=args.hours, strategy=args.strategy, initial_cash=args.initial_cash,
        seed=args.seed, symbols=args.symbols, warmup_sessions=args.warmup_sessions,
        evaluation_sessions=args.sessions, max_paths=args.max_paths,
        report_path=args.report, checkpoint_every=args.checkpoint_every,
    )


if __name__ == "__main__":
    main()
