"""Daily market-data retrieval via yfinance (unofficial Yahoo Finance interface)."""

from __future__ import annotations

import math
from typing import Any

from .config import HISTORY_PERIOD
from .symbols import BENCHMARK_TICKER, NIFTY50_SYMBOLS, YAHOO_TICKERS

BENCHMARK_KEY = "__BENCHMARK__"


def _to_date(index_value: Any) -> str:
    if hasattr(index_value, "date"):
        return index_value.date().isoformat()
    return str(index_value)[:10]


def download_market_data() -> dict[str, list[dict[str, Any]]]:
    """Download adjusted daily OHLC bars. Raises on total failure; never fabricates data."""
    try:
        import pandas as pd
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("Missing dependencies. Install requirements.txt before running the bot.") from exc

    yahoo_symbols = list(YAHOO_TICKERS.values()) + [BENCHMARK_TICKER]
    raw = yf.download(
        tickers=yahoo_symbols,
        period=HISTORY_PERIOD,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        actions=False,
        progress=False,
        threads=True,
        timeout=30,
    )
    if raw is None or raw.empty:
        raise RuntimeError("Yahoo Finance returned no daily bars")

    output: dict[str, list[dict[str, Any]]] = {}
    symbol_map = dict(YAHOO_TICKERS)
    symbol_map[BENCHMARK_TICKER] = BENCHMARK_KEY

    for yahoo_symbol, local_symbol in symbol_map.items():
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if yahoo_symbol in raw.columns.get_level_values(0):
                    frame = raw[yahoo_symbol].copy()
                elif yahoo_symbol in raw.columns.get_level_values(1):
                    frame = raw.xs(yahoo_symbol, axis=1, level=1).copy()
                else:
                    continue
            elif len(yahoo_symbols) == 1:
                frame = raw.copy()
            else:
                continue

            frame.columns = [str(column).lower().replace(" ", "_") for column in frame.columns]
            if "open" not in frame.columns or "close" not in frame.columns:
                continue
            rows: list[dict[str, Any]] = []
            for index, row in frame.iterrows():
                try:
                    opened = float(row["open"])
                    closed = float(row["close"])
                    if not (math.isfinite(opened) and math.isfinite(closed) and opened > 0 and closed > 0):
                        continue
                    rows.append({"date": _to_date(index), "open": opened, "close": closed})
                except (TypeError, ValueError, KeyError):
                    continue
            rows.sort(key=lambda entry: entry["date"])
            if rows:
                # Defensive deduplication in case the provider returns duplicate dates.
                unique = {row["date"]: row for row in rows}
                output[local_symbol] = [unique[day] for day in sorted(unique)]
        except (KeyError, ValueError, TypeError) as exc:
            print(f"Data warning for {yahoo_symbol}: {type(exc).__name__}")

    if BENCHMARK_KEY not in output:
        raise RuntimeError("Benchmark history (^NSEI) was unavailable; refusing to trade")
    return output
