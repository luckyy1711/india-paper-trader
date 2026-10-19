"""Read-only USD accounting for the simulated BTC/ETH portfolio.

All values come from saved virtual cash, recorded BUY/SELL fills and the latest
confirmed bot candle. No new trades, exchange calls or portfolio mutations.
"""
from __future__ import annotations

import math
from collections import defaultdict


def _amount(value, *, allow_zero=True) -> float:
    """Reject invalid amounts rather than silently converting NaN to a profit."""
    number = float(value)
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        raise ValueError("Invalid monetary value or crypto quantity")
    return number


def crypto_performance(state: dict, bars: dict, trades: list[dict]) -> dict:
    """Compute real paper P&L, FIFO cost basis and cash-flow totals.

    Net P&L = saved cash + confirmed-candle market value - initial virtual
    capital, assuming the paper account has no deposits or withdrawals.
    FIFO realized/unrealized is hidden if the historic trade journal is
    incomplete or cannot reconcile to the portfolio's saved positions.
    """
    initial = _amount(state["initial_cash"], allow_zero=False)
    cash = _amount(state["cash"])
    positions = {symbol: _amount(qty) for symbol, qty in state.get("positions", {}).items()}

    prices = {}
    last_price_times = {}
    for symbol, quantity in positions.items():
        if not quantity:
            continue
        candles = bars.get(symbol) or []
        if not candles:
            raise ValueError("Cannot value position without a confirmed market candle: " + symbol)
        price = _amount(candles[-1]["close"], allow_zero=False)
        prices[symbol] = price
        last_price_times[symbol] = candles[-1]["time"]

    invested_market = sum(quantity * prices[symbol] for symbol, quantity in positions.items() if quantity)
    equity = cash + invested_market
    net_pnl = equity - initial

    buy_count = sell_count = winning_sells = losing_sells = 0
    total_fees = spent_on_buys = received_from_sells = 0.0
    realized = 0.0
    lots = defaultdict(list)
    accounting_ok = True
    latest = None
    recent_fills = []
    row_count = 0

    for row in trades:
        try:
            symbol = str(row["symbol"])
            side = str(row["side"]).upper()
            if side not in ("BUY", "SELL") or symbol not in ("BTC-USD", "ETH-USD"):
                raise ValueError("Unknown paper fill side or market")
            quantity = _amount(row["quantity"], allow_zero=False)
            price = _amount(row["price"], allow_zero=False)
            fee = _amount(row.get("fees", 0))
            _ = row["time"]
            total_fees += fee
            row_count += 1
            notional = quantity * price
            if side == "BUY":
                buy_count += 1
                spend = notional + fee
                spent_on_buys += spend
                lots[symbol].append([quantity, spend])
                cash_delta = -spend
                profit = None
            else:
                sell_count += 1
                received = notional - fee
                received_from_sells += received
                cash_delta = received
                if received < 0:
                    raise ValueError("Sell fee exceeds proceeds")
                remaining = quantity
                sold_basis = 0.0
                # FIFO, including the original buy fee allocated to the sold
                # fraction. Each lot is quantity + total unallocated cost.
                while remaining > 1e-10:
                    if not lots[symbol]:
                        raise ValueError("Sell is missing its earlier BUY in recorded ledger")
                    available, basis = lots[symbol][0]
                    taken = min(available, remaining)
                    basis_used = basis * (taken / available)
                    sold_basis += basis_used
                    remaining -= taken
                    available -= taken
                    basis -= basis_used
                    if available <= 1e-10:
                        lots[symbol].pop(0)
                    else:
                        lots[symbol][0] = [available, basis]
                profit = received - sold_basis
                realized += profit
                winning_sells += int(profit > 1e-8)
                losing_sells += int(profit < -1e-8)
            latest = {
                "time": str(row["time"]),
                "side": side,
                "symbol": symbol,
                "quantity": quantity,
                "price": price,
                "fees_usd": fee,
                "notional_usd": notional,
                "cash_change_usd": cash_delta,
                "realized_pnl_usd": profit,
                "reason": str(row.get("reason", "")),
            }
            recent_fills.append(latest)
        except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
            accounting_ok = False
            # Do not claim a partial FIFO profit figure is fully reconciled.
            break

    remaining_basis = sum(basis for market in lots.values() for qty, basis in market)
    # CSV quantities are rounded to 8 decimals, while account.positions uses
    # unrounded floats. Ignore only sub-microcoin differences from serialization.
    for symbol in set(lots).union(positions):
        journal_qty = sum(qty for qty, basis in lots[symbol])
        account_qty = positions.get(symbol, 0.0)
        if abs(journal_qty - account_qty) > max(2e-7, account_qty * 0.000001):
            accounting_ok = False

    unrealized = invested_market - remaining_basis if accounting_ok else None
    if accounting_ok and abs((realized + unrealized) - net_pnl) > max(0.05, initial * 0.0001):
        accounting_ok = False  # Missing journal rows or a nontrade cash adjustment.

    if not accounting_ok:
        for fill in recent_fills:
            fill["realized_pnl_usd"] = None
        if latest is not None:
            latest["realized_pnl_usd"] = None
        realized_value = None
        unrealized = None
        remaining_basis_value = None
        winning_value = losing_value = None
    else:
        realized_value = realized
        remaining_basis_value = remaining_basis
        winning_value = winning_sells
        losing_value = losing_sells

    last_completed_fills = state.get("last_fills")
    if isinstance(last_completed_fills, list) and last_completed_fills:
        last_decision = "BUY / SELL"
    elif isinstance(last_completed_fills, list):
        last_decision = "HOLD"
    else:
        last_decision = "NO RECENT FILL DATA"

    return {
        "currency": "USD",
        "initial_capital_usd": initial,
        "available_cash_usd": cash,
        "holdings_market_value_usd": invested_market,
        "portfolio_equity_usd": equity,
        "net_pnl_usd": net_pnl,
        "net_return_pct": 100.0 * net_pnl / initial,
        "open_cost_basis_usd": remaining_basis_value,
        "realized_pnl_usd": realized_value,
        "unrealized_pnl_usd": unrealized,
        "fifo_reconciled": accounting_ok,
        "lifetime_buy_spend_usd": spent_on_buys,
        "lifetime_sell_proceeds_usd": received_from_sells,
        "total_recorded_fees_usd": total_fees,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "winning_sells": winning_value,
        "losing_sells": losing_value,
        "recorded_trade_count": row_count,
        "last_recorded_trade": latest,
        "recent_fills": recent_fills[-80:],
        "last_processed_action": last_decision,
        "valued_at_utc": max(last_price_times.values(), default=state.get("last_bar")),
        "valuation_note": "Based on the last CONFIRMED bot candles, not the separately refreshed preview price. Net P&L includes virtual transaction costs and any unrealized mark-to-market changes. No real money.",
        "basis_note": (
            "FIFO matches all recorded BUY/SELL fills, including fees."
            if accounting_ok else
            "Unverified historical fills or cash reconciliation: realized/unrealized split withheld; net equity P&L remains available."
        ),
    }
