"""Open-source LightGBM research candidate for Indian equities.

The workflow borrows the tree-boosting research pattern documented by Microsoft Qlib
and uses the upstream LightGBM library. It is an independent research adapter, not
a drop-in Freqtrade/LEAN engine. It has no broker integration and never places orders.
"""

from __future__ import annotations

from math import isfinite, sqrt
import statistics
from typing import Any

from .config import (
    INITIAL_CASH,
    MAX_POSITIONS,
    MIN_RESEARCH_BENCHMARK_BARS,
    TRANSACTION_COST_RATE,
)
from .core import benchmark_dates, execute_rebalance, mark_to_market, new_state

MODEL_NAME = "qlib_lightgbm_alpha"
MODEL_FAMILY = "LightGBM gradient-boosted decision trees"
SOURCE_PROJECT = "https://github.com/microsoft/qlib"
LIGHTGBM_PROJECT = "https://github.com/lightgbm-org/LightGBM"
HORIZON_SESSIONS = 21
REBALANCE_EVERY_SESSIONS = 21
REFIT_EVERY_SESSIONS = 63
FEATURE_WARMUP_SESSIONS = 252
MIN_TRAINING_ROWS = 2_000
MIN_TRAINING_DATES = 50
MIN_TRAINING_SYMBOLS = 15
MAX_TRAINING_ROWS = 30_000
BOOSTING_ROUNDS = 55
MIN_EXPECTED_EXCESS_RETURN = max(0.006, 2.0 * (TRANSACTION_COST_RATE + SLIPPAGE_RATE))


def _positive_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) and number > 0 else default


def _period_return(values: list[float], end: int, sessions: int) -> float | None:
    start = end - sessions
    if start < 0 or end >= len(values) or values[start] <= 0:
        return None
    result = values[end] / values[start] - 1.0
    return result if isfinite(result) else None


def _mean_at(values: list[float], end: int, sessions: int) -> float | None:
    start = end - sessions + 1
    if start < 0 or end >= len(values):
        return None
    selected = values[start:end + 1]
    if len(selected) != sessions:
        return None
    result = statistics.fmean(selected)
    return result if isfinite(result) else None


