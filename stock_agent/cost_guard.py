from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostGuardDecision:
    action: str
    reason: str
    current_cost_usd: float
    soft_limit_usd: float
    hard_limit_usd: float


class CostGuard:
    def __init__(self, config: dict):
        self.mode = str(config.get("mode", "WARN")).upper()
        self.soft = max(0.0, float(config.get("soft_cost_limit_usd", 0)))
        self.hard = max(0.0, float(config.get("hard_cost_limit_usd", 0)))

    def evaluate(self, current_cost_usd: float, minimum_rounds_met: bool) -> CostGuardDecision:
        current = max(0.0, float(current_cost_usd))
        if self.hard > 0 and current >= self.hard:
            action = "STOP_COMPLETE" if minimum_rounds_met else "STOP_INCOMPLETE"
            return CostGuardDecision(action, "BUDGET_LIMIT_REACHED", current, self.soft, self.hard)
        if self.soft > 0 and current >= self.soft:
            return CostGuardDecision("WARN", "SOFT_COST_LIMIT_REACHED", current, self.soft, self.hard)
        return CostGuardDecision("CONTINUE", "WITHIN_BUDGET", current, self.soft, self.hard)
