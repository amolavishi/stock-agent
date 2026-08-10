from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .claim_validation import validate_claim_evidence
from .schemas import Decision, EvidenceItem, RiskResult, TradePlan


class FinalGuardError(ValueError):
    pass


class FinalGuard:
    """Final deterministic boundary. Hermes cannot override these results."""

    @staticmethod
    def validate_trade_plan(plan: TradePlan) -> dict[str, Any]:
        errors: list[str] = []
        if plan.entry_price <= 0:
            errors.append("entry_price must be positive")
        if plan.stop_price >= plan.entry_price:
            errors.append("stop_price must be below entry_price")
        if plan.target_1 <= plan.entry_price:
            errors.append("target_1 must be above entry_price")
        if plan.target_2 < plan.target_1:
            errors.append("target_2 must be at or above target_1")
        if abs(plan.reward_risk - plan.rr_1) > 0.02:
            errors.append("reward_risk does not match the shared TradePlan")
        return {"valid": not errors, "errors": errors, "trade_plan": asdict(plan)}

    @staticmethod
    def validate_claims(claims: list[dict[str, Any]], evidence: list[EvidenceItem]) -> dict[str, Any]:
        try:
            validate_claim_evidence(claims, evidence)
            return {"valid": True, "errors": []}
        except Exception as exc:
            return {"valid": False, "errors": [str(exc)]}

    @staticmethod
    def validate_final(chairman_output: dict[str, Any], risk: RiskResult,
                       claims_valid: bool, trade_plan_valid: bool,
                       debate_status: str = "CONSENSUS_REACHED",
                       critical_open_issues: int = 0,
                       data_quality: str = "OK",
                       critical_capital_unknown: bool = False,
                       has_open_position: bool = False) -> dict[str, Any]:
        allowed = {item.value for item in Decision}
        proposed = str(chairman_output.get("decision", ""))
        errors: list[str] = []
        if proposed not in allowed:
            errors.append(f"unsupported decision: {proposed}")
        if proposed == "HOLD" and not has_open_position:
            errors.append("HOLD_REQUIRES_OPEN_POSITION")
        if not claims_valid:
            errors.append("claim-evidence validation failed")
        if not trade_plan_valid:
            errors.append("trade plan validation failed")
        quality_gate = (debate_status == "DEADLOCK" or critical_open_issues > 0
                        or data_quality not in {"OK", "PARTIAL"} or critical_capital_unknown)
        if not risk.hard_filter_pass or risk.risk_decision == "EXCLUDE":
            final = "EXCLUDE"
            overridden = proposed != "EXCLUDE"
        elif risk.risk_decision == "WAIT":
            final = "WAIT"
            overridden = proposed not in {"WAIT", "EXCLUDE"}
        elif quality_gate and proposed in {"BUY", "CONDITIONAL_BUY"}:
            final = "WAIT"
            overridden = True
        elif proposed == "HOLD" and not has_open_position:
            final = "WAIT"
            overridden = True
        elif errors:
            final = "EXCLUDE"
            overridden = True
        else:
            final = proposed
            overridden = False
        return {"valid": not errors, "proposed_decision": proposed,
                "final_decision": final, "risk_override_applied": overridden,
                "errors": errors, "quality_gate_applied": quality_gate,
                "debate_status": debate_status,
                "critical_open_issues": critical_open_issues,
                "critical_capital_unknown": critical_capital_unknown}
