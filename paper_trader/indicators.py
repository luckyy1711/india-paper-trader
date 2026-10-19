"""Pure daily OHLC indicators for research and *paper-only* signal confirmation.

Each value uses only the bars supplied through the signal session. No future bars.
Invalid/missing high or low values cause conservative rejection.
"""
from __future__ import annotations

import math


def _ema_series(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    ema = sum(values[:period]) / period
    result[period - 1] = ema
    multiplier = 2.0 / (period + 1)
    for index in range(period, len(values)):
        ema += (values[index] - ema) * multiplier
        result[index] = ema
    return result


def indicator_snapshot(rows: list[dict]) -> dict[str, float] | None:
    """Wilder RSI(14), ATR(14), EMA(20/50), MACD(12,26,9).

    Reject invalid OHLC and require at least 60 sessions; values are unrounded.
    All computations end at the final supplied daily candle.
    """
    if len(rows) < 60:
        return None
    closes, highs, lows = [], [], []
    for row in rows:
        try:
            close, high, low = (float(row[key]) for key in ("close", "high", "low"))
        except (KeyError, ValueError, TypeError):
            return None
        if not all(map(math.isfinite, (close, high, low))) or low <= 0 or not low <= close <= high:
            return None
        closes.append(close)
        highs.append(high)
        lows.append(low)

    ema20 = _ema_series(closes, 20)[-1]
    ema50 = _ema_series(closes, 50)[-1]
    fast, slow = _ema_series(closes, 12), _ema_series(closes, 26)
    macd_values = [a - b for a, b in zip(fast, slow) if a is not None and b is not None]
    if len(macd_values) < 9:
        return None
    macd = macd_values[-1]
    signal = _ema_series(macd_values, 9)[-1]

    gains, losses, ranges = [], [], []
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
        ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        ))
    period = 14
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    atr = sum(ranges[:period]) / period
    for index in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
        atr = (atr * (period - 1) + ranges[index]) / period
    rsi = 50.0 if avg_gain == 0 and avg_loss == 0 else (
        100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    )
    values = {
        "ema20": ema20, "ema50": ema50,
        "rsi14": rsi, "atr14": atr, "atr_pct": atr / closes[-1] * 100.0,
        "macd": macd, "macd_signal": signal, "macd_hist": macd - signal,
    }
    if any(value is None or not math.isfinite(value) for value in values.values()):
        return None
    return values


def confirmation_reason(values: dict[str, float] | None) -> str | None:
    """Return first reason for declining a candidate, or None if confirmed."""
    if values is None:
        return "Missing or invalid OHLC history for RSI, ATR, EMA, MACD"
    if values["ema20"] <= values["ema50"]:
        return "EMA20 is not above EMA50"
    if not 50 <= values["rsi14"] <= 75:
        return "RSI14 outside 50–75 confirmation range"
    if values["macd_hist"] <= 0:
        return "MACD line is not above its 9-session signal"
    if not 0.5 <= values["atr_pct"] <= 5.0:
        return "ATR14 outside 0.5–5% volatility filter"
    return None
