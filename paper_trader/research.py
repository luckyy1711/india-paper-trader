"""Historical comparison for research only; it never changes the active strategy."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
from math import sqrt
import statistics
from typing import Any

from .config import (
    ACTIVE_STRATEGY,
    INITIAL_CASH,
    MIN_RESEARCH_BENCHMARK_BARS,
    MIN_RESEARCH_SYMBOL_COVERAGE,
    MIN_RESEARCH_SYMBOLS_WITH_260_BARS,
    MIN_RESEARCH_SYMBOLS_WITH_300_BARS,
    STRATEGIES,
)
from .core import benchmark_dates, execute_rebalance, mark_to_market, score_symbol, select_targets, new_state
from .symbols import NIFTY50_SYMBOLS


def _metrics(equities: list[float], trade_count: int, session_count: int) -> dict[str, Any]:
    if len(equities) < 2:
        return {"status": "insufficient_history", "trade_count": trade_count, "sessions": session_count}
    initial, final = equities[0], equities[-1]
    returns = [equities[i] / equities[i - 1] - 1.0 for i in range(1, len(equities)) if equities[i - 1] > 0]
    running_peak = initial
    max_drawdown = 0.0
    for value in equities:
        running_peak = max(running_peak, value)
        if running_peak > 0:
            max_drawdown = max(max_drawdown, (running_peak - value) / running_peak)
    volatility = statistics.pstdev(returns) * sqrt(252) if len(returns) > 1 else 0.0
    mean_daily = statistics.fmean(returns) if returns else 0.0
    daily_vol = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = mean_daily / daily_vol * sqrt(252) if daily_vol > 1e-12 else 0.0
    total_return = final / initial - 1.0 if initial > 0 else 0.0
    years = max(len(returns) / 252.0, 1.0 / 252.0)
    cagr = (final / initial) ** (1.0 / years) - 1.0 if final > 0 and initial > 0 else -1.0
    return {
        "status": "ok",
        "sessions": len(returns),
        "trade_count": trade_count,
        "start_equity": round(initial, 2),
        "end_equity": round(final, 2),
        "total_return_pct": round(total_return * 100.0, 3),
        "annualized_return_pct": round(cagr * 100.0, 3),
        "annualized_volatility_pct": round(volatility * 100.0, 3),
        "sharpe_approx": round(sharpe, 3),
        "max_drawdown_pct": round(max_drawdown * 100.0, 3),
    }


def _yearly_breakdown(
    equity_points: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    benchmark_by_date: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Summarize calendar-year close-to-close results from one continuous simulation."""
    indices_by_year: dict[str, list[int]] = {}
    for index in range(1, len(equity_points)):
        year = str(equity_points[index]["date"])[:4]
        indices_by_year.setdefault(year, []).append(index)

    yearly: list[dict[str, Any]] = []
    for year, indices in sorted(indices_by_year.items()):
        first_index, last_index = indices[0], indices[-1]
        start_point, end_point = equity_points[first_index - 1], equity_points[last_index]
        start_equity, end_equity = float(start_point["equity"]), float(end_point["equity"])
        strategy_return = end_equity / start_equity - 1.0 if start_equity > 0 else 0.0
        start_date, end_date = str(start_point["date"]), str(end_point["date"])

        benchmark_return = None
        try:
            benchmark_start = float(benchmark_by_date.get(start_date, {}).get("close"))
            benchmark_end = float(benchmark_by_date.get(end_date, {}).get("close"))
            if benchmark_start > 0 and benchmark_end > 0:
                benchmark_return = (benchmark_end / benchmark_start - 1.0) * 100.0
        except (TypeError, ValueError):
            benchmark_return = None

        period_trades = sum(
            1 for trade in trades
            if start_date < str(trade.get("date", "")) <= end_date
        )
        yearly.append({
            "year": year,
            "start_date": start_date,
            "end_date": end_date,
            "sessions": last_index - first_index + 1,
            "start_equity": round(start_equity, 2),
            "end_equity": round(end_equity, 2),
            "total_return_pct": round(strategy_return * 100.0, 3),
            "benchmark_total_return_pct": round(benchmark_return, 3) if benchmark_return is not None else None,
            "excess_return_vs_nifty_pct": round(strategy_return * 100.0 - benchmark_return, 3) if benchmark_return is not None else None,
            "trade_count": period_trades,
        })
    return yearly


