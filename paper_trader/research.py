"""Historical comparison for research only; it never changes the active strategy."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
from math import sqrt
import statistics
from typing import Any

from .config import ACTIVE_STRATEGY, INITIAL_CASH, STRATEGIES
from .core import benchmark_dates, execute_rebalance, mark_to_market, score_symbol, select_targets, new_state


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


def backtest_strategy(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    strategy_name: str,
    sample_days: int = 252,
) -> dict[str, Any]:
    """Walk forward: close-derived signals fill at the next session open."""
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

    state = new_state()
    trades: list[dict[str, Any]] = []
    equities = [INITIAL_CASH]
    start_index = max(260, len(dates) - max(20, sample_days) - 1)
    first_signal_index = start_index
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

        targets = [] if state.get("risk_locked", False) else select_targets(prefix_data, strategy_name)
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

        # A missing bar for a NEW target should not block all other valid targets.
        valid_targets = [symbol for symbol in targets if symbol in opens]
        targets_missing_next_open += len(targets) - len(valid_targets)
        if any(symbol not in opens for symbol in state["positions"]):
            # Never fabricate a held-position fill or valuation. Skip the day.
            days_skipped_for_held_prices += 1
            previous_equity = equities[-1]
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
            equities.append(nav)
            continue

        # Existing positions can be held if their next-open bar exists; missing new candidates
        # are simply excluded from this rebalance rather than freezing the entire simulation.
        day_trades = execute_rebalance(state, valid_targets, opens, fill_date, signal_date)
        trades.extend(day_trades)
        nav = mark_to_market(state, closes)["equity"]
        equities.append(nav)
        target_count_total += len(valid_targets)

    metrics = _metrics(equities, len(trades), len(equities) - 1)
    metrics.update({
        "strategy": strategy_name,
        "sample_start": dates[first_signal_index] if first_signal_index < len(dates) else None,
        "sample_end": dates[-1],
        "signal_days_with_candidates": signal_days_with_candidates,
        "candidate_symbols_available_at_next_open": target_count_total,
        "target_symbols_missing_next_open": targets_missing_next_open,
        "days_skipped_missing_held_prices": days_skipped_for_held_prices,
    })
    return metrics


def build_research_report(bars_by_symbol: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    dates = benchmark_dates(bars_by_symbol)
    results = []
    for strategy_name in STRATEGIES:
        try:
            results.append(backtest_strategy(bars_by_symbol, strategy_name))
        except Exception as exc:
            results.append({"strategy": strategy_name, "status": "error", "error": type(exc).__name__})
    successful = [item for item in results if item.get("status") == "ok"]
    successful.sort(key=lambda item: (item.get("sharpe_approx", 0), item.get("total_return_pct", 0)), reverse=True)
    other_results = [item for item in results if item.get("status") != "ok"]
    stock_rows = {symbol: rows for symbol, rows in bars_by_symbol.items() if symbol != "__BENCHMARK__"}
    row_counts = {symbol: len(rows) for symbol, rows in stock_rows.items()}
    coverage = {
        "stock_symbols_downloaded": len(stock_rows),
        "stock_symbols_with_260_or_more_bars": sum(count >= 260 for count in row_counts.values()),
        "stock_symbols_with_200_or_more_bars": sum(count >= 200 for count in row_counts.values()),
        "min_stock_bars": min(row_counts.values()) if row_counts else 0,
        "max_stock_bars": max(row_counts.values()) if row_counts else 0,
        "symbols_scored_on_latest_common_signal_date": 0,
        "latest_common_signal_date": dates[-2] if len(dates) >= 2 else None,
    }
    if coverage["latest_common_signal_date"]:
        signal_date = coverage["latest_common_signal_date"]
        for symbol, rows in stock_rows.items():
            history = [row for row in rows if str(row.get("date", "")) <= signal_date]
            if any(score_symbol(history, strategy_name) is not None for strategy_name in STRATEGIES):
                coverage["symbols_scored_on_latest_common_signal_date"] += 1

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": "Nifty 50 index price series from Yahoo Finance; not a total-return benchmark",
        "data_source": "Yahoo Finance via yfinance; research-only and may be delayed, incomplete, or unavailable",
        "sample_sessions_available": len(dates),
        "market_data_coverage": coverage,
        "active_strategy": ACTIVE_STRATEGY,
        "active_strategy_changes_automatically": False,
        "selection_note": "Recent walk-forward research only, with simplified costs/slippage. Current constituents create survivorship bias. Candidate strategies never auto-promote; paper-trial them separately. Diagnostic counters are included to help explain any zero-trade result.",
        "results": successful + other_results,
    }
