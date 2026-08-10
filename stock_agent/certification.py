from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import (
    AnalysisStatus,
    CertificationResult,
    CertificationStatus,
    ExecutionStatus,
    SideEffectStatus,
)


NO_CERTIFIED_ACTION = "NO_CERTIFIED_ACTION"


@dataclass(frozen=True)
class DataRequirement:
    key: str
    tier: str
    material: bool = True


@dataclass
class RequiredDataAssessment:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class RequiredDataContract:
    """Deterministic certification prerequisites, independent from LLM judgement."""

    HIGH_BETA_ANALYZE = (
        DataRequirement("current_valid_price", "REQUIRED"),
        DataRequirement("latest_material_periodic_filing", "REQUIRED"),
        DataRequirement("material_offering_state", "REQUIRED"),
        DataRequirement("valid_shares_outstanding", "REQUIRED"),
        DataRequirement("latest_material_8k", "IMPORTANT"),
        DataRequirement("market_regime", "IMPORTANT"),
    )

    @staticmethod
    def assess(
        market: Any,
        evidence: list[Any],
        capital_structure: dict[str, Any] | None,
        *,
        live_mode: bool,
        sizing_requested: bool,
        portfolio_state: dict[str, Any] | None = None,
    ) -> RequiredDataAssessment:
        result = RequiredDataAssessment()
        if market is None or float(getattr(market, "current", 0) or 0) <= 0:
            result.failures.append("CURRENT_VALID_PRICE_MISSING")
        if str(getattr(market, "data_quality", "UNKNOWN")) not in {"OK", "COMPLETE"}:
            result.failures.append("MARKET_DATA_NOT_CERTIFIABLE")
        for field_name, accepted, reason in (
            ("transport_status", {"OK"}, "MARKET_TRANSPORT_FAILED"),
            ("quote_freshness", {"FRESH"}, "MARKET_QUOTE_STALE"),
            ("candle_freshness", {"FRESH"}, "MARKET_CANDLE_STALE"),
            ("bar_completeness", {"COMPLETE"}, "MARKET_BAR_INCOMPLETE"),
            ("volume_validity", {"VALID"}, "MARKET_VOLUME_INVALID"),
            ("indicator_readiness", {"READY"}, "MARKET_INDICATORS_UNCERTIFIED"),
        ):
            if str(getattr(market, field_name, "UNKNOWN")) not in accepted:
                result.failures.append(reason)
        if live_mode and bool(getattr(market, "is_mock", False)):
            result.failures.append("LIVE_RUN_RECEIVED_MOCK_MARKET_DATA")

        if live_mode:
            periodic = [item for item in evidence if getattr(item, "document_type", "") in {"10-Q", "10-K"}]
            latest = max(periodic, key=lambda item: getattr(item, "filed_at", "") or getattr(item, "published_at", ""), default=None)
            if latest is None:
                result.failures.append("LATEST_MATERIAL_PERIODIC_FILING_MISSING")
            elif str(getattr(latest, "lifecycle_status", "DISCOVERED")) != "READY_FOR_ANALYSIS":
                result.failures.append("LATEST_MATERIAL_PERIODIC_FILING_NOT_READY")

            capital = capital_structure or {}
            if capital.get("integrity_conflicts"):
                result.failures.append("MATERIAL_CAPITAL_STRUCTURE_CONFLICT")
            if capital.get("shares_outstanding") is None:
                result.failures.append("VALID_SHARES_OUTSTANDING_MISSING")
            unknown = set(capital.get("unknown_fields") or [])
            if {"atm_capacity", "recent_atm_usage"}.issubset(unknown):
                result.failures.append("MATERIAL_OFFERING_STATE_UNRESOLVED")

        if sizing_requested and not portfolio_state:
            result.failures.append("PORTFOLIO_STATE_REQUIRED_FOR_SIZING")
        if not any(getattr(item, "document_type", "") == "8-K" for item in evidence):
            result.warnings.append("LATEST_MATERIAL_8K_NOT_AVAILABLE")
        return result


