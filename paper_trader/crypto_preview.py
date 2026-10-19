"""Read-only near-live crypto candle preview for the localhost dashboard.

Never imports or calls the trading engine's run/rebalance operations and never
writes portfolio state. Public exchange minute bars can be delayed or unavailable.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

SYMBOLS = ("BTC-USD", "ETH-USD")
POLL_TTL_SECONDS = 10
MAX_BARS = 180


def _utc(seconds: int | float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


def _validate_bar(stamp: int, opened: float, high: float, low: float, closed: float) -> dict:
    values = (opened, high, low, closed)
    if not all(math.isfinite(x) and x > 0 for x in values):
        raise ValueError("Nonfinite or nonpositive crypto candle")
    if low > min(opened, closed) or high < max(opened, closed):
        raise ValueError("Inconsistent crypto OHLC candle")
    return {
        "time": _utc(stamp), "open": opened, "high": high,
        "low": low, "close": closed,
    }


def parse_binance_klines(raw: list, now: datetime | None = None) -> list[dict]:
    """Parse actual Binance 1m OHLC; the last candle may still be forming."""
    now = now or datetime.now(timezone.utc)
    result = []
    for item in raw:
        try:
            stamp = int(item[0]) / 1000.0
            close_millis = int(item[6])
            if stamp > now.timestamp() + 60:
                continue
            bar = _validate_bar(stamp, *(float(item[x]) for x in (1, 2, 3, 4)))
            # Binance's index 5 is actual BASE-ASSET volume in BTC or ETH,
            # not USD trading volume. Never synthesize a missing/invalid value.
            if len(item) > 5:
                amount = float(item[5])
                if math.isfinite(amount) and amount >= 0:
                    bar["volume"] = amount
            bar["complete"] = close_millis < now.timestamp() * 1000
            result.append(bar)
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
    return _finalize(result)


def parse_coinbase_candles(raw: list, now: datetime | None = None) -> list[dict]:
    """Coinbase Exchange granularity=60: [epoch, low, high, open, close, volume]."""
    now = now or datetime.now(timezone.utc)
    result = []
    for item in raw:
        try:
            stamp = int(item[0])
            if stamp > now.timestamp() + 60:
                continue
            bar = _validate_bar(stamp, *(float(item[x]) for x in (3, 2, 1, 4)))
            # Coinbase candle index 5 is also base-asset traded volume.
            if len(item) > 5:
                amount = float(item[5])
                if math.isfinite(amount) and amount >= 0:
                    bar["volume"] = amount
            bar["complete"] = stamp + 60 <= now.timestamp()
            result.append(bar)
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
    return _finalize(result)


def _finalize(rows: list[dict]) -> list[dict]:
    unique = {row["time"]: row for row in rows}
    return [unique[key] for key in sorted(unique)][-MAX_BARS:]


def _read_json(url: str):
    request = urllib.request.Request(
        url, headers={"User-Agent": "NEON-CryptoPaper-Preview/1.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=9) as response:
        return json.loads(response.read(1_500_000).decode("utf-8"))


def _fresh(rows: list[dict]) -> bool:
    """Reject a feed that has stopped producing new candles."""
    if not rows:
        return False
    newest = datetime.fromisoformat(rows[-1]["time"])
    delta = datetime.now(timezone.utc) - newest
    return timedelta(seconds=-65) <= delta <= timedelta(minutes=4)


def download_crypto_minute(symbol: str) -> tuple[list[dict], str]:
    if symbol not in SYMBOLS:
        raise ValueError("Unsupported crypto market")
    pair = symbol.replace("-USD", "USDT")
    failures = []
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            url = f"{host}/api/v3/klines?symbol={pair}&interval=1m&limit={MAX_BARS}"
            rows = parse_binance_klines(_read_json(url))
            if _fresh(rows):
                return rows, "Binance public " + pair + " minute candles (USDT ≈ USD)"
            failures.append("Binance source has no recent usable candles")
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            failures.append("Binance: " + type(exc).__name__)
    try:
        url = f"https://api.exchange.coinbase.com/products/{symbol}/candles?granularity=60"
        rows = parse_coinbase_candles(_read_json(url))
        if _fresh(rows):
            return rows, "Coinbase Exchange public " + symbol + " minute candles (USD)"
        failures.append("Coinbase source has no recent usable candles")
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        failures.append("Coinbase: " + type(exc).__name__)
    raise RuntimeError("Live minute preview unavailable: " + "; ".join(failures))


class CryptoPreviewCache:
    """Cache public 1m data across browser tabs; no invented tick prices."""
    def __init__(self, fetch_fn=download_crypto_minute, ttl_seconds: int = POLL_TTL_SECONDS):
        self.fetch_fn = fetch_fn
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict]] = {}

    def get(self, symbol: str) -> dict:
        if symbol not in SYMBOLS:
            raise ValueError("Unsupported crypto market")
        with self._lock:
            now = time.monotonic()
            previous = self._entries.get(symbol)
            if previous is None or now - previous[0] >= self.ttl_seconds:
                try:
                    rows, provider = self.fetch_fn(symbol)
                    if not rows:
                        raise RuntimeError("Empty candle response")
                    payload = {
                        "status": "ok", "bars": rows,
                        "provider": provider, "symbol": symbol, "interval": "1m",
                        "last_bar_time_utc": rows[-1]["time"],
                        "last_bar_complete": rows[-1].get("complete"),
                        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                except Exception as exc:
                    # Preserve a previously obtained *actual* series, but never
                    # report it as a fresh live quote after provider failure.
                    if previous is not None and previous[1].get("bars"):
                        payload = {
                            **previous[1], "status": "stale",
                            "message": "Latest provider refresh failed (" + type(exc).__name__ + ")",
                        }
                    else:
                        payload = {
                            "status": "unavailable", "bars": [], "symbol": symbol,
                            "interval": "1m", "provider": None,
                            "message": "Market preview unavailable (" + type(exc).__name__ + ")",
                        }
                self._entries[symbol] = (time.monotonic(), payload)
            captured, payload = self._entries[symbol]
            return {
                **payload, "cache_age_seconds": round(time.monotonic() - captured, 1),
                "backend_refresh_seconds": self.ttl_seconds,
            }
