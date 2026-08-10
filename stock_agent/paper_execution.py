from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from .database import Database


@dataclass(frozen=True)
class PaperValidationResult:
    valid: bool
    action: str
    reason_codes: list[str] = field(default_factory=list)

    def __iter__(self) -> Iterator[Any]:
        yield self.valid
        yield "OK" if self.valid else ",".join(self.reason_codes)


class CanonicalPaperValidator:
    """Single deterministic validation path for every PAPER financial effect."""

    ACTIVE_ORDER_STATES = {"PENDING", "TRIGGERED", "REVALIDATING"}

    def __init__(self, database: Database, max_total_exposure_pct: float = 60.0,
                 max_sector_exposure_pct: float = 25.0):
        self.db = database
        self.max_total_exposure_pct = float(max_total_exposure_pct)
        self.max_sector_exposure_pct = float(max_sector_exposure_pct)

    def has_open_position(self, ticker: str, account_id: str = "PAPER_DEFAULT") -> bool:
        with self.db.connect() as connection:
            row = connection.execute("""SELECT 1 FROM portfolio_positions
                WHERE ticker=? AND account_id=? AND status='OPEN' AND quantity>0 LIMIT 1""",
                (ticker.upper(), account_id)).fetchone()
        return row is not None

    def canonicalize_action(self, action: str, ticker: str,
                            account_id: str = "PAPER_DEFAULT") -> PaperValidationResult:
        action = str(action).upper()
        has_position = self.has_open_position(ticker, account_id)
        if action == "HOLD" and not has_position:
            return PaperValidationResult(False, "WAIT", ["HOLD_REQUIRES_OPEN_POSITION"])
        if action in {"TRIM", "SELL"} and not has_position:
            return PaperValidationResult(False, "NO_CERTIFIED_ACTION",
                                         [f"{action}_REQUIRES_OPEN_POSITION"])
        return PaperValidationResult(True, action, [])

    def validate_conditional(self, order: Any, current_price: float, *,
                             certification_status: str,
                             price_status: str) -> PaperValidationResult:
        reasons: list[str] = []
        if certification_status != "CERTIFIED":
            reasons.append("CERTIFICATION_REQUIRED")
        if price_status != "FRESH":
            reasons.append("PRICE_NOT_FRESH")
        if current_price <= 0:
            reasons.append("INVALID_PRICE")
        invalidation = float(order["invalidation_price"] or 0)
        if invalidation <= 0 or current_price <= invalidation:
            reasons.append("ENTRY_NOT_ABOVE_INVALIDATION")
        trigger = float(order["trigger_price"] or order["limit_price"] or 0)
        if trigger <= 0 or current_price > trigger:
            reasons.append("TRIGGER_NOT_SATISFIED")

        account = self.db.paper_account_state(str(order["account_id"]))
        notional = float(order["quantity"]) * current_price
        if notional > float(account["cash"]) + 0.01:
            reasons.append("INSUFFICIENT_CASH")
        projected = float(account["current_exposure"]) + notional
        if projected > float(account["equity"]) * self.max_total_exposure_pct / 100 + 0.01:
            reasons.append("TOTAL_EXPOSURE_LIMIT")
        sector = str(order["sector"] or "UNKNOWN")
        projected_sector = float(account["sector_exposure"].get(sector, 0)) + notional
        if projected_sector > float(account["equity"]) * self.max_sector_exposure_pct / 100 + 0.01:
            reasons.append("SECTOR_EXPOSURE_LIMIT")
        return PaperValidationResult(not reasons, "BUY" if not reasons else "NO_CERTIFIED_ACTION",
                                     reasons)
