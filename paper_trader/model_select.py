"""Explicit CLI selection of future paper signals; never places broker orders."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from .config import ACTIVE_STRATEGY, STRATEGIES
from .main import STATE_PATH, _load_state, _atomic_json

SELECTION_PATH = Path(__file__).resolve().parents[1] / "data" / "selected_strategy.json"


def current_strategy():
    if not SELECTION_PATH.exists():
        return ACTIVE_STRATEGY
    obj = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    name = obj.get("strategy")
    if name not in STRATEGIES:
        raise ValueError("Invalid selected strategy; refusing to trade")
    return name


def main():
    parser = argparse.ArgumentParser(description="Select future paper strategy (no broker orders)")
    parser.add_argument("--set", choices=sorted(STRATEGIES))
    parser.add_argument("--cancel-pending", action="store_true",
                        help="Explicitly cancel unfilled targets during model switch; never touches holdings, cash or risk lock")
    args = parser.parse_args()
    if args.set is None:
        if args.cancel_pending:
            parser.error("--cancel-pending requires --set")
        print("Active paper strategy:", current_strategy())
        print("Available:", ", ".join(sorted(STRATEGIES)))
        print("LightGBM is research-only; it cannot execute daily paper signals.")
        return
    state = _load_state() if STATE_PATH.exists() else None
    if state and state.get("risk_locked"):
        parser.error("Risk lock is active; cannot change strategy.")
    if state and state.get("pending_target") is not None and not args.cancel_pending:
        parser.error("Pending paper signal exists. Use --cancel-pending to intentionally discard unfilled targets; no state was changed.")
    if args.cancel_pending and state is None:
        parser.error("No paper portfolio state to update; no changes made.")

    # Do not reset last_signal_date: the bot may not create a second signal
    # for a market date it has already processed.
    if state and args.cancel_pending and state.get("pending_target") is not None:
        state["pending_target"] = None
        _atomic_json(STATE_PATH, state)
        print("Cancelled unfilled pending paper signal; cash, holdings and last signal date unchanged.")

    SELECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = SELECTION_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps({"strategy": args.set}, indent=2) + "\n", encoding="utf-8")
    temp.replace(SELECTION_PATH)
    print("Active model for FUTURE paper signals:", args.set)
    print("No orders placed. Previously processed session will not run again.")


if __name__ == "__main__":
    main()
