"""End-to-end semantic compatibility layer for V8 MAIN / V8 NEXT.

V2.1 remains a compatibility API, but candidate-conservation authority is now
implemented by the canonical V2.2 semantic core.  Keeping one derivation path
prevents the legacy helper from reintroducing engineering/evidence failure as
investment rejection when imported directly or through a different bootstrap
order.
"""
from __future__ import annotations

from typing import Any

from . import hunt_integrity_v18 as v18
from . import runtime as runtime_module
from . import v8_next_successor as successor
from . import v8_primary

V8_SYSTEM_SEMANTICS_VERSION = "V8_SYSTEM_SEMANTICS_V2.1"
_INSTALLED = False
_VALID_RESEARCH_GRADES = {"A", "A-", "B+", "B", "EXCLUDE"}

# PRE-A is downstream of Step18 and therefore must be blind input to
# certification.  These legacy sets remain populated for compatibility; V2.2
# owns the canonical complete forbidden-key registry.
_PRE_A_BLIND_KEYS = {
    "pre_a_status", "pre_a_metadata", "promotion_readiness", "a_trajectory",
    "pre_a_readiness", "trajectory_status", "pre_a_high", "pre_a_candidate",
}
v8_primary._DISCOVERY_ONLY_KEYS.update(_PRE_A_BLIND_KEYS)
v8_primary._DISCOVERY_SCORE_KEYS.update(_PRE_A_BLIND_KEYS)
v8_primary._BLIND_KEYS.update(_PRE_A_BLIND_KEYS)


def validated_research_grade(payload: dict[str, Any] | None) -> str | None:
    """Return every valid completed Step18 conclusion, including EXCLUDE."""
    if not isinstance(payload, dict):
        return None
    source = str(payload.get("source_sha256") or "")
    if source == successor.V8_NEXT_POLICY_HASH:
        grade, failures = successor.validate_v8_next_certification(payload)
        if failures or grade not in _VALID_RESEARCH_GRADES:
            return None
        return grade
    if source == getattr(v18, "STEP18_SOURCE_SHA256", ""):
        if payload.get("grade_authority") not in {True, "V8_STEP18_CANONICAL"}:
            return None
        if payload.get("discovery_score_used") not in {False, "NO", "FALSE"}:
            return None
        grade = str(payload.get("research_grade") or payload.get("grade") or "").upper()
        return grade if grade in _VALID_RESEARCH_GRADES else None
    return None


def certification_terminal_state(
    grade: str | None,
    *,
    step20_route: str | None,
    expectation_gap_pass: bool,
    has_evidence_debt: bool,
) -> tuple[str, str]:
    """Compatibility mapping; evaluation completeness and grade are orthogonal."""
    route = str(step20_route or "")
    if grade in _VALID_RESEARCH_GRADES:
        if route != "PASS":
            return "NOT_EVALUATED", f"V8_STEP20_{route or 'MISSING'}"
        if grade in {"A", "A-"}:
            return "PASS", f"V8_CERTIFICATION_{grade}"
        if grade == "B+":
            return "NEXT_STAGE", "V8_CERTIFICATION_B_PLUS_PRE_A"
        if grade == "B":
            return "NEXT_STAGE", "V8_CERTIFICATION_B_WATCH"
        return "REJECT", "V8_CERTIFICATION_EXCLUDE"
    if has_evidence_debt:
        return "EVIDENCE_DEBT", "UNRESOLVED_EVIDENCE_DEBT"
    if expectation_gap_pass:
        return "NOT_EVALUATED", "V8_CERTIFICATION_MISSING_OR_INVALID"
    return "NOT_EVALUATED", "NO_TERMINAL_STATE"


def candidate_conservation_v21(self: Any, run_id: str) -> list[dict[str, Any]]:
    """Delegate to the single canonical V2.2 conservation implementation.

    Import is deliberately local to avoid a module-load cycle: V2.2 imports
    ``validated_research_grade`` from this compatibility module.
    """
    from .v8_semantic_core_v22 import candidate_conservation_v22
    return candidate_conservation_v22(self, run_id)


def install_v8_system_semantics_v21() -> type:
    """Install compatibility hooks in place; no outer authority class."""
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_system_semantics_version", None) == V8_SYSTEM_SEMANTICS_VERSION:
        return current
    v18._certification_grade = validated_research_grade  # type: ignore[assignment]
    current._candidate_conservation = candidate_conservation_v21  # type: ignore[attr-defined,assignment]
    current.v8_system_semantics_version = V8_SYSTEM_SEMANTICS_VERSION
    _INSTALLED = True
    return current
