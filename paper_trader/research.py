"""Historical comparison for research only; it never changes the active strategy."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
from math import isfinite, sqrt
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
from .ml_research import MODEL_NAME as OPEN_SOURCE_ML_MODEL_NAME, backtest_lightgbm_research


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


RISK_POLICY_PERMANENT = "permanent_lock"
RISK_POLICY_NIFTY_REENTRY = "nifty_200_sma_reentry"
RISK_POLICIES = {RISK_POLICY_PERMANENT, RISK_POLICY_NIFTY_REENTRY}


def _benchmark_sma_position(rows: list[dict[str, Any]], window: int = 200) -> int | None:
    """Return 1 above, -1 below, 0 equal, or None without enough valid history."""
    if window < 1:
        raise ValueError("window must be positive")
    closes: list[float] = []
    for row in rows:
        try:
            close = float(row.get("close", 0))
            if close > 0 and isfinite(close):
                closes.append(close)
        except (TypeError, ValueError):
            continue
    if len(closes) < window:
        return None
    average = statistics.fmean(closes[-window:])
    if closes[-1] > average:
        return 1
    if closes[-1] < average:
        return -1
    return 0


def _benchmark_above_sma(rows: list[dict[str, Any]], window: int = 200) -> bool:
    """Use only benchmark closes supplied through the signal date."""
    return _benchmark_sma_position(rows, window) == 1


def _benchmark_below_sma(rows: list[dict[str, Any]], window: int = 200) -> bool:
    """Require a genuine close below the average, not just an equal close."""
    return _benchmark_sma_position(rows, window) == -1


def backtest_strategy(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    strategy_name: str,
    sample_days: int = 756,
    cost_multiplier: float = 1.0,
    risk_policy: str = RISK_POLICY_PERMANENT,
) -> dict[str, Any]:
    """Backtest a fixed strategy and optional research-only drawdown recovery policy."""
    dates = benchmark_dates(bars_by_symbol)
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_name}")
    if risk_policy not in RISK_POLICIES:
        raise ValueError(f"Unknown risk policy: {risk_policy}")
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
    risk_lock_date: str | None = None
    active_lock_date: str | None = None
    benchmark_was_below_sma_since_lock = False
    risk_lock_events = 0
    risk_reentry_dates: list[str] = []
    sessions_with_positions = 0
    post_lock_flat_sessions = 0

    def record_session(day: str) -> None:
        nonlocal risk_lock_date, active_lock_date, risk_lock_events
        nonlocal benchmark_was_below_sma_since_lock, sessions_with_positions, post_lock_flat_sessions
        if state.get("risk_locked", False):
            if active_lock_date is None:
                active_lock_date = day
                benchmark_was_below_sma_since_lock = False
                risk_lock_events += 1
                if risk_lock_date is None:
                    risk_lock_date = day
        else:
            active_lock_date = None
            benchmark_was_below_sma_since_lock = False
        if state["positions"]:
            sessions_with_positions += 1
        elif state.get("risk_locked", False) and active_lock_date is not None and day > active_lock_date:
            post_lock_flat_sessions += 1

    for idx in range(first_signal_index, len(dates) - 1):
        signal_date, fill_date = dates[idx], dates[idx + 1]
        prefix_data: dict[str, list[dict[str, Any]]] = {}
        for symbol, (symbol_dates, rowmap) in indexed.items():
            end = bisect_right(symbol_dates, signal_date)
            prefix_data[symbol] = [rowmap[day] for day in symbol_dates[:end]]

        benchmark_end = bisect_right(benchmark_dates_sorted, signal_date)
        benchmark_prefix = benchmark_rows[:benchmark_end]

        # This recovery policy exists only inside historical research. It does not modify
        # the production paper-trader state machine or its permanent drawdown lock.
        if state.get("risk_locked", False):
            targets = []
            market_above_sma = _benchmark_above_sma(benchmark_prefix, 200)
            if (
                risk_policy == RISK_POLICY_NIFTY_REENTRY
                and active_lock_date is not None
                and signal_date >= active_lock_date
                and _benchmark_below_sma(benchmark_prefix, 200)
            ):
                benchmark_was_below_sma_since_lock = True
            can_reenter = (
                risk_policy == RISK_POLICY_NIFTY_REENTRY
                and active_lock_date is not None
                and signal_date > active_lock_date
                and benchmark_was_below_sma_since_lock
                and not state["positions"]
                and market_above_sma
            )
            if can_reenter:
                recovery_targets = select_targets(
                    prefix_data, strategy_name, benchmark_bars=benchmark_prefix
                )
                # Require an eligible target with a usable next-open fill before unlocking.
                executable_target_exists = False
                for symbol in recovery_targets:
                    row = indexed.get(symbol, ([], {}))[1].get(fill_date)
                    if row is None:
                        continue
                    try:
                        opening = float(row.get("open", 0))
                    except (TypeError, ValueError):
                        continue
                    if opening > 0 and isfinite(opening):
                        executable_target_exists = True
                        break
                if executable_target_exists:
                    state["risk_locked"] = False
                    # Start a fresh drawdown measurement from the simulated re-entry equity.
                    state["peak_equity"] = round(float(equity_points[-1]["equity"]), 2)
                    active_lock_date = None
                    benchmark_was_below_sma_since_lock = False
                    risk_reentry_dates.append(signal_date)
                    targets = recovery_targets
        else:
            targets = select_targets(
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
            record_session(fill_date)
            continue

        # Research-only sizing: higher risk-adjusted momentum earns more capital,
        # but no stock can exceed the existing 25% cap. This is not a probability forecast.
        target_weights = None
        if strategy_name == "nifty_regime_momentum_126":
            scores = {symbol: score_symbol(prefix_data[symbol], strategy_name) for symbol in valid_targets}
            valid_scores = [float(v) for v in scores.values() if v is not None and isfinite(float(v)) and float(v) > 0]
            if valid_scores:
                best = max(valid_scores)
                target_weights = {
                    symbol: (0.075 if scores[symbol] is None else
                             0.075 if float(scores[symbol]) / best < 0.40 else
                             0.15 if float(scores[symbol]) / best < 0.75 else 0.50)
                    for symbol in valid_targets
                }
        trades.extend(execute_rebalance(state, valid_targets, opens, fill_date, signal_date, cost_multiplier=cost_multiplier, target_weights=target_weights, research_position_cap=0.50 if target_weights is not None else None))
        nav = mark_to_market(state, closes)["equity"]
        equity_points.append({"date": fill_date, "equity": nav})
        record_session(fill_date)
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
        "risk_policy": risk_policy,
        "sample_start": equity_points[0]["date"],
        "sample_end": equity_points[-1]["date"],
        "signal_days_with_candidates": signal_days_with_candidates,
        "candidate_symbols_available_at_next_open": target_count_total,
        "target_symbols_missing_next_open": targets_missing_next_open,
        "days_skipped_missing_held_prices": days_skipped_for_held_prices,
        "sessions_with_positions": sessions_with_positions,
        "risk_lock_triggered": risk_lock_date is not None,
        "risk_lock_date": risk_lock_date,
        "risk_lock_events": risk_lock_events,
        "risk_reentry_dates": risk_reentry_dates,
        "risk_reentry_count": len(risk_reentry_dates),
        "risk_lock_active_at_end": bool(state.get("risk_locked", False)),
        "post_lock_flat_sessions": post_lock_flat_sessions,
        "yearly_results": _yearly_breakdown(equity_points, trades, benchmark_by_date),
        "replay_equity_points": equity_points,
        "replay_trades": trades,
        "methodology": (
            "Signals use data available through each close; target orders fill at next session open with configured costs/slippage. "
            + (
                "The normal permanent 10% drawdown lock remains in force; after it triggers, the simulation stays in cash."
                if risk_policy == RISK_POLICY_PERMANENT
                else (
                    "Research-only recovery overlay: after a 10% drawdown lock, wait for Nifty to close below its "
                    "200-session SMA and then later close back above it, with an eligible stock target and usable "
                    "next-open price. The simulation resumes at the next open and resets the drawdown reference peak "
                    "to re-entry equity. "
                    "This policy is not used by the scheduled paper trader."
                )
            )
        ),
    })
    return metrics



def _walk_forward_checks(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    strategy_name: str,
    fold_sessions: int = 126,
    max_folds: int = 4,
    risk_policy: str = RISK_POLICY_PERMANENT,
) -> dict[str, Any]:
    """Evaluate a fixed rule on consecutive, non-overlapping recent half-year test folds."""
    dates = benchmark_dates(bars_by_symbol)
    if fold_sessions < 20 or max_folds < 2:
        raise ValueError("Walk-forward checks require at least 20 sessions per fold and two folds")
    if len(dates) < 260 + fold_sessions * max_folds:
        return {
            "status": "insufficient_history",
            "fold_sessions": fold_sessions,
            "requested_folds": max_folds,
            "completed_folds": 0,
            "folds": [],
        }

    folds: list[dict[str, Any]] = []
    for fold_number in range(max_folds):
        end_index = len(dates) - 1 - (max_folds - 1 - fold_number) * fold_sessions
        end_date = dates[end_index]
        prefix = {
            symbol: [row for row in rows if str(row.get("date", "")) <= end_date]
            for symbol, rows in bars_by_symbol.items()
        }
        candidate = backtest_strategy(
            prefix, strategy_name, sample_days=fold_sessions, risk_policy=risk_policy
        )
        keys = (
            "status", "sample_start", "sample_end", "sessions", "total_return_pct",
            "annualized_return_pct", "sharpe_approx", "max_drawdown_pct",
            "benchmark_total_return_pct", "excess_return_vs_nifty_pct", "trade_count",
            "risk_policy", "risk_lock_triggered", "risk_lock_date", "risk_lock_events",
            "risk_reentry_count", "risk_reentry_dates", "risk_lock_active_at_end",
        )
        folds.append({
            "fold": fold_number + 1,
            "test_end": end_date,
            **{key: candidate.get(key) for key in keys if key in candidate},
        })

    valid = [fold for fold in folds if fold.get("status") == "ok"]
    return {
        "status": "ok" if len(valid) == max_folds else "partial",
        "fold_sessions": fold_sessions,
        "requested_folds": max_folds,
        "completed_folds": len(valid),
        "positive_return_folds": sum(float(fold.get("total_return_pct", 0)) > 0 for fold in valid),
        "folds_beating_nifty": sum(
            fold.get("excess_return_vs_nifty_pct") is not None
            and float(fold["excess_return_vs_nifty_pct"]) > 0 for fold in valid
        ),
        "folds": folds,
        "risk_policy": risk_policy,
        "interpretation": (
            "Chronological fixed-rule validation on consecutive, non-overlapping test periods. "
            "The strategy is not re-tuned between folds; each fold starts with a fresh simulated portfolio, "
            "so this is not a single deployable compounded equity curve and does not prove future profitability."
        ),
    }


def _compact_risk_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "status", "risk_policy", "sample_start", "sample_end", "sessions", "total_return_pct",
        "annualized_return_pct", "sharpe_approx", "max_drawdown_pct",
        "benchmark_total_return_pct", "excess_return_vs_nifty_pct", "trade_count",
        "risk_lock_triggered", "risk_lock_date", "risk_lock_events",
        "risk_reentry_count", "risk_reentry_dates", "risk_lock_active_at_end",
    )
    return {key: candidate.get(key) for key in keys if key in candidate}


def _risk_recovery_comparison(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    baseline_result: dict[str, Any],
) -> dict[str, Any]:
    """Compare a permanent lock with a research-only Nifty-200-SMA recovery overlay."""
    recovery_full = backtest_strategy(
        bars_by_symbol,
        ACTIVE_STRATEGY,
        sample_days=756,
        risk_policy=RISK_POLICY_NIFTY_REENTRY,
    )
    recovery_folds = _walk_forward_checks(
        bars_by_symbol,
        ACTIVE_STRATEGY,
        fold_sessions=126,
        max_folds=4,
        risk_policy=RISK_POLICY_NIFTY_REENTRY,
    )
    permanent_folds = baseline_result.get("walk_forward") or _walk_forward_checks(
        bars_by_symbol,
        ACTIVE_STRATEGY,
        fold_sessions=126,
        max_folds=4,
        risk_policy=RISK_POLICY_PERMANENT,
    )
    status = "ok" if recovery_full.get("status") == "ok" and recovery_folds.get("status") == "ok" else "partial"
    return {
        "status": status,
        "strategy": ACTIVE_STRATEGY,
        "policies": [
            {
                "policy": RISK_POLICY_PERMANENT,
                "label": "Permanent 10% drawdown lock",
                "full_sample": _compact_risk_metrics(baseline_result),
                "walk_forward": permanent_folds,
            },
            {
                "policy": RISK_POLICY_NIFTY_REENTRY,
                "label": "Resume after Nifty closes above its 200-day average",
                "full_sample": _compact_risk_metrics(recovery_full),
                "walk_forward": recovery_folds,
            },
        ],
        "methodology": (
            "Both policies use the same active strategy and market data. The recovery overlay remains in cash after "
            "a drawdown lock until Nifty closes below its 200-session SMA and then later closes back above it, with "
            "at least one eligible target and a valid next-open price. Re-entry happens at the next open; the risk "
            "reference peak resets to re-entry equity. The scheduled paper trader and its permanent lock are not changed."
        ),
        "limitations": [
            "The four 126-session fold portfolios restart independently; they are not one compounded equity curve.",
            "A small number of historical windows does not establish future profitability.",
            "Nifty is a price-index benchmark, the constituent universe has survivorship bias, and market-data quality can vary.",
            "This is a research-only policy comparison, not permission to place real-money orders.",
        ],
        "applies_to_scheduled_paper_trader": False,
        "active_strategy_changes_automatically": False,
    }


def _compact_open_source_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "status", "sample_start", "sample_end", "sessions", "evaluation_sessions",
        "total_return_pct", "annualized_return_pct", "sharpe_approx", "max_drawdown_pct",
        "benchmark_total_return_pct", "excess_return_vs_nifty_pct", "trade_count",
        "model_refresh_count", "training_sample_count_last_fit",
    )
    return {key: candidate.get(key) for key in keys if key in candidate}


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
        # Keep rules fixed; test evaluation-window and trading-cost sensitivity rather than fishing for parameters.
        for item in results:
            if item.get("status") != "ok":
                continue
            strategy_name = item["strategy"]

            def compact_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
                keys = (
                    "status", "sample_start", "sample_end", "sessions", "total_return_pct",
                    "annualized_return_pct", "sharpe_approx", "max_drawdown_pct",
                    "benchmark_total_return_pct", "excess_return_vs_nifty_pct", "trade_count",
                )
                return {key: candidate.get(key) for key in keys if key in candidate}

            period_checks = {}
            for days in (252, 504):
                candidate = backtest_strategy(bars_by_symbol, strategy_name, sample_days=days)
                period_checks[str(days)] = compact_metrics(candidate)

            cost_checks = {}
            for multiplier in (1.5, 2.0):
                candidate = backtest_strategy(
                    bars_by_symbol, strategy_name, sample_days=756, cost_multiplier=multiplier
                )
                cost_checks[str(multiplier)] = compact_metrics(candidate)

            all_periods = {"756": compact_metrics(item), **period_checks}
            all_costs = {"1.0": compact_metrics(item), **cost_checks}
            valid_periods = [
                result for result in all_periods.values()
                if result.get("status") == "ok" and isinstance(result.get("total_return_pct"), (int, float))
            ]
            valid_costs = [
                result for result in all_costs.values()
                if result.get("status") == "ok" and isinstance(result.get("total_return_pct"), (int, float))
            ]
            item["robustness"] = {
                "period_sensitivity": all_periods,
                "cost_sensitivity": all_costs,
                "positive_return_periods": sum(result["total_return_pct"] > 0 for result in valid_periods),
                "periods_with_excess_return_over_nifty": sum(
                    result.get("excess_return_vs_nifty_pct") is not None
                    and result["excess_return_vs_nifty_pct"] > 0 for result in valid_periods
                ),
                "positive_return_cost_scenarios": sum(result["total_return_pct"] > 0 for result in valid_costs),
                "cost_scenarios_tested": len(valid_costs),
                "interpretation": (
                    "Sensitivity checks only, not independent out-of-sample proof. Periods overlap, "
                    "the universe has survivorship bias, and the benchmark excludes dividends."
                ),
            }
            item["walk_forward"] = _walk_forward_checks(
                bars_by_symbol, strategy_name, fold_sessions=126, max_folds=4
            )

        successful = [item for item in results if item.get("status") == "ok"]
        # Preserve baseline ordering; robustness is a diagnostic, not a new automatic ranking rule.
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

    # Open-source ML candidate is isolated from the six rule-based strategies. It never
    # changes ACTIVE_STRATEGY; all model training is chronological and paper-only.
    open_source_models: list[dict[str, Any]] = []
    if quality_status != "ok":
        open_source_models = [{
            "strategy": OPEN_SOURCE_ML_MODEL_NAME,
            "status": "blocked_insufficient_market_data",
            "model_family": "LightGBM gradient-boosted decision trees",
            "active_strategy": False,
            "auto_promoted": False,
            "trade_count": 0,
            "message": quality_message,
        }]
    else:
        try:
            ml_base = backtest_lightgbm_research(bars_by_symbol, cost_multiplier=1.0)
            if ml_base.get("status") == "ok":
                # Compare the ML candidate and active rule over exactly the same evaluation dates.
                # The rule backtest uses the same fill/cost engine but its own rule-based signals.
                ml_sessions = int(ml_base.get("sessions", 0))
                aligned_rule = backtest_strategy(
                    bars_by_symbol, ACTIVE_STRATEGY, sample_days=max(20, ml_sessions)
                )
                comparison_keys = (
                    "status", "sample_start", "sample_end", "sessions", "total_return_pct",
                    "annualized_return_pct", "sharpe_approx", "max_drawdown_pct",
                    "benchmark_total_return_pct", "excess_return_vs_nifty_pct", "trade_count",
                    "risk_lock_triggered", "risk_lock_date", "risk_lock_active_at_end",
                )
                if (
                    aligned_rule.get("status") == "ok"
                    and aligned_rule.get("sample_start") == ml_base.get("sample_start")
                    and aligned_rule.get("sample_end") == ml_base.get("sample_end")
                ):
                    ml_base["same_period_rule_baseline"] = {
                        key: aligned_rule[key] for key in comparison_keys if key in aligned_rule
                    }
                    ml_base["same_period_comparison"] = {
                        "status": "ok",
                        "sample_start": ml_base.get("sample_start"),
                        "sample_end": ml_base.get("sample_end"),
                        "sessions": ml_base.get("sessions"),
                        "model_total_return_pct": ml_base.get("total_return_pct"),
                        "rule_total_return_pct": aligned_rule.get("total_return_pct"),
                        "benchmark_total_return_pct": ml_base.get("benchmark_total_return_pct"),
                        "model_excess_return_vs_nifty_pct": ml_base.get("excess_return_vs_nifty_pct"),
                        "rule_excess_return_vs_nifty_pct": aligned_rule.get("excess_return_vs_nifty_pct"),
                        "model_minus_rule_return_pp": round(
                            float(ml_base.get("total_return_pct", 0))
                            - float(aligned_rule.get("total_return_pct", 0)), 3
                        ),
                        "risk_policy": "Both simulations use the existing permanent 10% drawdown lock.",
                        "interpretation": (
                            "Aligned-date comparison using the same initial virtual cash, next-session-open "
                            "fills, and base transaction-cost/slippage assumptions. This is historical research, "
                            "not evidence of future profitability."
                        ),
                    }
                else:
                    ml_base["same_period_comparison"] = {
                        "status": "date_alignment_failed",
                        "message": "The model and rule baseline did not produce identical evaluation dates; no direct return comparison is reported.",
                    }
                sensitivity: dict[str, dict[str, Any]] = {"1.0": _compact_open_source_metrics(ml_base)}
                for multiplier in (1.5, 2.0):
                    stressed = backtest_lightgbm_research(
                        bars_by_symbol, cost_multiplier=multiplier
                    )
                    sensitivity[str(multiplier)] = _compact_open_source_metrics(stressed)
                valid_costs = [
                    value for value in sensitivity.values()
                    if value.get("status") == "ok"
                    and isinstance(value.get("total_return_pct"), (int, float))
                ]
                ml_base["robustness"] = {
                    "cost_sensitivity": sensitivity,
                    "cost_scenarios_tested": len(valid_costs),
                    "positive_return_cost_scenarios": sum(
                        float(value["total_return_pct"]) > 0 for value in valid_costs
                    ),
                    "interpretation": (
                        "The same fixed LightGBM training and signal schedule is re-simulated with "
                        "1.0x, 1.5x, and 2.0x transaction-cost/slippage assumptions. These are cost "
                        "sensitivity checks, not independent validation."
                    ),
                }
            open_source_models = [ml_base]
        except Exception as exc:
            open_source_models = [{
                "strategy": OPEN_SOURCE_ML_MODEL_NAME,
                "status": "error",
                "model_family": "LightGBM gradient-boosted decision trees",
                "active_strategy": False,
                "auto_promoted": False,
                "trade_count": 0,
                "error_type": type(exc).__name__,
                "message": "The open-source model research run failed; rule-based results remain separate.",
            }]

    risk_recovery_comparison: dict[str, Any] = {
        "status": "blocked_insufficient_market_data",
        "message": "Risk-recovery comparison is withheld because the research data-quality gate did not pass.",
        "policies": [],
        "applies_to_scheduled_paper_trader": False,
    }
    if quality_status == "ok":
        active_result = next(
            (item for item in results if item.get("strategy") == ACTIVE_STRATEGY and item.get("status") == "ok"),
            None,
        )
        if active_result is not None:
            risk_recovery_comparison = _risk_recovery_comparison(bars_by_symbol, active_result)

    return {
        "status": quality_status,
        "risk_recovery_comparison": risk_recovery_comparison,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": "Nifty 50 index price series from Yahoo Finance; not a total-return benchmark",
        "data_source": "Yahoo Finance via yfinance; research-only and may be delayed, incomplete, or unavailable",
        "sample_sessions_available": len(dates),
        "research_methodology": {
            "evaluation_window_target_sessions": 756,
            "warmup_sessions": 260,
            "fill_timing": "Signals calculated after the close; target orders fill at next session open.",
            "cost_model": "Configured transaction costs and adverse slippage are included; each candidate also receives 1.5x and 2.0x cost stress checks.",
            "benchmark": "Each strategy is compared against Nifty 50 price-index close-to-close return over the same sample.",
            "yearly_breakdown": "Calendar-year sub-periods from the same continuous simulation; partial first/last years are possible.",
            "robustness_checks": "Each fixed strategy is re-tested over 252-, 504-, and 756-session windows, plus 1.5x and 2.0x transaction-cost/slippage scenarios. These overlapping windows are sensitivity checks, not independent validation.",
            "walk_forward_checks": "Each strategy is tested on four consecutive 126-session chronological folds with no overlap between return intervals. Each fold uses a fresh simulated portfolio; results are not one compounded equity curve and do not prove future profitability.",
            "candidate_policy": "Candidates are research-only and never automatically replace the active strategy.",
            "open_source_model": "A separate LightGBM alpha-regression candidate follows an expanding-window process; a 21-session label is only used for training after the outcome window has ended. Cost sensitivity is tested at 1.0x, 1.5x, and 2.0x.",
            "limitations": [
                "Current index constituents introduce survivorship bias.",
                "Yahoo Finance data is unofficial and may be revised or incomplete.",
                "A three-year window and one set of historical constituents cannot establish future profitability.",
                "Trying many variants can overfit; judge robustness and later paper-trading, not the best single return.",
                "The cost stress multiplies both modeled transaction costs and slippage; it does not model market impact or liquidity constraints.",
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
        "open_source_models": open_source_models,
    }
