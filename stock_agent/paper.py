from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
import uuid

from .database import Database
from .schemas import InvestmentDecision, MarketSnapshot, PositionSize, now_iso


@dataclass
class PerformanceMeasurement:
    horizon_days: int
    return_pct: float
    qqq_alpha: float | None
    iwm_alpha: float | None
    sector_alpha: float | None
    mfe: float
    mae: float
    stop_hit: bool
    target1_hit: bool
    target2_hit: bool


class PaperPortfolio:
    def __init__(self, database: Database):
        self.db = database

    def plan_effect(self, decision: InvestmentDecision, size: PositionSize,
                    sector: str = "UNKNOWN", horizon: str = "1-2M") -> dict:
        timestamp = now_iso()
        risk_per_share = round(decision.trade_plan.risk_per_share, 4)
        operation_key = f"paper:{decision.run_id}:{decision.decision}:{decision.ticker}"
        effect = {
            "account_id": size.account_id or "PAPER_DEFAULT", "run_id": decision.run_id,
            "ticker": decision.ticker, "timestamp": timestamp, "sector": sector or "UNKNOWN",
            "quantity": size.quantity, "price": decision.trade_plan.entry_price,
            "notional_usd": size.notional_usd,
            "prediction": {
                "prediction_id": f"PRED_{decision.run_id}", "run_id": decision.run_id,
                "ticker": decision.ticker, "decision": decision.decision,
                "confidence": decision.confidence, "reference_price": decision.trade_plan.entry_price,
                "horizon": horizon,
            },
            "action": "PREDICTION_ONLY",
            "financial_operation_key": operation_key,
            "stop_price": decision.trade_plan.stop_price,
            "risk_per_share": risk_per_share,
            "risk_provenance": {
                "status": "KNOWN",
                "method": "TRADE_PLAN_ENTRY_MINUS_STOP",
                "source_run_id": decision.run_id,
                "source_operation_key": operation_key,
                "entry_price": decision.trade_plan.entry_price,
                "stop_price": decision.trade_plan.stop_price,
                "risk_per_share": risk_per_share,
                "quantity": size.quantity,
                "risk_usd": round(max(0, size.quantity) * risk_per_share, 2),
            },
        }
        if size.quantity <= 0:
            if decision.decision not in {"SELL", "TRIM"}:
                return effect
        if decision.decision == "BUY":
            effect["action"] = "BUY"
        elif decision.decision == "CONDITIONAL_BUY":
            effect.update({
                "action": "CONDITIONAL_ORDER",
                "order_id": f"ORDER_{decision.run_id}",
                "trigger_price": decision.trade_plan.preferred_price_max,
                "valid_until": (datetime.now(timezone.utc) + timedelta(days=40)).isoformat(),
                "invalidation_price": decision.trade_plan.stop_price,
            })
        elif decision.decision in {"SELL", "TRIM"}:
            with self.db.connect() as c:
                row = c.execute("""SELECT quantity,average_price FROM portfolio_positions
                    WHERE ticker=? AND account_id=? AND status='OPEN'""",
                    (decision.ticker, effect["account_id"])).fetchone()
            if row:
                held = int(float(row["quantity"]))
                quantity = held if decision.decision == "SELL" else max(1, held // 2)
                effect.update({"action": decision.decision, "quantity": quantity,
                               "notional_usd": round(quantity * decision.trade_plan.entry_price, 2),
                               "average_price": float(row["average_price"])})
        return effect

    def enter(self, decision: InvestmentDecision, size: PositionSize) -> bool:
        """Legacy tool entry, now respecting conditional-order semantics."""
        effect = self.plan_effect(decision, size)
        with self.db.connect() as c:
            self.db._apply_paper_effect(c, effect)
        return effect["action"] in {"BUY", "CONDITIONAL_ORDER"}

    def save_measurement(self, run_id: str, measurement: PerformanceMeasurement) -> None:
        with self.db.connect() as c:
            c.execute("""INSERT INTO paper_performance VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id,horizon_days) DO UPDATE SET
                return_pct=excluded.return_pct,qqq_alpha=excluded.qqq_alpha,
                iwm_alpha=excluded.iwm_alpha,sector_alpha=excluded.sector_alpha,
                mfe=excluded.mfe,mae=excluded.mae,stop_hit=excluded.stop_hit,
                target1_hit=excluded.target1_hit,target2_hit=excluded.target2_hit,
                measured_at=excluded.measured_at""",
                (run_id, measurement.horizon_days, measurement.return_pct, measurement.qqq_alpha,
                 measurement.iwm_alpha, measurement.sector_alpha, measurement.mfe, measurement.mae,
                 int(measurement.stop_hit), int(measurement.target1_hit), int(measurement.target2_hit), now_iso()))

    def evaluate_pending_orders(self, ticker: str, current_price: float,
            revalidate: Callable[[Any, float], Any] | None = None) -> list[str]:
        """Trigger orders, then require canonical BUY revalidation before an atomic fill."""
        filled: list[str] = []
        timestamp = now_iso()
        with self.db.connect() as c:
            rows = c.execute("""SELECT * FROM paper_orders WHERE ticker=?
                AND status IN ('PENDING','TRIGGERED','REVALIDATING')
                ORDER BY created_at""", (ticker.upper(),)).fetchall()
            for row in rows:
                if row["valid_until"] and row["valid_until"] < timestamp:
                    self.db.release_paper_reservation(
                        c, row["order_id"], "EXPIRED", "VALID_UNTIL_ELAPSED", timestamp)
                    continue
                if current_price > float(row["trigger_price"] or row["limit_price"]):
                    continue
                c.execute("""UPDATE paper_orders SET status='TRIGGERED',triggered_at=COALESCE(
                    triggered_at,?),updated_at=? WHERE order_id=?""",
                    (timestamp, timestamp, row["order_id"]))
                c.execute("""UPDATE paper_orders SET status='REVALIDATING',updated_at=?
                    WHERE order_id=?""", (timestamp, row["order_id"]))
                if revalidate is None:
                    continue
                validation = revalidate(row, current_price)
                if hasattr(validation, "valid"):
                    valid = bool(validation.valid)
                    reason = "OK" if valid else ",".join(validation.reason_codes)
                else:
                    valid, reason = validation
                if not valid:
                    self.db.release_paper_reservation(
                        c, row["order_id"], "INVALIDATED", str(reason), timestamp)
                    continue
                if self.db.fill_conditional_order(c, row, current_price, timestamp):
                    filled.append(row["order_id"])
        return filled

    @staticmethod
    def measure(entry: float, closes: list[float], highs: list[float], lows: list[float],
                stop: float, target1: float, target2: float, benchmark_returns: dict[str, float] | None = None
                ) -> PerformanceMeasurement:
        if not closes or not (len(closes) == len(highs) == len(lows)):
            raise ValueError("performance price series is empty or inconsistent")
        absolute = (closes[-1] / entry - 1) * 100
        bench = benchmark_returns or {}
        return PerformanceMeasurement(len(closes), round(absolute, 4),
            round(absolute - bench["QQQ"], 4) if "QQQ" in bench else None,
            round(absolute - bench["IWM"], 4) if "IWM" in bench else None,
            round(absolute - bench["SECTOR"], 4) if "SECTOR" in bench else None,
            round((max(highs) / entry - 1) * 100, 4), round((min(lows) / entry - 1) * 100, 4),
            min(lows) <= stop, max(highs) >= target1, max(highs) >= target2)