def _annualized_volatility(values: list[float], end: int, sessions: int) -> float | None:
    start = end - sessions
    if start < 0 or end >= len(values):
        return None
    daily_returns = []
    for index in range(start + 1, end + 1):
        previous = values[index - 1]
        if previous > 0:
            value = values[index] / previous - 1.0
            if isfinite(value):
                daily_returns.append(value)
    if len(daily_returns) < max(10, sessions // 2):
        return None
    return statistics.pstdev(daily_returns) * sqrt(252)


def _rsi_ratio(values: list[float], end: int, sessions: int = 14) -> float | None:
    start = end - sessions
    if start < 0 or end >= len(values):
        return None
    gains = 0.0
    losses = 0.0
    for index in range(start + 1, end + 1):
        delta = values[index] - values[index - 1]
        if delta > 0:
            gains += delta
        elif delta < 0:
            losses -= delta
    total = gains + losses
    if total <= 1e-12:
        return 0.5
    return gains / total


def _feature_vector(
    row: dict[str, Any],
    closes: list[float],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    stock_index: int,
    benchmark_closes: list[float],
    benchmark_index: int,
) -> tuple[float, ...] | None:
    """Calculate a compact Qlib-inspired technical feature set through today's close only."""
    if stock_index < FEATURE_WARMUP_SESSIONS or benchmark_index < 200:
        return None

    returns = [
        _period_return(closes, stock_index, window)
        for window in (5, 21, 63, 126, 252)
    ]
    averages = [
        _mean_at(closes, stock_index, window)
        for window in (20, 50, 100, 200)
    ]
    volatilities = [
        _annualized_volatility(closes, stock_index, window)
        for window in (21, 63, 126)
    ]
    rsi = _rsi_ratio(closes, stock_index, 14)
    if any(value is None for value in returns + averages + volatilities) or rsi is None:
        return None

    relative_averages = [
        closes[stock_index] / average - 1.0 if average and average > 0 else None
        for average in averages
    ]
    if any(value is None for value in relative_averages):
        return None

    market_returns = [
        _period_return(benchmark_closes, benchmark_index, window)
        for window in (21, 63, 126)
    ]
    market_average = _mean_at(benchmark_closes, benchmark_index, 200)
    if any(value is None for value in market_returns) or not market_average or market_average <= 0:
        return None

    opened = opens[stock_index]
    high = highs[stock_index]
    low = lows[stock_index]
    close = closes[stock_index]
    intraday_return = close / opened - 1.0 if opened > 0 else 0.0
    intraday_range = max(0.0, high - low) / close if high > 0 and low > 0 and high >= low else 0.0

    recent_volumes = [value for value in volumes[max(0, stock_index - 19):stock_index + 1] if value > 0]
    avg_volume = statistics.fmean(recent_volumes) if recent_volumes else 0.0
    volume_relative = volumes[stock_index] / avg_volume - 1.0 if avg_volume > 0 else 0.0
    volume_relative = max(-5.0, min(20.0, volume_relative))

    features = (
        *returns,
        *relative_averages,
        *volatilities,
        rsi,
        intraday_return,
        intraday_range,
        volume_relative,
        *market_returns,
        benchmark_closes[benchmark_index] / market_average - 1.0,
    )
    if len(features) != 20 or not all(isfinite(value) for value in features):
        return None
    return tuple(float(value) for value in features)


def _prepare_research_rows(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> tuple[
    list[str],
    dict[int, list[tuple[str, tuple[float, ...]]]],
    list[tuple[int, int, str, tuple[float, ...], float]],
    dict[str, dict[str, Any]],
    list[float],
]:
    """Build point-in-time features and forward labels; labels are purged at training time."""
    benchmark_rows = sorted(
        [
            row for row in bars_by_symbol.get("__BENCHMARK__", [])
            if row.get("date") and _positive_number(row.get("close")) > 0
        ],
        key=lambda row: str(row["date"]),
    )
    benchmark_by_date = {str(row["date"]): row for row in benchmark_rows}
    dates = list(benchmark_by_date)
    benchmark_closes = [_positive_number(row.get("close")) for row in benchmark_rows]
    position_by_date = {day: index for index, day in enumerate(dates)}

    features_by_index: dict[int, list[tuple[str, tuple[float, ...]]]] = {}
    labelled_rows: list[tuple[int, int, str, tuple[float, ...], float]] = []

    for symbol, raw_rows in bars_by_symbol.items():
        if symbol == "__BENCHMARK__":
            continue
        by_date = {
            str(row["date"]): row
            for row in raw_rows
            if row.get("date") and _positive_number(row.get("close")) > 0
        }
        stock_rows = [by_date[day] for day in sorted(by_date)]
        if len(stock_rows) <= FEATURE_WARMUP_SESSIONS:
            continue

        stock_dates = [str(row["date"]) for row in stock_rows]
        closes = [_positive_number(row.get("close")) for row in stock_rows]
        opens = [_positive_number(row.get("open"), closes[i]) for i, row in enumerate(stock_rows)]
        highs = [_positive_number(row.get("high"), max(opens[i], closes[i])) for i, row in enumerate(stock_rows)]
        lows = [_positive_number(row.get("low"), min(opens[i], closes[i])) for i, row in enumerate(stock_rows)]
        volumes = []
        for row in stock_rows:
            try:
                volume = float(row.get("volume", 0) or 0)
            except (TypeError, ValueError):
                volume = 0.0
            volumes.append(volume if isfinite(volume) and volume > 0 else 0.0)

        for stock_index, row in enumerate(stock_rows):
            day = stock_dates[stock_index]
            benchmark_index = position_by_date.get(day)
            if benchmark_index is None:
                continue
            feature = _feature_vector(
                row, closes, opens, highs, lows, volumes, stock_index,
                benchmark_closes, benchmark_index,
            )
            if feature is None:
                continue
            features_by_index.setdefault(benchmark_index, []).append((symbol, feature))

            label_end_index = benchmark_index + HORIZON_SESSIONS
            if label_end_index >= len(dates):
                continue
            future_date = dates[label_end_index]
            future_row = by_date.get(future_date)
            current_benchmark = benchmark_closes[benchmark_index]
            future_benchmark = benchmark_closes[label_end_index]
            current_close = closes[stock_index]
            future_close = _positive_number(future_row.get("close")) if future_row else 0.0
            if min(current_benchmark, future_benchmark, current_close, future_close) <= 0:
                continue
            stock_return = future_close / current_close - 1.0
            market_return = future_benchmark / current_benchmark - 1.0
            # Clip rare extreme labels so data glitches cannot dominate a small model.
            target = max(-0.50, min(0.50, stock_return - market_return))
            if isfinite(target):
                labelled_rows.append((benchmark_index, label_end_index, symbol, feature, target))

    labelled_rows.sort(key=lambda item: (item[0], item[2]))
    for candidates in features_by_index.values():
        candidates.sort(key=lambda item: item[0])
    return dates, features_by_index, labelled_rows, benchmark_by_date, benchmark_closes


def _eligible_training_rows(
    samples: list[tuple[int, int, str, tuple[float, ...], float]],
    signal_index: int,
) -> list[tuple[int, int, str, tuple[float, ...], float]]:
    """Only expose training labels whose full forward horizon ends by the signal date."""
    return [sample for sample in samples if sample[1] <= signal_index]


def _walk_forward_periods(
    equity_points: list[dict[str, Any]],
    benchmark_by_date: dict[str, dict[str, Any]],
    fold_sessions: int = 126,
) -> dict[str, Any]:
    """Summarize consecutive, non-overlapping returns of the rolling out-of-sample simulation."""
    folds = []
    for start in range(0, len(equity_points) - 1, fold_sessions):
        end = min(start + fold_sessions, len(equity_points) - 1)
        start_point, end_point = equity_points[start], equity_points[end]
        start_equity = float(start_point["equity"])
        end_equity = float(end_point["equity"])
        strategy_return = end_equity / start_equity - 1.0 if start_equity > 0 else 0.0
        benchmark_start = _positive_number(benchmark_by_date.get(start_point["date"], {}).get("close"))
        benchmark_end = _positive_number(benchmark_by_date.get(end_point["date"], {}).get("close"))
        benchmark_return = benchmark_end / benchmark_start - 1.0 if benchmark_start and benchmark_end else None
        folds.append({
            "fold": len(folds) + 1,
            "start_date": start_point["date"],
            "end_date": end_point["date"],
            "sessions": end - start,
            "total_return_pct": round(strategy_return * 100.0, 3),
            "benchmark_total_return_pct": round(benchmark_return * 100.0, 3) if benchmark_return is not None else None,
            "excess_return_vs_nifty_pct": round((strategy_return - benchmark_return) * 100.0, 3) if benchmark_return is not None else None,
        })
    valid = [item for item in folds if item["sessions"] > 0]
    return {
        "status": "ok" if len(valid) >= 2 else "insufficient_history",
        "fold_sessions": fold_sessions,
        "requested_folds": len(folds),
        "completed_folds": len(valid),
        "positive_return_folds": sum(item["total_return_pct"] > 0 for item in valid),
        "folds_beating_nifty": sum(
            item["excess_return_vs_nifty_pct"] is not None and item["excess_return_vs_nifty_pct"] > 0
            for item in valid
        ),
        "folds": folds,
        "interpretation": (
            "Consecutive, non-overlapping periods from one expanding-window out-of-sample run. "
            "The LightGBM model is retrained using only labels whose 21-session outcome had ended "
            "by each signal date. Fold results are descriptive, not proof of future profitability."
        ),
    }


def backtest_lightgbm_research(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    cost_multiplier: float = 1.0,
    evaluation_sessions: int = 1_008,
) -> dict[str, Any]:
    """Backtest a LightGBM model without using future labels or changing the active strategy."""
    if not isfinite(cost_multiplier) or cost_multiplier < 1.0:
        raise ValueError("cost_multiplier must be finite and at least 1.0")
    dates, features_by_index, labelled_rows, benchmark_by_date, benchmark_closes = _prepare_research_rows(
        bars_by_symbol
    )
    base = {
        "strategy": MODEL_NAME,
        "model_family": MODEL_FAMILY,
        "source_project": SOURCE_PROJECT,
        "source_project_note": "Qlib's public LightGBM quantitative-research workflow was used as a design reference; Qlib itself is not installed or vendored.",
        "active_strategy": False,
        "auto_promoted": False,
        "cost_multiplier": cost_multiplier,
        "label_horizon_sessions": HORIZON_SESSIONS,
        "evaluation_window_target_sessions": evaluation_sessions,
        "raw_benchmark_sessions": len(dates),
        "training_sample_count_available": len(labelled_rows),
        "methodology": (
            "LightGBM regresses each stock's next-21-session return minus Nifty 50's return using compact "
            "rolling price/volume features. Features use data through the signal close; a training label is "
            "eligible only when its entire 21-session outcome ends on or before that signal date. The model "
            "is retrained on an expanding window every 63 sessions, targets are re-ranked every 21 sessions, "
            "and fills use the next session open. A 200-session Nifty trend filter and the same paper-trader "
            "transaction-cost/slippage model are applied. This is a research candidate, not production strategy."
        ),
        "limitations": [
            "Uses the current Nifty 50 constituent list, so survivorship bias remains.",
            "Yahoo adjusted OHLCV data is unofficial and may be incomplete or revised.",
            "The label predicts gross relative return; simulated transaction costs and adverse slippage are charged on fills.",
            "A rolling historical test can still overfit and cannot establish future profitability.",
            "This model is not copied wholesale from Freqtrade or Qlib; LightGBM is the open-source model library and Qlib is a cited workflow reference.",
        ],
    }
    if len(dates) < MIN_RESEARCH_BENCHMARK_BARS or len(dates) < FEATURE_WARMUP_SESSIONS + 21 + MIN_TRAINING_DATES:
        return {
            **base,
            "status": "insufficient_history",
            "message": "Not enough aligned benchmark sessions for the feature warm-up and chronological model training.",
            "trade_count": 0,
        }
    if len(labelled_rows) < MIN_TRAINING_ROWS:
        return {
            **base,
            "status": "insufficient_training_data",
            "message": f"Only {len(labelled_rows)} valid stock/date labels; at least {MIN_TRAINING_ROWS} are required.",
            "trade_count": 0,
        }

    try:
        import lightgbm as lgb
        import numpy as np
    except ImportError as exc:
        return {
            **base,
            "status": "dependency_missing",
            "message": "Install requirements.txt to enable the open-source LightGBM research candidate.",
            "error_type": type(exc).__name__,
            "trade_count": 0,
        }

    evaluation_start = max(FEATURE_WARMUP_SESSIONS + MIN_TRAINING_DATES, len(dates) - evaluation_sessions - 1)
    if evaluation_start >= len(dates) - 2:
        return {**base, "status": "insufficient_history", "trade_count": 0}

    training_samples: list[tuple[int, int, str, tuple[float, ...], float]] = labelled_rows
    state = new_state()
    trades: list[dict[str, Any]] = []
    equity_points: list[dict[str, Any]] = [{"date": dates[evaluation_start], "equity": INITIAL_CASH}]
    last_close: dict[str, float] = {}
    rowmaps: dict[str, dict[str, dict[str, Any]]] = {}
    for symbol, rows in bars_by_symbol.items():
        if symbol != "__BENCHMARK__":
            rowmaps[symbol] = {
                str(row["date"]): row
                for row in rows if row.get("date") and _positive_number(row.get("close")) > 0
            }
    for symbol, rows in rowmaps.items():
        row = rows.get(dates[evaluation_start])
        if row is not None:
            last_close[symbol] = _positive_number(row.get("close"))

    booster = None
    last_fit_index: int | None = None
    last_fit_sample_count = 0
    training_dates: list[str] = []
    last_fit_error: str | None = None
    risk_lock_date: str | None = None
    missing_fill_symbols = 0
    missing_mark_sessions = 0
    signal_count = 0

    def train_at(signal_index: int):
        nonlocal last_fit_sample_count, last_fit_error
        eligible = _eligible_training_rows(training_samples, signal_index)
        distinct_dates = {sample[0] for sample in eligible}
        distinct_symbols = {sample[2] for sample in eligible}
        last_fit_sample_count = len(eligible)
        if (
            len(eligible) < MIN_TRAINING_ROWS
            or len(distinct_dates) < MIN_TRAINING_DATES
            or len(distinct_symbols) < MIN_TRAINING_SYMBOLS
        ):
            last_fit_error = "insufficient_purged_training_history"
            return None
        eligible = eligible[-MAX_TRAINING_ROWS:]
        x_train = np.asarray([sample[3] for sample in eligible], dtype=np.float32)
        y_train = np.asarray([sample[4] for sample in eligible], dtype=np.float32)
        dataset = lgb.Dataset(x_train, label=y_train, free_raw_data=True)
        parameters = {
            "objective": "regression",
            "metric": "l2",
            "learning_rate": 0.035,
            "num_leaves": 7,
            "max_depth": 4,
            "min_data_in_leaf": 60,
            "feature_fraction": 0.9,
            "bagging_fraction": 0.9,
            "bagging_freq": 1,
            "lambda_l2": 3.0,
            "verbosity": -1,
            "num_threads": 1,
            "seed": 42,
            "feature_fraction_seed": 42,
            "bagging_seed": 42,
            "data_random_seed": 42,
            "deterministic": True,
            "force_col_wise": True,
        }
        try:
            trained = lgb.train(parameters, dataset, num_boost_round=BOOSTING_ROUNDS)
        except Exception as exc:
            last_fit_error = f"training_failed:{type(exc).__name__}"
            return None
        last_fit_error = None
        return trained

    for signal_index in range(evaluation_start, len(dates) - 1):
        signal_date = dates[signal_index]
        fill_date = dates[signal_index + 1]
        scheduled_rebalance = (signal_index - evaluation_start) % REBALANCE_EVERY_SESSIONS == 0
        forced_exit = bool(state.get("risk_locked", False))
        should_rebalance = scheduled_rebalance or forced_exit
        targets: list[str] = []

        if should_rebalance and not forced_exit:
            if booster is None or last_fit_index is None or signal_index - last_fit_index >= REFIT_EVERY_SESSIONS:
                trained = train_at(signal_index)
                if trained is not None:
                    booster = trained
                    last_fit_index = signal_index
                    training_dates.append(signal_date)

            candidates = features_by_index.get(signal_index, [])
            market_window = benchmark_closes[max(0, signal_index - 199):signal_index + 1]
            market_average = statistics.fmean(market_window) if len(market_window) == 200 else 0.0
            market_is_up = market_average > 0 and benchmark_closes[signal_index] > market_average
            if booster is not None and market_is_up and candidates:
                predictions = booster.predict(
                    np.asarray([feature for _symbol, feature in candidates], dtype=np.float32),
                    num_threads=1,
                )
                ranked = sorted(
                    (
                        (float(prediction), symbol)
                        for (symbol, _feature), prediction in zip(candidates, predictions)
                        if isfinite(float(prediction)) and float(prediction) >= MIN_EXPECTED_EXCESS_RETURN
                    ),
                    key=lambda item: (-item[0], item[1]),
                )
                targets = [symbol for _score, symbol in ranked[:MAX_POSITIONS]]
                signal_count += bool(targets)

        if should_rebalance:
            open_prices: dict[str, float] = {}
            for symbol, rows in rowmaps.items():
                row = rows.get(fill_date)
                if row is not None:
                    price = _positive_number(row.get("open"))
                    if price > 0:
                        open_prices[symbol] = price
            if all(symbol in open_prices for symbol in state["positions"]):
                valid_targets = [symbol for symbol in targets if symbol in open_prices]
                missing_fill_symbols += len(targets) - len(valid_targets)
                trades.extend(
                    execute_rebalance(
                        state, valid_targets, open_prices, fill_date, signal_date,
                        cost_multiplier=cost_multiplier,
                    )
                )
            else:
                missing_fill_symbols += sum(symbol not in open_prices for symbol in state["positions"])
        day_closes: dict[str, float] = {}
        for symbol, rows in rowmaps.items():
            row = rows.get(fill_date)
            if row is not None:
                close = _positive_number(row.get("close"))
                if close > 0:
                    day_closes[symbol] = close
                    last_close[symbol] = close
        missing_held = [symbol for symbol in state["positions"] if symbol not in day_closes]
        if missing_held:
            missing_mark_sessions += 1
            for symbol in missing_held:
                if last_close.get(symbol, 0) > 0:
                    day_closes[symbol] = last_close[symbol]
        was_locked = bool(state.get("risk_locked", False))
        try:
            snapshot = mark_to_market(state, day_closes)
            equity = snapshot["equity"]
        except (ValueError, KeyError):
            equity = float(equity_points[-1]["equity"])
        if not was_locked and state.get("risk_locked", False) and risk_lock_date is None:
            risk_lock_date = fill_date
        equity_points.append({"date": fill_date, "equity": round(float(equity), 2)})

    if len(equity_points) < 3:
        return {**base, "status": "insufficient_history", "trade_count": len(trades)}

    # Import metric helpers only at runtime to avoid a module import cycle.
    from .research import _metrics, _yearly_breakdown

    metrics = _metrics(
        [float(point["equity"]) for point in equity_points],
        len(trades),
        len(equity_points) - 1,
    )
    benchmark_start = _positive_number(benchmark_by_date.get(equity_points[0]["date"], {}).get("close"))
    benchmark_end = _positive_number(benchmark_by_date.get(equity_points[-1]["date"], {}).get("close"))
    benchmark_return = benchmark_end / benchmark_start - 1.0 if benchmark_start and benchmark_end else None
    metrics.update({
        "strategy": MODEL_NAME,
        "status": "ok",
        "model_family": MODEL_FAMILY,
        "cost_multiplier": cost_multiplier,
        "benchmark_total_return_pct": round(benchmark_return * 100.0, 3) if benchmark_return is not None else None,
        "excess_return_vs_nifty_pct": (
            round((metrics["total_return_pct"] - benchmark_return * 100.0), 3)
            if benchmark_return is not None and "total_return_pct" in metrics else None
        ),
        "sample_start": equity_points[0]["date"],
        "sample_end": equity_points[-1]["date"],
        "raw_benchmark_sessions": len(dates),
        "evaluation_sessions": len(equity_points) - 1,
        "training_sample_count_available": len(training_samples),
        "training_sample_count_last_fit": last_fit_sample_count,
        "model_refresh_count": len(training_dates),
        "model_training_dates": training_dates,
        "last_training_status": last_fit_error or "ok",
        "signal_dates_with_positive_alpha": signal_count,
        "candidate_symbols_missing_next_open": missing_fill_symbols,
        "sessions_with_missing_held_close": missing_mark_sessions,
        "risk_lock_triggered": risk_lock_date is not None,
        "risk_lock_date": risk_lock_date,
        "yearly_results": _yearly_breakdown(equity_points, trades, benchmark_by_date),
        "walk_forward": _walk_forward_periods(equity_points, benchmark_by_date),
    })
    return {**base, **metrics, "status": "ok"}