class CertificationEngine:
    def evaluate(
        self,
        *,
        run_id: str,
        debate_status: str,
        market: Any,
        evidence: list[Any],
        capital_structure: dict[str, Any] | None,
        live_mode: bool,
        critical_open_issues: int = 0,
        unresolved_must_answer: int = 0,
        claim_validation_passed: bool = True,
        system_integrity_ok: bool = True,
        sizing_requested: bool = False,
        portfolio_state: dict[str, Any] | None = None,
    ) -> CertificationResult:
        data = RequiredDataContract.assess(
            market,
            evidence,
            capital_structure,
            live_mode=live_mode,
            sizing_requested=sizing_requested,
            portfolio_state=portfolio_state,
        )
        reasons = list(data.failures)
        analysis_status = AnalysisStatus.COMPLETED.value
        status = CertificationStatus.CERTIFIED.value

        if not system_integrity_ok:
            status = CertificationStatus.BLOCKED_SYSTEM_INTEGRITY.value
            reasons.append("SYSTEM_INTEGRITY_CHECK_FAILED")
            analysis_status = AnalysisStatus.BLOCKED.value
        elif any("MARKET" in item or "PRICE" in item for item in data.failures):
            status = CertificationStatus.BLOCKED_MARKET_DATA.value
            analysis_status = AnalysisStatus.BLOCKED.value
        elif any("CONFLICT" in item for item in data.failures):
            status = CertificationStatus.BLOCKED_DATA_CONFLICT.value
            analysis_status = AnalysisStatus.BLOCKED.value
        elif data.failures:
            status = CertificationStatus.BLOCKED_DATA_MISSING.value
            analysis_status = AnalysisStatus.BLOCKED.value
        elif not claim_validation_passed:
            status = CertificationStatus.BLOCKED_EVIDENCE.value
            reasons.append("CLAIM_EVIDENCE_VALIDATION_FAILED")
            analysis_status = AnalysisStatus.BLOCKED.value
        elif unresolved_must_answer > 0:
            status = CertificationStatus.BLOCKED_EVIDENCE.value
            reasons.append("MUST_ANSWER_EVIDENCE_REQUEST_UNRESOLVED")
            analysis_status = AnalysisStatus.BLOCKED.value
        elif debate_status == "DEADLOCK" or critical_open_issues > 0:
            status = CertificationStatus.BLOCKED_DEBATE.value
            reasons.append("DEBATE_DEADLOCK" if debate_status == "DEADLOCK" else "CRITICAL_ISSUE_UNRESOLVED")
            analysis_status = AnalysisStatus.DEADLOCK.value if debate_status == "DEADLOCK" else AnalysisStatus.BLOCKED.value
        elif debate_status not in {"CONSENSUS_REACHED", "FINAL_CONSENSUS"}:
            status = CertificationStatus.BLOCKED_DEBATE.value
            reasons.append("FINAL_CONSENSUS_NOT_REACHED")
            analysis_status = AnalysisStatus.BLOCKED.value

        certified = status == CertificationStatus.CERTIFIED.value
        return CertificationResult(
            run_id=run_id,
            execution_status=ExecutionStatus.SUCCESS.value,
            analysis_status=analysis_status,
            certification_status=status,
            side_effect_status=(SideEffectStatus.AUTHORIZED_PENDING.value if certified and sizing_requested
                                else SideEffectStatus.NOT_AUTHORIZED.value),
            action="PENDING_CERTIFIED_DECISION" if certified else NO_CERTIFIED_ACTION,
            reason_codes=list(dict.fromkeys(reasons)),
            required_data_failures=data.failures,
            important_data_warnings=data.warnings,
            decision_confidence=None,
            trade_plan_status="PENDING" if certified else "WITHHELD",
            position_sizing_status="PENDING" if certified and sizing_requested else "WITHHELD",
        )
