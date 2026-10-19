"""Generate a read-only chart snapshot of actual paper trades and latest market bars.

Does not run a trading cycle, alter state, or create hypothetical fills.
"""
from __future__ import annotations
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from .market_data import download_market_data, BENCHMARK_KEY
from .main import STATE_PATH, TRADES_PATH
from .core import select_targets
from .config import STRATEGIES
from .model_select import current_strategy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "paper_chart.json"


def build_snapshot(bars, state, trades):
    traded = {t.get("symbol") for t in trades if t.get("symbol")}
    traded.update(state.get("positions", {}).keys())
    pending = state.get("pending_target") or {}
    traded.update(pending.get("target_symbols", []))
    traded = {s for s in traded if s in bars and s != BENCHMARK_KEY}
    symbols = sorted(traded)
    if not symbols:
        symbols = sorted(s for s in bars if s != BENCHMARK_KEY)[:5]
    model_signals = {}
    for name in STRATEGIES:
        try:
            targets = select_targets(
                {s: rows for s, rows in bars.items() if s != BENCHMARK_KEY},
                name, benchmark_bars=bars.get(BENCHMARK_KEY, []),
            )
            model_signals[name] = {"targets": targets, "status": "ok"}
        except (ValueError, KeyError, TypeError) as exc:
            model_signals[name] = {"targets": [], "status": "error", "reason": str(exc)[:120]}
    # Include all current strategy candidates so switching previews can inspect their charts.\n    symbols = sorted(set(symbols) | {s for v in model_signals.values() for s in v.get("targets", []) if s in bars})\n    return {\n        "active_strategy": current_strategy(),
        "model_signals": model_signals,
        "mode": "actual_paper_portfolio_read_only",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance daily bars (delayed, not streaming)",
        "state": {
            "cash": state.get("cash"),
            "positions": state.get("positions", {}),
            "risk_locked": state.get("risk_locked"),
            "pending_target": state.get("pending_target"),
        },
        "trades": trades,
        "candles": {s: [
            {k: row[k] for k in ("date", "open", "high", "low", "close") if k in row}
            for row in bars[s][-260:]
        ] for s in symbols},
        "benchmark": [
            {k: row[k] for k in ("date", "open", "high", "low", "close") if k in row}
            for row in bars.get(BENCHMARK_KEY, [])[-260:]
        ],
    }


def main():
    if not STATE_PATH.exists():
        print("No paper portfolio state found. Run the paper bot first.")
        return
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    trades = []
    if TRADES_PATH.exists():
        with TRADES_PATH.open(newline="", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
    print("Fetching market bars for read-only paper chart...", flush=True)
    snapshot = build_snapshot(download_market_data(), state, trades)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"Saved {OUT} | {len(trades)} actual recorded fills | {len(snapshot['candles'])} symbols")


if __name__ == "__main__":
    main()
