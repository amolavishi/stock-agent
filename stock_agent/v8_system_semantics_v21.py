"""End-to-end semantic state guard for V8 MAIN / V8 NEXT.

This module fixes a class of contradictions that only becomes visible when a
candidate is followed from Discovery through Step18/20 and into Shadow/PRE-A:
a valid non-executable Research Grade (B+, B, EXCLUDE) is still a completed
investment evaluation and must never be rewritten as NOT_EVALUATED merely
because it is not A/A-.

Authority remains unchanged:
- Step18 is the sole Research Grade writer.
- Step20 is a pure validator.
- only A/A- can qualify for execution.
- B+ routes to PRE-A/re-certification, B to watch, EXCLUDE to rejection.
- missing/invalid certification or engineering failure remains non-evaluable.
"""
from __future__ import annotations

import json
from typing import Any

from . import hunt_integrity_v18 as v18
from . import runtime as runtime_module
from . import v8_next_certification as cert
from . import v8_next_successor as successor
from .models import GateDecision

V8_SYSTEM_SEMANTICS_VERSION = "V8_SYSTEM_SEMANTICS_V2.1"
_INSTALLED = False
_VALID_RESEARCH_GRADES = {"A", "A-", "B+", "B", "EXCLUDE"}
_INCOMPLETE_STATES = {
    "ENGINEERING_FAILURE", "PROVIDER_FAILURE", "NOT_EVALUATED",
    "EVIDENCE_DEBT", "SOURCE_EXHAUSTED",
}


def validated_research_grade(payload: dict[str, Any] | None) -> str | None:
    """Return all valid Step18 conclusions, including EXCLUDE.

    Parsing a grade is not the same as qualifying it for execution.  The latter
    continues to be restricted to A/A- by the qualification gate.
    """
    if not isinstance(payload, dict):
        return None
    source = str(payload.get("source_sha256") or "")
    if source == successor.V8_NEXT_POLICY_HASH:
        grade, failures = successor.validate_v8_next_certification(payload)
        if failures or grade not in _VALID_RESEARCH_GRADES:
            return None
        return grade

    # Frozen legacy compatibility.  Keep the same authority requirements but
    # do not erase a valid legacy EXCLUDE merely because it is non-executable.
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
    """Map evaluation completeness and investment conclusion without conflating them."""
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


