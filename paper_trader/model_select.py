"""Explicit CLI strategy selection for future paper signals only.

Does not execute orders. Refuses switching while a pending order exists.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .config import ACTIVE_STRATEGY, STRATEGIES
from .main import STATE_PATH

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
    parser = argparse.ArgumentParser(description="Select future paper strategy, never broker orders")
    parser.add_argument("--set", choices=sorted(STRATEGIES), required=False)
    args = parser.parse_args()
    if args.set is None:
        print("Active paper strategy:", current_strategy())
        print("Available:", ", ".join(sorted(STRATEGIES)))
        print("LightGBM remains research-only; not an executable paper strategy.")
        return
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    pending = state.get("pending_target")
    if pending is not None:
        parser.error("Pending paper signal exists. Cannot change strategy until it has been processed; no state was changed.")
    if state.get("risk_locked"):
        parser.error("Risk lock is active; cannot change strategy.")
    SELECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = SELECTION_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps({"strategy": args.set}, indent=2) + "\n", encoding="utf-8")
    temp.replace(SELECTION_PATH)
    print("Selected for future paper signals:", args.set)
    print("No orders placed. Existing holdings remain governed by normal rebalance and risk rules.")


if __name__ == "__main__":
    main()
