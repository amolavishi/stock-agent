from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiscoveryBudgetGuard:
    limits: dict[str, int | float] = field(default_factory=dict)
    used: dict[str, int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # ``None`` in YAML means "unbounded"; keeping it in the numeric map
        # makes the audit snapshot itself fail while comparing usage to limits.
        self.limits = {str(key): value for key, value in self.limits.items()
                       if value is not None}

    def consume(self, name: str, amount: int | float = 1) -> bool:
        limit = self.limits.get(name)
        current = self.used.get(name, 0) + amount
        self.used[name] = current
        return limit is None or current <= limit

    def allow(self, name: str, amount: int | float = 1) -> bool:
        limit = self.limits.get(name)
        return limit is None or self.used.get(name, 0) + amount <= limit

    def snapshot(self) -> dict[str, Any]:
        exceeded = [name for name, value in self.used.items()
                    if name in self.limits and value > self.limits[name]]
        return {"limits": dict(self.limits), "used": dict(self.used),
                "exceeded": exceeded, "status": "BLOCKED_COST" if exceeded else "OK"}