def _latest_payload(store: Any, run_id: str, stage: str, sid: str) -> dict[str, Any] | None:
    row = store.get_stage_result(run_id, stage, sid)
    if not row or row.get("status") != "SUCCEEDED":
        return None
    try:
        value = json.loads(row.get("result_json") or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def candidate_conservation_v21(self: Any, run_id: str) -> list[dict[str, Any]]:
    """Conserve every discovered candidate using orthogonal evaluation/grade semantics."""
    discovery_row = self.store.get_stage_result(run_id, "STOCK_DISCOVERY", None)
    if not discovery_row or discovery_row.get("status") != "SUCCEEDED":
        return []
    try:
        discovery = json.loads(discovery_row.get("result_json") or "{}")
    except (TypeError, ValueError):
        return []
    candidates = discovery.get("candidates") if isinstance(discovery, dict) else []
    ledger: list[dict[str, Any]] = []

    for candidate in candidates or []:
        if not isinstance(candidate, dict) or not candidate.get("security_id"):
            continue
        sid = str(candidate["security_id"])
        action = str(candidate.get("recommended_discovery_action") or "EXCLUDE")
        rows = self.store.list_stage_results(run_id, sid)
        values: dict[str, dict[str, Any]] = {}
        dependencies: set[str] = set()
        for row in rows:
            try:
                value = json.loads(row.get("result_json") or "{}")
            except (TypeError, ValueError):
                value = {}
            if isinstance(value, dict):
                values[str(row.get("stage"))] = value
            try:
                dependencies.update(str(item) for item in json.loads(row.get("dependency_ids_json") or "[]"))
            except (TypeError, ValueError):
                pass

        state, reason = "NOT_EVALUATED", "NO_TERMINAL_STATE"
        if action == "EXCLUDE":
            state, reason = "REJECT", "DISCOVERY_EXCLUDE"
        elif action in {"WATCH_STAGE0", "WATCH_RESET"}:
            state, reason = "NEXT_STAGE", action
        elif "CANDIDATE_ENGINEERING_FAILURE" in values or sid in getattr(self, "_v18_candidate_failures", {}):
            failure = values.get("CANDIDATE_ENGINEERING_FAILURE") or getattr(self, "_v18_candidate_failures", {}).get(sid) or {}
            state, reason = "ENGINEERING_FAILURE", str(failure.get("failed_stage") or failure.get("stage") or "CANDIDATE_STAGE")
        elif any(stage in values for stage in ("RESEARCH_PROVIDER_FAILURE", "SEC_PROVIDER_FAILURE", "SEC_STALE_DATA")):
            failed_stage = next(stage for stage in ("RESEARCH_PROVIDER_FAILURE", "SEC_PROVIDER_FAILURE", "SEC_STALE_DATA") if stage in values)
            state, reason = "PROVIDER_FAILURE", failed_stage
        else:
            terminal_gate = False
            for gate_stage in ("STAGE_GATE", "CAPITAL_PRESCREEN_GATE", "CATALYST_GATE", "EXPECTATION_GAP_GATE"):
                value = values.get(gate_stage) or {}
                decision = str(value.get("decision") or "")
                if decision == GateDecision.REJECT.value:
                    state, reason, terminal_gate = "REJECT", gate_stage, True
                    break
                if decision == GateDecision.INSUFFICIENT_EVIDENCE.value and gate_stage in {"CATALYST_GATE", "EXPECTATION_GAP_GATE"}:
                    if values.get("SOURCE_EXHAUSTED"):
                        state, reason = "SOURCE_EXHAUSTED", gate_stage
                    else:
                        state, reason = "NOT_EVALUATED", gate_stage
                    terminal_gate = True
                    break

            if not terminal_gate:
                audit = values.get("ADVERSARIAL_AUDIT") or {}
                if str(audit.get("audit_recommendation") or "") in {"CHALLENGES_CONTINUATION", "AUDIT_EVIDENCE_INCOMPLETE"}:
                    state, reason = "REJECT", "ADVERSARIAL_AUDIT"
                else:
                    certification = values.get(cert.STEP18_STAGE) or _latest_payload(self.store, run_id, cert.STEP18_STAGE, sid)
                    grade = validated_research_grade(certification)
                    validator = values.get(cert.STEP20_STAGE) or _latest_payload(self.store, run_id, cert.STEP20_STAGE, sid) or {}
                    route = str(validator.get("route") or "")
                    expectation_pass = str((values.get("EXPECTATION_GAP_GATE") or {}).get("decision") or "") == GateDecision.PASS.value
                    state, reason = certification_terminal_state(
                        grade,
                        step20_route=route,
                        expectation_gap_pass=expectation_pass,
                        has_evidence_debt=bool(values.get("EVIDENCE_DEBT")),
                    )

        receipt = {
            "state": state,
            "reason": reason,
            "discovery_action": action,
            "security_id": sid,
            "version": V8_SYSTEM_SEMANTICS_VERSION,
            "evaluation_complete": state not in _INCOMPLETE_STATES,
        }
        dep_ids = sorted(dependencies)
        run = self.store.get_run(run_id)
        self.store.record_stage_result(
            run_id, None, "CANDIDATE_CONSERVATION", sid, receipt, dep_ids,
            self.store.dependency_hash(dep_ids, run.rule_set.rule_set_hash, run.context_manifest_hash),
            self.store.current_evidence_epoch_for(dep_ids),
        )
        ledger.append(receipt)

    counts: dict[str, int] = {}
    for item in ledger:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    self.store.record_funnel(run_id, "CANDIDATE_CONSERVATION_TOTAL", len(ledger), {
        "states": counts,
        "version": V8_SYSTEM_SEMANTICS_VERSION,
        "evaluation_and_grade_are_orthogonal": True,
    })
    # Explicitly zero all incomplete state counters not present in this run so
    # an earlier write in the same run cannot leave stale degradation residue.
    for state in sorted(_INCOMPLETE_STATES | set(counts)):
        self.store.record_funnel(run_id, f"CONSERVATION_{state}", counts.get(state, 0), {
            "version": V8_SYSTEM_SEMANTICS_VERSION,
        })
    return ledger


def install_v8_system_semantics_v21() -> type:
    """Patch semantics in place without creating a new outer runtime authority class."""
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_system_semantics_version", None) == V8_SYSTEM_SEMANTICS_VERSION:
        return current

    # V1.8 calls this module-global parser dynamically for qualification,
    # conservation and Shadow rendering.  Returning EXCLUDE here does not make
    # it executable because qualification still explicitly requires A/A-.
    v18._certification_grade = validated_research_grade  # type: ignore[assignment]

    # Assign the corrected method directly to the final composed class.  This
    # preserves the existing final pre-live sentinel class as the outer owner.
    current._candidate_conservation = candidate_conservation_v21  # type: ignore[attr-defined,assignment]
    current.v8_system_semantics_version = V8_SYSTEM_SEMANTICS_VERSION
    _INSTALLED = True
    return current
