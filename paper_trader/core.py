"""Pure strategy and portfolio logic; no network calls or broker integration."""

from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Any

from .config import (
    INITIAL_CASH,
    MAX_DRAWDOWN,
    MAX_POSITION_WEIGHT,
    MAX_POSITIONS,
    MAX_TOTAL_EXPOSURE,
    SLIPPAGE_RATE,
    STRATEGIES,
    TRANSACTION_COST_RATE,
)


PRICE_EPSILON = 1e-9


def new_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "initial_cash": INITIAL_CASH,
        "cash": round(INITIAL_CASH, 2),
        "positions": {},  # symbol -> whole-share quantity
        "pending_target": None,
        "last_signal_date": None,
        "last_mark_date": None,
        "peak_equity": round(INITIAL_CASH, 2),
        "risk_locked": False,
    }


def _valid_close_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for row in rows:
        try:
            close = float(row.get("close", 0))
            if close > 0 and math.isfinite(close) and row.get("date"):
                clean.append({**row, "close": close})
        except (TypeError, ValueError):
            continue
    clean.sort(key=lambda r: r["date"])
    return clean


def _sma(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    sample = values[-window:]
    return sum(sample) / window


def score_symbol(rows: list[dict[str, Any]], strategy_name: str) -> float | None:
    """Return risk-adjusted momentum score when a symbol passes its trend filter."""
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_name}")
    params = STRATEGIES[strategy_name]
    rows = _valid_close_rows(rows)
    trend_window = int(params["trend_window"])
    momentum_window = int(params["momentum_window"])
    closes = [row["close"] for row in rows]
    required = max(trend_window, momentum_window + 1)
    if len(closes) < required:
        return None

    latest = closes[-1]
    trend_average = _sma(closes, trend_window)
    if trend_average is None or latest <= trend_average:
        return None

    if params["filter"] == "crossover":
        short_average = _sma(closes, 50)
        if short_average is None or short_average <= trend_average:
            return None

    prior = closes[-(momentum_window + 1)]
    if prior <= PRICE_EPSILON:
        return None
    momentum = latest / prior - 1.0
    if momentum <= 0:
        return None

    recent = closes[-(momentum_window + 1):]
    returns = [recent[i] / recent[i - 1] - 1.0 for i in range(1, len(recent)) if recent[i - 1] > PRICE_EPSILON]
    if len(returns) < max(20, momentum_window // 2):
        return None
    volatility = statistics.pstdev(returns) * math.sqrt(252)
    if not math.isfinite(volatility) or volatility <= PRICE_EPSILON:
        return None
    return momentum / volatility


def select_targets(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    strategy_name: str = "trend_momentum_126",
    max_positions: int = MAX_POSITIONS,
) -> list[str]:
    """Select a ranked, long-only target list using information available by the close."""
    ranked: list[tuple[float, str]] = []
    for symbol, rows in bars_by_symbol.items():
        score = score_symbol(rows, strategy_name)
        if score is not None and math.isfinite(score):
            ranked.append((score, symbol))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    return [symbol for _, symbol in ranked[:max_positions]]


def mark_to_market(state: dict[str, Any], close_prices: dict[str, float]) -> dict[str, float]:
    """Revalue the portfolio; fail closed if any open holding lacks a current price."""
    cash = float(state["cash"])
    position_value = 0.0
    for symbol, quantity in state["positions"].items():
        price = close_prices.get(symbol)
        if price is None or not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError(f"Missing/invalid close price for held position {symbol}; refusing to mark portfolio")
        position_value += int(quantity) * float(price)
    equity = cash + position_value
    peak = max(float(state.get("peak_equity", state["initial_cash"])), equity)
    drawdown = 0.0 if peak <= 0 else max(0.0, (peak - equity) / peak)
    state["peak_equity"] = round(peak, 2)
    if drawdown >= MAX_DRAWDOWN:
        state["risk_locked"] = True
    exposure = 0.0 if equity <= 0 else position_value / equity
    return {
        "cash": round(cash, 2),
        "position_value": round(position_value, 2),
        "equity": round(equity, 2),
        "peak_equity": round(peak, 2),
        "drawdown_pct": round(drawdown * 100.0, 3),
        "exposure_pct": round(exposure * 100.0, 3),
    }


def execute_rebalance(
    state: dict[str, Any],
    target_symbols: list[str],
    open_prices: dict[str, float],
    fill_date: str,
    signal_date: str,
) -> list[dict[str, Any]]:
    """Execute a simulated rebalance at the next session's open, with costs and guardrails."""
    positions: dict[str, int] = {str(k): int(v) for k, v in state["positions"].items()}
    cash = float(state["cash"])
    targets = [] if state.get("risk_locked", False) else list(dict.fromkeys(target_symbols))[:MAX_POSITIONS]

    for symbol in positions:
        price = open_prices.get(symbol)
        if price is None or not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError(f"Missing/invalid open price for held position {symbol}; refusing to trade")
    for symbol in targets:
        price = open_prices.get(symbol)
        if price is None or not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError(f"Missing/invalid open price for target {symbol}; refusing to trade")

    trades: list[dict[str, Any]] = []

    def record(side: str, symbol: str, quantity: int, fill_price: float, reason: str) -> None:
        nonlocal cash
        gross = quantity * fill_price
        cost = gross * TRANSACTION_COST_RATE
        if side == "SELL":
            cash += gross - cost
        else:
            cash -= gross + cost
        trades.append({
            "date": fill_date,
            "signal_date": signal_date,
            "side": side,
            "symbol": symbol,
            "quantity": int(quantity),
            "price": round(fill_price, 4),
            "gross_value": round(gross, 2),
            "costs": round(cost, 2),
            "reason": reason,
            "cash_after": round(cash, 2),
        })

    # Sell holdings that are no longer selected first, so proceeds can fund new buys.
    for symbol in list(positions):
        if symbol not in targets:
            quantity = positions.pop(symbol)
            fill = float(open_prices[symbol]) * (1.0 - SLIPPAGE_RATE)
            record("SELL", symbol, quantity, fill, "target exit or risk lock")

    def nav_at_open() -> float:
        return cash + sum(qty * float(open_prices[symbol]) for symbol, qty in positions.items())

    nav = nav_at_open()
    position_cap = max(0.0, nav * MAX_POSITION_WEIGHT)
    # Trim existing target positions if appreciation/gaps have breached the sizing limit.
    for symbol in list(positions):
        price = float(open_prices[symbol])
        permitted = math.floor(position_cap / price) if price > 0 else 0
        excess = max(0, positions[symbol] - permitted)
        if excess:
            fill = price * (1.0 - SLIPPAGE_RATE)
            record("SELL", symbol, excess, fill, "trim to maximum position size")
            remaining = positions[symbol] - excess
            if remaining > 0:
                positions[symbol] = remaining
            else:
                positions.pop(symbol, None)

    nav = nav_at_open()
    position_cap = max(0.0, nav * MAX_POSITION_WEIGHT)
    total_exposure_cap = max(0.0, nav * MAX_TOTAL_EXPOSURE)

    for symbol in targets:
        if symbol in positions or len(positions) >= MAX_POSITIONS:
            continue
        price = float(open_prices[symbol]) * (1.0 + SLIPPAGE_RATE)
        if price <= 0:
            continue
        current_exposure = sum(qty * float(open_prices[sym]) for sym, qty in positions.items())
        position_room = max(0.0, position_cap - current_exposure * 0.0)
        exposure_room = max(0.0, total_exposure_cap - current_exposure)
        spend_room = min(cash, position_room, exposure_room)
        unit_cash_cost = price * (1.0 + TRANSACTION_COST_RATE)
        quantity = math.floor(spend_room / unit_cash_cost) if unit_cash_cost > 0 else 0
        if quantity <= 0:
            continue
        record("BUY", symbol, quantity, price, "ranked signal; whole shares only")
        positions[symbol] = quantity

    if cash < -0.01:
        raise RuntimeError("Portfolio cash became negative; refusing to save invalid state")
    state["cash"] = round(max(0.0, cash), 2)
    state["positions"] = positions
    return trades


def benchmark_dates(bars_by_symbol: dict[str, list[dict[str, Any]]]) -> list[str]:
    rows = bars_by_symbol.get("__BENCHMARK__", [])
    return sorted({str(row["date"]) for row in rows if row.get("date")})


def first_session_after(signal_date: str, dates: list[str]) -> str | None:
    return next((day for day in dates if day > signal_date), None)