def backtest_strategy(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    strategy_name: str,
    sample_days: int = 756,
) -> dict[str, Any]:
    """Walk forward over about 3 years: close-known signals fill at the next session open."""
    dates = benchmark_dates(bars_by_symbol)
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_name}")
    if len(dates) < 300:
        return {"status": "insufficient_history", "sessions_available": len(dates), "trade_count": 0}

    indexed: dict[str, tuple[list[str], dict[str, dict[str, Any]]]] = {}
    for symbol, rows in bars_by_symbol.items():
        if symbol == "__BENCHMARK__":
            continue
        rowmap = {str(row["date"]): row for row in rows}
        indexed[symbol] = (sorted(rowmap), rowmap)

    benchmark_rows = sorted(
        [row for row in bars_by_symbol.get("__BENCHMARK__", []) if row.get("date")],
        key=lambda row: str(row["date"]),
    )
    benchmark_dates_sorted = [str(row["date"]) for row in benchmark_rows]
    benchmark_by_date = {str(row["date"]): row for row in benchmark_rows}

    state = new_state()
    trades: list[dict[str, Any]] = []
    first_signal_index = max(260, len(dates) - max(20, sample_days) - 1)
    equity_points: list[dict[str, Any]] = [{"date": dates[first_signal_index], "equity": INITIAL_CASH}]
    signal_days_with_candidates = 0
    days_skipped_for_held_prices = 0
    targets_missing_next_open = 0
    target_count_total = 0

    for idx in range(first_signal_index, len(dates) - 1):
        signal_date, fill_date = dates[idx], dates[idx + 1]
        prefix_data: dict[str, list[dict[str, Any]]] = {}
        for symbol, (symbol_dates, rowmap) in indexed.items():
            end = bisect_right(symbol_dates, signal_date)
            prefix_data[symbol] = [rowmap[day] for day in symbol_dates[:end]]

        benchmark_end = bisect_right(benchmark_dates_sorted, signal_date)
        benchmark_prefix = benchmark_rows[:benchmark_end]
        targets = [] if state.get("risk_locked", False) else select_targets(
            prefix_data, strategy_name, benchmark_bars=benchmark_prefix
        )
        if targets:
            signal_days_with_candidates += 1
        opens: dict[str, float] = {}
        closes: dict[str, float] = {}
        for symbol, (_, rowmap) in indexed.items():
            row = rowmap.get(fill_date)
            if row is not None:
                try:
                    op, cl = float(row["open"]), float(row["close"])
                    if op > 0 and cl > 0:
                        opens[symbol], closes[symbol] = op, cl
                except (TypeError, ValueError, KeyError):
                    continue

        valid_targets = [symbol for symbol in targets if symbol in opens]
        targets_missing_next_open += len(targets) - len(valid_targets)
        if any(symbol not in opens for symbol in state["positions"]):
            days_skipped_for_held_prices += 1
            previous_equity = float(equity_points[-1]["equity"])
            for symbol in state["positions"]:
                if symbol not in closes:
                    prior_rows = indexed[symbol][1]
                    prior_days = [day for day in prior_rows if day < fill_date]
                    if prior_days:
                        closes[symbol] = float(prior_rows[prior_days[-1]]["close"])
            try:
                nav = mark_to_market(state, closes)["equity"]
            except (ValueError, KeyError):
                nav = previous_equity
            equity_points.append({"date": fill_date, "equity": nav})
            continue

        trades.extend(execute_rebalance(state, valid_targets, opens, fill_date, signal_date))
        nav = mark_to_market(state, closes)["equity"]
        equity_points.append({"date": fill_date, "equity": nav})
        target_count_total += len(valid_targets)

    equities = [float(point["equity"]) for point in equity_points]
    metrics = _metrics(equities, len(trades), len(equities) - 1)
    benchmark_return = None
    try:
        benchmark_start = float(benchmark_by_date.get(dates[first_signal_index], {}).get("close"))
        benchmark_end = float(benchmark_by_date.get(equity_points[-1]["date"], {}).get("close"))
        if benchmark_start > 0 and benchmark_end > 0:
            benchmark_return = (benchmark_end / benchmark_start - 1.0) * 100.0
    except (TypeError, ValueError):
        benchmark_return = None

    metrics["benchmark_total_return_pct"] = round(benchmark_return, 3) if benchmark_return is not None else None
    metrics["excess_return_vs_nifty_pct"] = (
        round(metrics["total_return_pct"] - benchmark_return, 3)
        if benchmark_return is not None and "total_return_pct" in metrics else None
    )
    metrics.update({
        "strategy": strategy_name,
        "sample_start": equity_points[0]["date"],
        "sample_end": equity_points[-1]["date"],
        "signal_days_with_candidates": signal_days_with_candidates,
        "candidate_symbols_available_at_next_open": target_count_total,
        "target_symbols_missing_next_open": targets_missing_next_open,
        "days_skipped_missing_held_prices": days_skipped_for_held_prices,
        "yearly_results": _yearly_breakdown(equity_points, trades, benchmark_by_date),
        "methodology": "Signals use data available through each close; target orders fill at next session open with configured costs/slippage.",
    })
    return metrics


