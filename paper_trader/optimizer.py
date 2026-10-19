"""Chronological strategy selection experiment. Never alters paper-trading state."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import STRATEGIES
from .market_data import download_market_data
from .research import backtest_strategy

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "optimizer.json"


def segment(points, start, end):
    part = points[start:end + 1]
    if len(part) < 2 or float(part[0]["equity"]) <= 0:
        return None
    initial = float(part[0]["equity"])
    final = float(part[-1]["equity"])
    peak = initial
    drawdown = 0.0
    for p in part:
        value = float(p["equity"])
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak)
    return {
        "start": part[0]["date"],
        "end": part[-1]["date"],
        "return_pct": round((final / initial - 1) * 100, 3),
        "max_drawdown_pct": round(drawdown * 100, 3),
        "end_equity": round(final, 2),
    }


def run_optimizer(bars, *, training_sessions=504, holdout_sessions=252):
    if training_sessions < 252 or holdout_sessions < 126:
        raise ValueError("Need at least 252 training and 126 holdout sessions")
    total = training_sessions + holdout_sessions
    results = []
    for name in sorted(STRATEGIES):
        print(f"Testing {name}...", flush=True)
        result = backtest_strategy(bars, name, sample_days=total)
        if result.get("status") != "ok":
            results.append({"strategy": name, "status": result.get("status", "failed")})
            continue
        points = result.get("replay_equity_points", [])
        if len(points) < total + 1:
            results.append({"strategy": name, "status": "insufficient_sessions", "available": len(points) - 1})
            continue
        points = points[-(total + 1):]
        train = segment(points, 0, training_sessions)
        # Reset virtual portfolio for independent holdout; do not inherit training lock.
        holdout_result = backtest_strategy(bars, name, sample_days=holdout_sessions)
        holdout_points = holdout_result.get("replay_equity_points", [])
        holdout = segment(holdout_points, 0, len(holdout_points) - 1) if holdout_result.get("status") == "ok" else None
        if train is None or holdout is None:
            continue
        # Explicit selection criterion, fixed before viewing holdout results.
        score = train["return_pct"] - 0.75 * train["max_drawdown_pct"]
        results.append({
            "strategy": name, "status": "ok",
            "training": train, "holdout": holdout,
            "training_score": round(score, 3),
            "full_period_trades": result.get("trade_count"),
        })
    ranked = sorted((r for r in results if r.get("status") == "ok"),
                    key=lambda r: (-r["training_score"], r["strategy"]))
    winner = ranked[0] if ranked else None
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "research_only_chronological_strategy_selection",
        "training_sessions": training_sessions,
        "holdout_sessions": holdout_sessions,
        "selection_rule": "training_return_pct - 0.75 * training_max_drawdown_pct",
        "selected_strategy": winner["strategy"] if winner else None,
        "selected_holdout": winner["holdout"] if winner else None,
        "results": results,
        "safety": {
            "paper_portfolio_untouched": True,
            "active_strategy_unchanged": True,
            "broker_orders_enabled": False,
        },
        "limitations": [
            "Only one chronological holdout; historical performance does not predict profits.",
            "The 50-stock universe may have survivorship bias.",
            "Holdout uses a separately reset virtual portfolio, avoiding inherited training risk lock.",
            "Selection uses only training metrics; holdout is reported but never used for selection.",
            "Rule-based strategies only; LightGBM omitted because training costs and evaluation differ.",
            "Trading costs and 10% drawdown lock are included by the backtest engine.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Compare rule strategies without touching paper trades")
    parser.add_argument("--training-sessions", type=int, default=504)
    parser.add_argument("--holdout-sessions", type=int, default=252)
    args = parser.parse_args()
    print("Downloading one shared market-data snapshot...", flush=True)
    bars = download_market_data()
    report = run_optimizer(bars, training_sessions=args.training_sessions,
                           holdout_sessions=args.holdout_sessions)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nSTRATEGY                  TRAIN %   HOLDOUT %  HOLDOUT DD %", flush=True)
    for row in sorted(report["results"], key=lambda r: -r.get("training_score", -1e9)):
        if row.get("status") != "ok":
            print(f'{row["strategy"]}: {row["status"]}')
            continue
        print(f'{row["strategy"]:<26}{row["training"]["return_pct"]:>8.2f}   '
              f'{row["holdout"]["return_pct"]:>8.2f}   '
              f'{row["holdout"]["max_drawdown_pct"]:>8.2f}')
    print(f'\nTraining-selected: {report["selected_strategy"]}')
    print(f'Report: {OUTPUT}')
    print("Research only: no paper-trading strategy or state was changed.")


if __name__ == "__main__":
    main()
