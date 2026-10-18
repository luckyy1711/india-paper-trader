"""Daily market-data retrieval via yfinance (unofficial Yahoo Finance interface)."""

from __future__ import annotations

import math
import time
from typing import Any

from .config import HISTORY_PERIOD
from .symbols import BENCHMARK_TICKER, NIFTY50_SYMBOLS, YAHOO_TICKERS

BENCHMARK_KEY = "__BENCHMARK__"
BATCH_SIZE = 8


def _to_date(index_value: Any) -> str:
    if hasattr(index_value, "date"):
        return index_value.date().isoformat()
    return str(index_value)[:10]


def _rows_from_frame(frame: Any) -> list[dict[str, Any]]:
    """Normalize a single-ticker OHLC frame without assuming column casing."""
    if frame is None or getattr(frame, "empty", True):
        return []
    import pandas as pd

    if isinstance(frame.columns, pd.MultiIndex):
        expected = {"open", "close"}
        for level in range(frame.columns.nlevels):
            labels = {str(value).lower() for value in frame.columns.get_level_values(level)}
            if expected.issubset(labels):
                frame = frame.copy()
                frame.columns = frame.columns.get_level_values(level)
                break
    frame = frame.copy()
    frame.columns = [str(column).lower().replace(" ", "_") for column in frame.columns]
    if "open" not in frame.columns or "close" not in frame.columns:
        return []

    rows: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        try:
            opened, closed = float(row["open"]), float(row["close"])
            if math.isfinite(opened) and math.isfinite(closed) and opened > 0 and closed > 0:
                rows.append({"date": _to_date(index), "open": opened, "close": closed})
        except (TypeError, ValueError, KeyError):
            continue
    unique = {row["date"]: row for row in rows}
    return [unique[day] for day in sorted(unique)]


def _extract_ticker_frame(raw: Any, yahoo_symbol: str, expected_tickers: list[str], pd: Any) -> Any:
    if raw is None or raw.empty:
        return None
    if not isinstance(raw.columns, pd.MultiIndex):
        return raw.copy() if len(expected_tickers) == 1 else None
    for level in range(raw.columns.nlevels):
        labels = set(str(value) for value in raw.columns.get_level_values(level))
        if yahoo_symbol in labels:
            try:
                return raw.xs(yahoo_symbol, axis=1, level=level, drop_level=True).copy()
            except (KeyError, ValueError, TypeError):
                continue
    return None


def download_market_data() -> dict[str, list[dict[str, Any]]]:
    """Download daily OHLC bars in small batches to reduce provider throttling.

    Missing tickers receive one delayed individual retry. Data is never fabricated.
    Raises if the benchmark or all stock histories are unavailable.
    """
    try:
        import pandas as pd
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("Missing dependencies. Install requirements.txt before running the bot.") from exc

    symbol_map = dict(YAHOO_TICKERS)
    symbol_map[BENCHMARK_TICKER] = BENCHMARK_KEY
    yahoo_symbols = list(symbol_map)
    output: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, str] = {}

    # A single 51-symbol request followed by 50 rapid individual retries can
    # trigger throttling. Use small batches, with a pause between requests.
    for offset in range(0, len(yahoo_symbols), BATCH_SIZE):
        batch = yahoo_symbols[offset:offset + BATCH_SIZE]
        try:
            raw = yf.download(
                tickers=batch,
                period=HISTORY_PERIOD,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                actions=False,
                progress=False,
                threads=False,
                timeout=30,
            )
            for yahoo_symbol in batch:
                frame = _extract_ticker_frame(raw, yahoo_symbol, batch, pd)
                rows = _rows_from_frame(frame) if frame is not None else []
                if rows:
                    output[symbol_map[yahoo_symbol]] = rows
                else:
                    failures[yahoo_symbol] = "missing_or_unrecognized_batch_columns"
        except Exception as exc:
            for yahoo_symbol in batch:
                failures[yahoo_symbol] = f"batch_{type(exc).__name__}"
        if offset + BATCH_SIZE < len(yahoo_symbols):
            time.sleep(1.5)

    # Retry only missing tickers, slowly, to avoid hammering Yahoo Finance.
    missing = [ticker for ticker in yahoo_symbols if symbol_map[ticker] not in output]
    for yahoo_symbol in missing:
        time.sleep(2.0)
        try:
            single = yf.download(
                tickers=yahoo_symbol,
                period=HISTORY_PERIOD,
                interval="1d",
                auto_adjust=True,
                actions=False,
                progress=False,
                threads=False,
                timeout=25,
            )
            rows = _rows_from_frame(single)
            if rows:
                output[symbol_map[yahoo_symbol]] = rows
                failures.pop(yahoo_symbol, None)
            else:
                failures[yahoo_symbol] = "no_usable_rows_after_single_ticker_retry"
        except Exception as exc:
            failures[yahoo_symbol] = f"single_ticker_{type(exc).__name__}"

    if BENCHMARK_KEY not in output:
        summary = ", ".join(f"{key}:{value}" for key, value in list(failures.items())[:8])
        raise RuntimeError(f"Benchmark history (^NSEI) unavailable; refusing to trade. Diagnostics: {summary}")

    stock_count = sum(1 for symbol in output if symbol != BENCHMARK_KEY)
    if stock_count == 0:
        summary = ", ".join(f"{key}:{value}" for key, value in list(failures.items())[:8])
        raise RuntimeError(f"No stock histories could be parsed from Yahoo Finance. Diagnostics: {summary}")

    print(
        f"Market data coverage: {stock_count}/{len(NIFTY50_SYMBOLS)} stocks; "
        f"benchmark bars={len(output[BENCHMARK_KEY])}; "
        f"individual retries recovered={sum(1 for ticker in missing if symbol_map[ticker] in output)}"
    )
    return output
