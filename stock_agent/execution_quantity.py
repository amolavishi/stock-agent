"""Python-owned transaction quantity semantics for ExecutionAction.

`position_shares` is state. `risk_target_shares` is the maximum/target holding
allowed by the current RiskEngine result. The returned value is the shares to
transact now; these quantities must never be conflated.
"""
from __future__ import annotations

import math
from typing import Any

from .models import ExecutionAction


class ExecutionQuantityError(ValueError):
    pass


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return int(value)
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    return None


def _planned_add_requested_shares(add_plan: dict[str, Any], *, price: float, equity: float) -> int:
    explicit = _positive_int(add_plan.get("planned_add_shares"))
    if explicit is not None:
        return explicit
    pct = add_plan.get("planned_add_capital_pct")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or float(pct) <= 0:
        raise ExecutionQuantityError("ADD plan must specify positive planned_add_shares or planned_add_capital_pct")
    if price <= 0 or equity <= 0:
        raise ExecutionQuantityError("ADD percentage sizing requires positive price and equity")
    shares = int(math.floor(equity * float(pct) / 100.0 / price))
    if shares <= 0:
        raise ExecutionQuantityError("ADD percentage plan rounds to zero shares")
    return shares


def transaction_shares(
    action: ExecutionAction,
    *,
    position_shares: int,
    risk_target_shares: int,
    price: float,
    equity: float,
    add_plan: dict[str, Any] | None = None,
) -> int:
    current = max(0, int(position_shares))
    risk_target = max(0, int(risk_target_shares))

    if action in {ExecutionAction.NO_TRADE, ExecutionAction.WATCH}:
        return 0
    if action == ExecutionAction.STARTER:
        if current != 0:
            raise ExecutionQuantityError("STARTER requires no existing position")
        if risk_target <= 0:
            raise ExecutionQuantityError("STARTER requires positive RiskEngine shares")
        return risk_target
    if action == ExecutionAction.ADD:
        if current <= 0:
            raise ExecutionQuantityError("ADD requires an existing position")
        if not isinstance(add_plan, dict):
            raise ExecutionQuantityError("ADD requires AddPlanV2")
        requested = _planned_add_requested_shares(add_plan, price=price, equity=equity)
        resulting = add_plan.get("resulting_position_cap") or {}
        resulting_cap = _positive_int(resulting.get("shares"))
        if resulting_cap is None:
            raise ExecutionQuantityError("ADD requires resulting_position_cap.shares")
        remaining_to_plan = resulting_cap - current
        remaining_to_risk = risk_target - current
        quantity = min(requested, remaining_to_plan, remaining_to_risk)
        if quantity <= 0:
            raise ExecutionQuantityError("ADD has no positive shares within position/risk caps")
        return int(quantity)
    if action == ExecutionAction.FULL:
        if current <= 0:
            raise ExecutionQuantityError("FULL requires an existing position")
        if current > risk_target:
            raise ExecutionQuantityError("FULL cannot maintain a position above the current RiskEngine target")
        # Zero means the existing position is already at the Python-approved
        # full target. FULL may therefore be a maintain-state recommendation.
        return int(risk_target - current)
    if action == ExecutionAction.TRIM:
        if current <= 0:
            raise ExecutionQuantityError("TRIM requires an existing position")
        reduction = current - risk_target
        if reduction <= 0:
            raise ExecutionQuantityError("TRIM requires a lower Python RiskEngine target")
        if reduction >= current:
            raise ExecutionQuantityError("TRIM cannot liquidate the full position; use EXIT")
        return int(reduction)
    if action == ExecutionAction.EXIT:
        if current <= 0:
            raise ExecutionQuantityError("EXIT requires an existing position")
        return current
    raise ExecutionQuantityError(f"unsupported ExecutionAction: {action}")

