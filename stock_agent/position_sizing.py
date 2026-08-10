from __future__ import annotations

import math

from .schemas import PositionSize, TradePlan


class PositionSizingError(ValueError):
    pass


class PositionSizingEngine:
    """Deterministic PAPER sizing. An LLM never chooses the quantity."""

    def __init__(self, max_position_pct: float = 10.0, max_loss_pct: float = 0.75,
                 max_total_exposure_pct: float = 60.0,
                 max_sector_exposure_pct: float = 25.0):
        self.max_position_pct = max_position_pct
        self.max_loss_pct = max_loss_pct
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_sector_exposure_pct = max_sector_exposure_pct

    def calculate(self, plan: TradePlan, equity_usd: float, cash_usd: float) -> PositionSize:
        if equity_usd <= 0 or cash_usd < 0 or plan.entry_price <= 0:
            raise PositionSizingError("invalid portfolio or entry price")
        risk_per_share = plan.entry_price - plan.stop_price
        if risk_per_share <= 0:
            raise PositionSizingError("stop price must be below entry price")
        loss_budget = equity_usd * self.max_loss_pct / 100
        by_loss = math.floor(loss_budget / risk_per_share)
        max_notional = min(cash_usd, equity_usd * self.max_position_pct / 100)
        by_notional = math.floor(max_notional / plan.entry_price)
        quantity = max(0, min(by_loss, by_notional))
        limiting = "LOSS_BUDGET" if by_loss <= by_notional else "POSITION_OR_CASH_CAP"
        notional = round(quantity * plan.entry_price, 2)
        return PositionSize(quantity, notional, round(loss_budget, 2),
                            round(notional / equity_usd * 100, 4), limiting,
                            initial_capital_at_risk_usd=round(quantity * risk_per_share, 2),
                            gross_exposure_usd=notional)

    def calculate_for_account(self, plan: TradePlan, account: dict,
                              sector: str = "UNKNOWN") -> PositionSize:
        equity = float(account["equity"])
        cash = float(account.get("available_cash",
                                 float(account["cash"]) - float(account.get("reserved_cash", 0))))
        if equity <= 0 or cash < 0:
            raise PositionSizingError("invalid PAPER account state")
        risk_per_share = plan.entry_price - plan.stop_price
        if risk_per_share <= 0:
            raise PositionSizingError("stop price must be below entry price")
        risk_budget = float(account.get("risk_budget", equity * self.max_loss_pct / 100))
        open_risk = float(account.get("risk_budget_used", 0))
        pending_risk = float(account.get("pending_committed_risk", 0))
        portfolio_risk_used = open_risk + pending_risk
        loss_budget = max(0.0, risk_budget - portfolio_risk_used)
        by_loss = math.floor(loss_budget / risk_per_share)
        single_cap = equity * self.max_position_pct / 100
        total_remaining = max(0.0, equity * self.max_total_exposure_pct / 100
                              - float(account.get("current_exposure", 0))
                              - float(account.get("reserved_cash", 0)))
        sector_used = float(account.get("sector_exposure", {}).get(sector, 0))
        sector_remaining = max(0.0, equity * self.max_sector_exposure_pct / 100 - sector_used)
        limits = {
            "AVAILABLE_CASH": cash,
            "SINGLE_POSITION_CAP": single_cap,
            "TOTAL_EXPOSURE_CAP": total_remaining,
            "SECTOR_EXPOSURE_CAP": sector_remaining,
        }
        limiting_name, max_notional = min(limits.items(), key=lambda item: item[1])
        by_notional = math.floor(max_notional / plan.entry_price)
        quantity = max(0, min(by_loss, by_notional))
        limiting = "LOSS_BUDGET" if by_loss <= by_notional else limiting_name
        notional = round(quantity * plan.entry_price, 2)
        return PositionSize(quantity, notional, round(loss_budget, 2),
                            round(notional / equity * 100, 4), limiting,
                            str(account.get("account_id", "PAPER_DEFAULT")), round(cash, 2),
                            round(float(account.get("current_exposure", 0)), 2),
                            round(sector_used, 2),
                            initial_capital_at_risk_usd=round(quantity * risk_per_share, 2),
                            current_mark_to_stop_risk_usd=0.0,
                            pending_committed_risk_usd=round(
                                pending_risk, 2),
                            gross_exposure_usd=round(
                                float(account.get("current_exposure", 0)) + notional, 2),
                            risk_rule_version="portfolio_heat_v1",
                            portfolio_risk_used_usd=round(portfolio_risk_used, 2),
                            risk_budget_remaining_usd=round(loss_budget, 2))
