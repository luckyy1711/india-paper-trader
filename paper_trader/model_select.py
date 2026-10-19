"""Paper-only strategy selection with atomic portfolio audit and explicit cancellation.

The selected strategy lives in state.json when a portfolio exists so that cloud
and local runs read the same authoritative value. No broker orders are placed.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ACTIVE_STRATEGY, STRATEGIES
from .main import STATE_PATH, _atomic_json, _load_state, portfolio_lock

SELECTION_PATH = Path(__file__).resolve().parents[1] / "data" / "selected_strategy.json"
AUDIT_LIMIT = 100


def _legacy_strategy() -> str:
    if not SELECTION_PATH.exists():
        return ACTIVE_STRATEGY
    obj = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or obj.get("strategy") not in STRATEGIES:
        raise ValueError("Invalid selected strategy file; refusing to trade")
    return obj["strategy"]


def current_strategy() -> str:
    """Read the authoritative strategy; fail closed on invalid saved values."""
    if STATE_PATH.exists():
        state = _load_state()
        if "selected_strategy" in state:
            choice = state["selected_strategy"]
            if choice not in STRATEGIES:
                raise ValueError("Invalid strategy in portfolio state; refusing to trade")
            return choice
    return _legacy_strategy()


def switch_strategy(strategy: str, *, cancel_pending: bool = False) -> dict:
    """Atomically update a saved portfolio without changing cash, fills, risk or holdings.

    Pending unfilled targets are discarded only with the explicit flag and only
    for an actual strategy change. last_signal_date intentionally stays intact.
    """
    if strategy not in STRATEGIES:
        raise ValueError("Unsupported daily paper strategy; LightGBM is research-only")
    with portfolio_lock():
        state = _load_state() if STATE_PATH.exists() else None
        if state is None:
            # Legacy selection before the very first paper portfolio is created.
            old = _legacy_strategy()
            if cancel_pending:
                raise ValueError("No saved portfolio exists; omit --cancel-pending")
            _atomic_json(SELECTION_PATH, {"strategy": strategy})
            return {"old_strategy": old, "strategy": strategy, "cancelled": None, "changed": old != strategy}

        if state.get("risk_locked"):
            raise ValueError("Risk lock is active; switching strategy is blocked")

        old = state.get("selected_strategy", _legacy_strategy() if "selected_strategy" not in state else None)
        if old not in STRATEGIES:
            raise ValueError("Invalid saved strategy; refusing to change the portfolio")
        if old == strategy:
            return {"old_strategy": old, "strategy": strategy, "cancelled": None, "changed": False}

        pending = state.get("pending_target")
        if pending is not None and not cancel_pending:
            raise ValueError(
                "Unfilled pending targets exist. Re-run with --cancel-pending to explicitly discard them; no state changed."
            )
        cancelled = None
        if pending is not None:
            # _load_state already validates pending shape, so only store known fields.
            cancelled = {
                "signal_date": pending["signal_date"],
                "target_symbols": list(pending["target_symbols"]),
            }

        event = {
            "timestamp_ist": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds"),
            "event": "strategy_changed",
            "from_strategy": old,
            "to_strategy": strategy,
            "cancelled_pending": cancelled,
            "last_signal_date_unchanged": state.get("last_signal_date"),
        }
        history = state.get("strategy_switch_audit", [])
        if not isinstance(history, list):
            raise ValueError("Invalid strategy audit history; refusing to change the portfolio")
        history.append(event)
        state["strategy_switch_audit"] = history[-AUDIT_LIMIT:]
        state["pending_target"] = None if pending is not None else state.get("pending_target")
        state["selected_strategy"] = strategy
        _atomic_json(STATE_PATH, state)
        return {"old_strategy": old, "strategy": strategy, "cancelled": cancelled, "changed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Select future paper-only strategy; never places orders")
    parser.add_argument("--set", choices=sorted(STRATEGIES))
    parser.add_argument("--cancel-pending", action="store_true",
                        help="Cancel unfilled pending targets only when switching models")
    args = parser.parse_args()
    if args.set is None:
        if args.cancel_pending:
            parser.error("--cancel-pending requires --set")
        print("Active paper strategy:", current_strategy())
        print("Available:", ", ".join(sorted(STRATEGIES)))
        print("LightGBM remains research-only.")
        return
    try:
        result = switch_strategy(args.set, cancel_pending=args.cancel_pending)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.error(str(exc))
    if not result["changed"]:
        print("Already using this model. Pending signal and portfolio were not changed.")
        return
    if result["cancelled"] is not None:
        print("Cancelled ONLY unfilled pending stocks:", ", ".join(result["cancelled"]["target_symbols"]) or "(none)")
    print("Future paper strategy:", result["strategy"])
    print("Saved in portfolio state with an audit event. Cash, holdings, fills, risk lock and processed dates unchanged.")
    print("The same session will not be processed twice; new signals wait for the next eligible session.")


if __name__ == "__main__":
    main()
