"""Local read-only 1-minute candle preview server; NEVER places orders.

Browser polls the local cache every second. The Yahoo backend is fetched at most
once per symbol per 60 seconds. This is NOT a licensed real-time NSE feed.
Bind only to localhost; the static dashboard cannot provide this API by itself.
"""
from __future__ import annotations

import argparse
import json
import math
import threading
import time
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from .symbols import YAHOO_TICKERS

ROOT = Path(__file__).resolve().parents[1]
IST = ZoneInfo("Asia/Kolkata")
TTL_SECONDS = 60


def download_intraday(symbol: str) -> list[dict]:
    """Fetch only one explicitly allowed Nifty 50 symbol; preserve actual bar times."""
    if symbol not in YAHOO_TICKERS:
        raise ValueError("Symbol is not in the configured Nifty 50 universe")
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required for the local intraday preview") from exc
    frame = yf.Ticker(YAHOO_TICKERS[symbol]).history(
        period="1d", interval="1m", auto_adjust=True, actions=False,
        prepost=False, timeout=15, raise_errors=False,
    )
    if frame is None or frame.empty:
        return []
    frame = frame.copy()
    frame.columns = [str(x).lower() for x in frame.columns]
    if not all(field in frame.columns for field in ("open", "high", "low", "close")):
        return []
    rows: list[dict] = []
    for index, candle in frame.iterrows():
        try:
            timestamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                continue  # Do not invent a timezone.
            timestamp = timestamp.astimezone(IST)
            values = {key: float(candle[key]) for key in ("open", "high", "low", "close")}
            if any(not math.isfinite(value) or value <= 0 for value in values.values()):
                continue
            if values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]):
                continue
            rows.append({"date": timestamp.isoformat(timespec="minutes"), **values})
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
    unique = {row["date"]: row for row in rows}
    return [unique[key] for key in sorted(unique)][-390:]


class QuoteCache:
    """Serialize requests so two browser tabs cannot multiply provider calls."""
    def __init__(self, fetch_fn=download_intraday, ttl: int = TTL_SECONDS):
        self.fetch_fn = fetch_fn
        self.ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict]] = {}

    def get(self, symbol: str) -> dict:
        if symbol not in YAHOO_TICKERS:
            raise ValueError("Unknown or unsupported stock symbol")
        with self._lock:
            now = time.monotonic()
            previous = self._entries.get(symbol)
            if previous is None or now - previous[0] >= self.ttl:
                fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                try:
                    bars = self.fetch_fn(symbol)
                    if bars:
                        payload = {
                            "status": "ok", "bars": bars,
                            "last_bar_time_ist": bars[-1]["date"],
                        }
                    else:
                        payload = {"status": "unavailable", "bars": [], "message": "No 1-minute provider candles available"}
                except Exception as exc:
                    payload = {
                        "status": "unavailable", "bars": [],
                        "message": f"Provider error: {type(exc).__name__}",
                    }
                payload.update({
                    "symbol": symbol,
                    "provider": "Yahoo Finance via yfinance (unofficial; not guaranteed real time)",
                    "interval": "1m",
                    "fetched_at_utc": fetched_at,
                    "backend_refresh_seconds": self.ttl,
                })
                self._entries[symbol] = (time.monotonic(), payload)
            captured, payload = self._entries[symbol]
            return {**payload, "cache_age_seconds": round(time.monotonic() - captured, 1)}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, cache: QuoteCache, **kwargs):
        self.cache = cache
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != "/api/intraday":
            return super().do_GET()
        symbol = parse_qs(url.query).get("symbol", [""])[0]
        try:
            payload = self.cache.get(symbol)
            status = 200
        except ValueError:
            status = 400
            payload = {"status": "error", "message": "Select a supported Nifty 50 symbol"}
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only local paper chart with delayed Yahoo 1-minute candles")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged port between 1024 and 65535")
    cache = QuoteCache()
    handler = partial(Handler, cache=cache)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Paper chart: http://127.0.0.1:{args.port}/dashboard/paper.html", flush=True)
        print("1-second local dashboard polling; provider fetches capped at once per symbol per 60 seconds.", flush=True)
        print("Unofficial/delayed feed; no broker integration, no tick trades.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    main()