def build_research_report(bars_by_symbol: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Build a multi-year comparison and withhold results when input coverage is inadequate."""
    dates = benchmark_dates(bars_by_symbol)
    stock_rows = {symbol: rows for symbol, rows in bars_by_symbol.items() if symbol != "__BENCHMARK__"}
    row_counts = {symbol: len(rows) for symbol, rows in stock_rows.items()}
    bars_260_count = sum(count >= 260 for count in row_counts.values())
    bars_300_count = sum(count >= 300 for count in row_counts.values())
    coverage = {
        "stock_symbols_downloaded": len(stock_rows),
        "stock_symbols_with_260_or_more_bars": bars_260_count,
        "stock_symbols_with_300_or_more_bars": bars_300_count,
        "stock_symbols_with_200_or_more_bars": sum(count >= 200 for count in row_counts.values()),
        "min_stock_bars": min(row_counts.values()) if row_counts else 0,
        "max_stock_bars": max(row_counts.values()) if row_counts else 0,
        "symbols_scored_on_latest_common_signal_date": 0,
        "latest_common_signal_date": dates[-2] if len(dates) >= 2 else None,
    }

    if coverage["latest_common_signal_date"]:
        signal_date = coverage["latest_common_signal_date"]
        for _symbol, rows in stock_rows.items():
            history = [row for row in rows if str(row.get("date", "")) <= signal_date]
            if any(score_symbol(history, name) is not None for name in STRATEGIES):
                coverage["symbols_scored_on_latest_common_signal_date"] += 1

    failed_checks = []
    if len(stock_rows) < MIN_RESEARCH_SYMBOL_COVERAGE:
        failed_checks.append(
            f"only {len(stock_rows)} of {len(NIFTY50_SYMBOLS)} stock histories downloaded "
            f"(at least {MIN_RESEARCH_SYMBOL_COVERAGE} required)"
        )
    if bars_300_count < MIN_RESEARCH_SYMBOLS_WITH_300_BARS:
        failed_checks.append(
            f"only {bars_300_count} stocks have at least 300 daily bars "
            f"(at least {MIN_RESEARCH_SYMBOLS_WITH_300_BARS} required)"
        )
    if len(dates) < MIN_RESEARCH_BENCHMARK_BARS:
        failed_checks.append(
            f"benchmark has only {len(dates)} daily bars (at least {MIN_RESEARCH_BENCHMARK_BARS} required)"
        )

    quality_status = "blocked_insufficient_market_data" if failed_checks else "ok"
    if failed_checks:
        quality_message = "; ".join(failed_checks) + ". Backtest metrics are withheld until the data-quality gate passes."
        results = [
            {"strategy": name, "status": "blocked_insufficient_market_data", "trade_count": 0, "message": quality_message}
            for name in STRATEGIES
        ]
    else:
        results = []
        for name in STRATEGIES:
            try:
                results.append(backtest_strategy(bars_by_symbol, name))
            except Exception as exc:
                results.append({"strategy": name, "status": "error", "error": type(exc).__name__})
        successful = [item for item in results if item.get("status") == "ok"]
        successful.sort(
            key=lambda item: (item.get("excess_return_vs_nifty_pct", -999), item.get("sharpe_approx", 0)),
            reverse=True,
        )
        results = successful + [item for item in results if item.get("status") != "ok"]
        quality_message = (
            f"Data-quality gate passed: {len(stock_rows)} stock histories downloaded, "
            f"{bars_300_count} with at least 300 daily bars, and {len(dates)} benchmark bars. "
            "The report evaluates up to 756 recent sessions (about 3 years), with calendar-year checks. "
            "Coverage checks do not guarantee accuracy or predict future performance."
        )

    return {
        "status": quality_status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": "Nifty 50 index price series from Yahoo Finance; not a total-return benchmark",
        "data_source": "Yahoo Finance via yfinance; research-only and may be delayed, incomplete, or unavailable",
        "sample_sessions_available": len(dates),
        "research_methodology": {
            "evaluation_window_target_sessions": 756,
            "warmup_sessions": 260,
            "fill_timing": "Signals calculated after the close; target orders fill at next session open.",
            "cost_model": "Configured transaction costs and adverse slippage are included in fills.",
            "benchmark": "Each strategy is compared against Nifty 50 price-index close-to-close return over the same sample.",
            "yearly_breakdown": "Calendar-year sub-periods from the same continuous simulation; partial first/last years are possible.",
            "candidate_policy": "Candidates are research-only and never automatically replace the active strategy.",
            "limitations": [
                "Current index constituents introduce survivorship bias.",
                "Yahoo Finance data is unofficial and may be revised or incomplete.",
                "A three-year window and one set of historical constituents cannot establish future profitability.",
                "Trying many variants can overfit; judge robustness and later paper-trading, not the best single return.",
            ],
        },
        "market_data_coverage": coverage,
        "data_quality": {
            "status": quality_status,
            "minimum_stock_symbols_required": MIN_RESEARCH_SYMBOL_COVERAGE,
            "minimum_symbols_with_260_or_more_bars_required": MIN_RESEARCH_SYMBOLS_WITH_260_BARS,
            "minimum_symbols_with_300_or_more_bars_required": MIN_RESEARCH_SYMBOLS_WITH_300_BARS,
            "minimum_benchmark_bars_required": MIN_RESEARCH_BENCHMARK_BARS,
            "failed_checks": failed_checks,
            "message": quality_message,
        },
        "active_strategy": ACTIVE_STRATEGY,
        "active_strategy_changes_automatically": False,
        "selection_note": (
            "Research includes the existing momentum rules, classic 12-1 relative strength, 6/12-month dual momentum, "
            "and a Nifty 200-day regime filter. Research never auto-promotes candidates. Current constituents create "
            "survivorship bias; metrics are not forecasts. Paper-trial candidates separately before considering promotion."
        ),
        "results": results,
    }
