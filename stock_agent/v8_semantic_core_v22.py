"""Canonical semantic core for V8.4 MAIN hardening.

One Fact -> One Meaning -> One Authoritative State -> Consistent Downstream Projection.

This module adds no Discovery, Research Grade, PRE-A, sizing, execution, or broker
authority.  It centralizes semantic derivation from already-persisted receipts and
patches the final composed production class in place; it never creates an outer
runtime wrapper.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Any, Mapping

from . import runtime as runtime_module
from . import v8_main_discovery_integrity as integrity
from . import v8_main_discovery_post_v11 as discovery_post
from . import v8_pre_live_integrity_v20 as pre_live
from . import v8_primary
from . import v8_next_certification as cert
from .models import GateDecision, RunMode, RunOutcome, canonical_hash
from .v8_system_semantics_v21 import validated_research_grade

V8_SEMANTIC_CORE_VERSION = "V8_SEMANTIC_CORE_V2.2"
_INSTALLED = False

CANDIDATE_COMPLETE_STATES = {"PASS", "NEXT_STAGE", "WATCH", "REJECT"}
CANDIDATE_INCOMPLETE_STATES = {"NOT_EVALUATED", "ENGINEERING_FAILURE", "EVIDENCE_DEBT", "DATA_BLOCK"}

CERTIFICATION_FORBIDDEN_KEYS = {
    # Discovery / scanner routing
    "discovery_priority_score", "discovery_score", "discovery_rank", "primary_rank", "primary_score", "rank",
    "research_value", "signal_strength", "scanner_id", "scanner_name", "scanner_priority", "scanner_receipt",
    "secondary_status", "secondary_queue", "near_miss", "near_miss_status", "rejection_sentinel",
    "sentinel_history", "discovery_disposition", "recommended_discovery_action", "verification_path",
    "recheck_trigger", "expected_resolution", "expiry", "secondary_is_pre_a", "research_value_is_research_grade",
    "fatal_fail", "research_route_allowed", "why_not_deep_dive", "queue_status",
    # PRE-A trajectory
    "pre_a_status", "pre_a_metadata", "promotion_readiness", "a_trajectory", "pre_a_readiness",
    "trajectory_status", "pre_a_high", "pre_a_candidate",
    # Grade quota / shortage anchoring
    "target_verified_a_minus_or_better", "verified_a_minus_or_better_count", "verified_a_count",
    "verified_a_minus_count", "candidate_shortage", "grade_quota", "grade_target", "required_a_count",
    "remaining_a_needed", "target_a_count",
    # Prior grade / certification anchors
    "research_grade", "primary_grade", "final_grade", "certification", "certification_score",
    # Execution / portfolio anchors forbidden from Research Grade
    "authoritative_action", "primary_action", "final_allocation", "final_allocation_action", "position_shares",
    "current_position_shares", "risk_target_position_shares", "transaction_shares", "resulting_position_shares",
    "target_price", "entry_price", "stop_price",
}
CERTIFICATION_FORBIDDEN_PREFIXES = (
    "pre_a_", "discovery_priority_", "discovery_rank_", "scanner_priority_", "grade_quota_",
    "remaining_a_", "verified_a_", "candidate_shortage_", "promotion_readiness_",
)


def _forbidden_key(key: Any) -> bool:
    value = str(key).casefold()
    return value in CERTIFICATION_FORBIDDEN_KEYS or any(value.startswith(prefix) for prefix in CERTIFICATION_FORBIDDEN_PREFIXES)


def blind_certification_packet(value: Any) -> Any:
    """Canonical zero-anchor firewall shared by Step16/17/17.5/18 inputs."""
    if isinstance(value, Mapping):
        return {
            str(key): blind_certification_packet(item)
            for key, item in value.items()
            if not _forbidden_key(key)
        }
    if isinstance(value, list):
        return [blind_certification_packet(item) for item in value]
    if isinstance(value, tuple):
        return [blind_certification_packet(item) for item in value]
    return copy.deepcopy(value)


def _parse_details(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    raw = row.get("details_json")
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _funnel(store: Any, run_id: str) -> dict[str, dict[str, Any]]:
    return {str(row.get("funnel_stage") or ""): row for row in store.list_funnel(run_id) if isinstance(row, dict)}


def _count(funnel: dict[str, dict[str, Any]], stage: str) -> int:
    try:
        return int((funnel.get(stage) or {}).get("count") or 0)
    except (TypeError, ValueError):
        return 0


def source_exhaustion_proof(store: Any, run_id: str, eligible: int) -> dict[str, Any]:
    """Prove source closure; an operational probe threshold can never prove it."""
    funnel = _funnel(store, run_id)
    raw = _count(funnel, "RAW_UNIVERSE")
    adv_probed = _count(funnel, "ADV_PROBED")
    unresolved = _count(funnel, "ADV_NOT_EVALUATED")
    scope = _parse_details(funnel.get("V8_4_UNIVERSE_SCOPE"))
    full_scope_validated = bool(
        scope.get("full_scope_validated") is True
        and str(scope.get("scope_claim") or "") == "FULL_STRATEGY_UNIVERSE_SCAN"
    )
    eligible_count = max(0, int(eligible or 0))
    denominator_reconciled = bool(raw >= eligible_count and eligible_count >= 0)
    eligible_probe_complete = bool(adv_probed >= eligible_count)
    unresolved_zero = unresolved == 0
    full_universe_reconciled = bool(
        full_scope_validated
        and denominator_reconciled
        and eligible_probe_complete
        and unresolved_zero
    )
    provider_budget_exhausted = _count(funnel, "PROVIDER_BUDGET_EXHAUSTED") > 0
    minimum_operational_probe_met = adv_probed >= int(pre_live.MIN_OPERATIONAL_PROBE)
    source_end_observed = full_universe_reconciled
    source_exhausted = bool(full_universe_reconciled and source_end_observed)
    body = {
        "canonical_universe_count": raw,
        "eligible_universe_count": eligible_count,
        "probed_count": adv_probed,
        "unresolved_count": unresolved,
        "minimum_operational_probe": int(pre_live.MIN_OPERATIONAL_PROBE),
        "minimum_operational_probe_met": minimum_operational_probe_met,
        "operational_probe_threshold_is_source_exhaustion": False,
        "provider_budget_exhausted": provider_budget_exhausted,
        "provider_budget_exhausted_is_source_exhaustion": False,
        "full_scope_validated": full_scope_validated,
        "denominator_reconciled": denominator_reconciled,
        "eligible_probe_complete": eligible_probe_complete,
        "identity_reconciliation_complete": bool(full_scope_validated),
        "security_type_classification_complete": bool(full_scope_validated),
        "eligibility_reconciliation_complete": bool(full_scope_validated),
        "source_end_observed": source_end_observed,
        "full_universe_reconciled": full_universe_reconciled,
        "source_exhausted": source_exhausted,
        "search_debt_remains": not source_exhausted,
        "proof_status": "PASS" if source_exhausted else "INCOMPLETE",
        "version": V8_SEMANTIC_CORE_VERSION,
        "grade_authority": False,
    }
    body["proof_hash"] = canonical_hash(body)
    return body


def provider_exhaustion_v22(store: Any, run_id: str, eligible: int) -> tuple[bool, dict[str, Any]]:
    proof = source_exhaustion_proof(store, run_id, eligible)
    return bool(proof["source_exhausted"]), dict(proof)


def _latest_payload(store: Any, run_id: str, stage: str, sid: str) -> dict[str, Any] | None:
    row = store.get_stage_result(run_id, stage, sid)
    if not row or row.get("status") != "SUCCEEDED":
        return None
    try:
        value = json.loads(row.get("result_json") or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _verified_discovery_exclude(store: Any, run_id: str, sid: str) -> bool:
    target = str(sid).upper()
    for row in store.list_stage_results(run_id):
        stage = str(row.get("stage") or "")
        if not stage.startswith("V8_MAIN_SCANNER_") or "_R" not in stage or row.get("status") != "SUCCEEDED":
            continue
        try:
            payload = json.loads(row.get("result_json") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for item in payload.get("coverage_ledger") or []:
            if not isinstance(item, dict) or str(item.get("security_id") or "").upper() != target:
                continue
            if (
                str(item.get("disposition") or "") == "EXCLUDE"
                and str(item.get("failure_class") or "") in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"}
                and bool(item.get("evidence_ids"))
            ):
                return True
    return False


def _certification_state(grade: str | None, route: str) -> tuple[str, str, bool]:
    if grade in {"A", "A-", "B+", "B", "EXCLUDE"}:
        if route != "PASS":
            return "NOT_EVALUATED", f"V8_STEP20_{route or 'MISSING'}", False
        if grade in {"A", "A-"}:
            return "PASS", f"V8_CERTIFICATION_{grade}", True
        if grade == "B+":
            return "NEXT_STAGE", "V8_CERTIFICATION_B_PLUS_PRE_A", True
        if grade == "B":
            return "WATCH", "V8_CERTIFICATION_B_WATCH", True
        return "REJECT", "V8_CERTIFICATION_EXCLUDE", True
    return "NOT_EVALUATED", "V8_CERTIFICATION_MISSING_OR_INVALID", False


def candidate_conservation_v22(self: Any, run_id: str) -> list[dict[str, Any]]:
    """Conserve candidates without allowing information failures to become investment rejects."""
    discovery_row = self.store.get_stage_result(run_id, "STOCK_DISCOVERY", None)
    if not discovery_row or discovery_row.get("status") != "SUCCEEDED":
        return []
    try:
        discovery = json.loads(discovery_row.get("result_json") or "{}")
    except (TypeError, ValueError):
        return []
    candidates = discovery.get("candidates") if isinstance(discovery, dict) else []
    ledger: list[dict[str, Any]] = []
    run = self.store.get_run(run_id)

    for candidate in candidates or []:
        if not isinstance(candidate, dict) or not candidate.get("security_id"):
            continue
        sid = str(candidate["security_id"])
        action = str(candidate.get("recommended_discovery_action") or "")
        rows = self.store.list_stage_results(run_id, sid)
        values: dict[str, dict[str, Any]] = {}
        dependencies: set[str] = set()
        for row in rows:
            stage_name = str(row.get("stage") or "")
            row_status = str(row.get("status") or "")
            # Failed/incomplete StageResults are engineering/information
            # receipts, never issuer evidence. Only explicit failure-marker
            # stages may be parsed while non-SUCCEEDED. All other failed rows
            # are ignored so a stale ``decision=REJECT`` payload cannot become
            # an investment rejection. Absence of a successful replacement
            # remains NOT_EVALUATED/EVIDENCE_DEBT downstream.
            failure_marker = stage_name in {
                "CANDIDATE_ENGINEERING_FAILURE",
                "RESEARCH_PROVIDER_FAILURE",
                "SEC_PROVIDER_FAILURE",
                "SEC_STALE_DATA",
            }
            if row_status != "SUCCEEDED" and not failure_marker:
                try:
                    dependencies.update(str(item) for item in json.loads(row.get("dependency_ids_json") or "[]"))
                except (TypeError, ValueError):
                    pass
                continue
            try:
                value = json.loads(row.get("result_json") or "{}")
            except (TypeError, ValueError):
                value = {}
            if isinstance(value, dict):
                values[stage_name] = value
            try:
                dependencies.update(str(item) for item in json.loads(row.get("dependency_ids_json") or "[]"))
            except (TypeError, ValueError):
                pass

        state, reason, evaluation_complete = "NOT_EVALUATED", "NO_TERMINAL_STATE", False
        failure = values.get("CANDIDATE_ENGINEERING_FAILURE") or getattr(self, "_v18_candidate_failures", {}).get(sid)
        if isinstance(failure, dict) and failure:
            state, reason = "ENGINEERING_FAILURE", str(failure.get("failed_stage") or failure.get("stage") or "CANDIDATE_STAGE")
        elif any(stage in values for stage in ("RESEARCH_PROVIDER_FAILURE", "SEC_PROVIDER_FAILURE", "SEC_STALE_DATA")):
            failed_stage = next(stage for stage in ("RESEARCH_PROVIDER_FAILURE", "SEC_PROVIDER_FAILURE", "SEC_STALE_DATA") if stage in values)
            state, reason = "ENGINEERING_FAILURE", failed_stage
        else:
            terminal_gate = False
            for gate_stage in ("STAGE_GATE", "CAPITAL_PRESCREEN_GATE", "CATALYST_GATE", "EXPECTATION_GAP_GATE"):
                value = values.get(gate_stage) or {}
                decision = str(value.get("decision") or "")
                if decision == GateDecision.REJECT.value:
                    state, reason, evaluation_complete, terminal_gate = "REJECT", gate_stage, True, True
                    break
                if decision in {GateDecision.INSUFFICIENT_EVIDENCE.value, GateDecision.SYSTEM_ERROR.value, GateDecision.RETRY_WITH_NEW_EVIDENCE.value}:
                    state = "ENGINEERING_FAILURE" if decision == GateDecision.SYSTEM_ERROR.value else "EVIDENCE_DEBT"
                    reason, evaluation_complete, terminal_gate = gate_stage, False, True
                    break

            if not terminal_gate:
                audit = values.get("ADVERSARIAL_AUDIT") or {}
                recommendation = str(audit.get("audit_recommendation") or "")
                audit_status = str(audit.get("status") or "")
                if bool(audit.get("engineering_failure")):
                    state, reason = "ENGINEERING_FAILURE", "ADVERSARIAL_AUDIT"
                elif recommendation == "AUDIT_EVIDENCE_INCOMPLETE" or audit_status in {"INCOMPLETE", "CONTEXT_INCOMPLETE", "BLOCKED"}:
                    state, reason = "EVIDENCE_DEBT", "ADVERSARIAL_AUDIT"
                elif recommendation == "CHALLENGES_CONTINUATION":
                    state, reason, evaluation_complete = "REJECT", "ADVERSARIAL_AUDIT", True
                else:
                    certification = values.get(cert.STEP18_STAGE) or _latest_payload(self.store, run_id, cert.STEP18_STAGE, sid)
                    grade = validated_research_grade(certification)
                    validator = values.get(cert.STEP20_STAGE) or _latest_payload(self.store, run_id, cert.STEP20_STAGE, sid) or {}
                    route = str(validator.get("route") or "")
                    if grade is not None:
                        state, reason, evaluation_complete = _certification_state(grade, route)
                    elif values.get("EVIDENCE_DEBT"):
                        state, reason = "EVIDENCE_DEBT", "UNRESOLVED_EVIDENCE_DEBT"
                    elif action == "EXCLUDE":
                        if _verified_discovery_exclude(self.store, run_id, sid):
                            state, reason, evaluation_complete = "REJECT", "VERIFIED_DISCOVERY_HARD_FAIL", True
                        else:
                            state, reason = "EVIDENCE_DEBT", "UNVERIFIED_DISCOVERY_EXCLUDE"
                    elif action in {"WATCH_STAGE0", "WATCH_RESET"}:
                        state, reason, evaluation_complete = "WATCH", action, True
                    elif action in {"DEEP_DIVE_SECONDARY", "EARLY_TRAJECTORY"}:
                        state, reason = "EVIDENCE_DEBT", action
                    else:
                        state, reason = "NOT_EVALUATED", "V8_CERTIFICATION_MISSING_OR_INVALID"

        receipt = {
            "state": state,
            "reason": reason,
            "discovery_action": action,
            "security_id": sid,
            "version": V8_SEMANTIC_CORE_VERSION,
            "evaluation_complete": bool(evaluation_complete and state in CANDIDATE_COMPLETE_STATES),
            "investment_reject": state == "REJECT",
            "information_failure": state in {"NOT_EVALUATED", "ENGINEERING_FAILURE", "EVIDENCE_DEBT", "DATA_BLOCK"},
        }
        dep_ids = sorted(dependencies)
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
        "version": V8_SEMANTIC_CORE_VERSION,
        "candidate_source_exhausted_state_forbidden": True,
        "evaluation_and_investment_conclusion_are_orthogonal": True,
    })
    for state in sorted(CANDIDATE_COMPLETE_STATES | CANDIDATE_INCOMPLETE_STATES):
        self.store.record_funnel(run_id, f"CONSERVATION_{state}", counts.get(state, 0), {"version": V8_SEMANTIC_CORE_VERSION})
    return ledger


def _scanner_counts(agent: Any, run_id: str) -> dict[str, Any]:
    state = getattr(agent, "_v8_integrity_state", {}).get(run_id) or {}
    scanners = state.get("scanners") or {}
    required = set(integrity.SCANNER_REQUIRED_DIMENSIONS)
    executed = {
        sid for sid, receipt in scanners.items()
        if receipt.get("execution_status") == "SIGNAL_SCAN_COMPLETE" and receipt.get("output_validated") is True
    }
    validated = {sid for sid, receipt in scanners.items() if receipt.get("output_validated") is True}
    coverage = {sid for sid, receipt in scanners.items() if receipt.get("coverage_ledger_validated") is True}
    local_closed = {sid for sid, receipt in scanners.items() if receipt.get("source_exhaustion") is True}
    data_blocked = {
        sid for sid, receipt in scanners.items()
        if receipt.get("execution_status") in {"DATA_BLOCKED", "PROVIDER_FAILURE", "SCHEMA_FAILURE"}
        or bool(receipt.get("failure_class"))
    }
    return {
        "required": required,
        "scanners": scanners,
        "executed": executed,
        "validated": validated,
        "coverage": coverage,
        "local_closed": local_closed,
        "data_blocked": data_blocked,
    }


def build_run_evaluation_proof(agent: Any, run_id: str) -> dict[str, Any]:
    funnel = _funnel(agent.store, run_id)
    scan = _scanner_counts(agent, run_id)
    required = scan["required"]
    coach_state = getattr(agent, "_v8_main_discovery_state", {}).get(run_id) or {}
    eligible = int(coach_state.get("strategy_eligible_unique") or 0)
    if eligible <= 0 and scan["scanners"]:
        eligible = min((int(receipt.get("evaluated_count") or 0) for receipt in scan["scanners"].values()), default=0)
    source = source_exhaustion_proof(agent.store, run_id, eligible)

    source_integrity = _parse_details(funnel.get("V8_SOURCE_INTEGRITY"))
    source_integrity_pass = source_integrity.get("complete") is True
    sentinel = _parse_details(funnel.get("V8_MAIN_SENTINEL_COVERAGE_VALIDATION"))
    sentinel_complete = bool(sentinel.get("exact_sample_coverage") is True or coach_state.get("sentinel_complete") is True)
    systematic_fn = bool(coach_state.get("systematic_false_negative_risk"))

    try:
        open_high_secondary = int(agent.store.connection.execute(
            "SELECT COUNT(*) n FROM discovery_secondary_queue WHERE status='OPEN' AND research_value='HIGH'"
        ).fetchone()["n"])
    except Exception:
        open_high_secondary = 0

    early = _parse_details(funnel.get("V8_4_EARLY_TRAJECTORY_LEDGER"))
    unresolved_high_early = list(early.get("unresolved_high_research_value_ids") or [])
    stop = _parse_details(funnel.get("V8_MAIN_FORENSIC_SEARCH_STOP_AUDIT"))
    search_stop_allowed = stop.get("search_stop_allowed") is True

    ledger = candidate_conservation_v22(agent, run_id)
    discovery_row = agent.store.get_stage_result(run_id, "STOCK_DISCOVERY", None)
    discovered_ids: list[str] = []
    if discovery_row and discovery_row.get("status") == "SUCCEEDED":
        try:
            discovery = json.loads(discovery_row.get("result_json") or "{}")
        except (TypeError, ValueError):
            discovery = {}
        discovered_ids = [
            str(item.get("security_id")) for item in (discovery.get("candidates") or [])
            if isinstance(item, dict) and item.get("security_id")
        ] if isinstance(discovery, dict) else []

    ledger_ids = [str(item.get("security_id") or "") for item in ledger]
    conservation_complete = (
        len(ledger_ids) == len(discovered_ids)
        and len(set(ledger_ids)) == len(ledger_ids)
        and len(set(discovered_ids)) == len(discovered_ids)
        and set(ledger_ids) == set(discovered_ids)
    )
    engineering_count = sum(item.get("state") in {"ENGINEERING_FAILURE", "DATA_BLOCK"} for item in ledger)
    not_evaluated_count = sum(item.get("state") in {"NOT_EVALUATED", "EVIDENCE_DEBT"} for item in ledger)
    qualified_count = sum(item.get("state") == "PASS" and item.get("evaluation_complete") is True for item in ledger)
    scanner_execution_complete = scan["executed"] == required
    scanner_validation_complete = scan["validated"] == required
    scanner_coverage_complete = scan["coverage"] == required
    scanner_local_search_complete = scan["local_closed"] == required
    scanner_data_block_count = len(scan["data_blocked"])

    proof_pass = all((
        source_integrity_pass,
        scanner_execution_complete,
        scanner_validation_complete,
        scanner_coverage_complete,
        scanner_local_search_complete,
        scanner_data_block_count == 0,
        sentinel_complete,
        not systematic_fn,
        open_high_secondary == 0,
        len(unresolved_high_early) == 0,
        conservation_complete,
        engineering_count == 0,
        not_evaluated_count == 0,
        search_stop_allowed,
        source["source_exhausted"] is True,
    ))

    body = {
        "source_integrity_pass": source_integrity_pass,
        "canonical_universe_valid": source.get("full_scope_validated") is True,
        "scanner_required_count": len(required),
        "scanner_executed_count": len(scan["executed"]),
        "scanner_validated_count": len(scan["validated"]),
        "scanner_coverage_complete": scanner_coverage_complete,
        "scanner_local_search_complete": scanner_local_search_complete,
        "scanner_data_block_count": scanner_data_block_count,
        "sentinel_complete": sentinel_complete,
        "systematic_false_negative_risk": systematic_fn,
        "high_value_secondary_open_count": open_high_secondary,
        "high_early_trajectory_unresolved_count": len(unresolved_high_early),
        "candidate_discovered_count": len(discovered_ids),
        "candidate_conservation_count": len(ledger),
        "candidate_conservation_complete": conservation_complete,
        "candidate_engineering_failure_count": engineering_count,
        "candidate_not_evaluated_count": not_evaluated_count,
        "qualified_a_or_a_minus_count": qualified_count,
        "search_stop_allowed": search_stop_allowed,
        "source_exhaustion_proven": source["source_exhausted"] is True,
        "source_exhaustion_proof_hash": source["proof_hash"],
        "search_debt_remaining": not proof_pass,
        "terminal_lineage_valid": conservation_complete,
        "step15_20_pipeline_consistent": not_evaluated_count == 0,
        "proof_status": "PASS" if proof_pass else "INCOMPLETE",
        "clean_no_trade_authorized": bool(proof_pass and qualified_count == 0),
        "qualified_pool_authorized": bool(proof_pass and qualified_count > 0),
        "broker_write_authority": False,
        "version": V8_SEMANTIC_CORE_VERSION,
    }
    body["proof_hash"] = canonical_hash(body)
    return body


def derive_authoritative_run_terminal_state(current: str, mode: RunMode, proof: dict[str, Any]) -> tuple[str, str | None]:
    current_value = str(current or "")
    if current_value == "NOT_EVALUABLE_INPUT_INTEGRITY" or not proof.get("source_integrity_pass"):
        return "NOT_EVALUABLE_INPUT_INTEGRITY", "V8 source integrity not proven"
    if current_value in {"NOT_EVALUABLE_PRE_DISCOVERY_FAILURE", "NOT_EVALUABLE_MARKET_CONTEXT"}:
        return current_value, "pre-discovery/market context failure"
    if int(proof.get("candidate_engineering_failure_count") or 0) > 0:
        return "NOT_EVALUABLE_ENGINEERING_FAILURE", "candidate engineering failure remains"
    if int(proof.get("scanner_data_block_count") or 0) > 0:
        return "NOT_EVALUABLE_DISCOVERY_COVERAGE", "scanner data block remains"
    if not (
        proof.get("scanner_executed_count") == proof.get("scanner_required_count")
        and proof.get("scanner_validated_count") == proof.get("scanner_required_count")
        and proof.get("scanner_coverage_complete") is True
        and proof.get("sentinel_complete") is True
    ):
        return "NOT_EVALUABLE_DISCOVERY_COVERAGE", "scanner/sentinel coverage incomplete"
    if not proof.get("source_exhaustion_proven") or not proof.get("search_stop_allowed"):
        return "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT", "authoritative search closure not proven"
    if not proof.get("candidate_conservation_complete") or int(proof.get("candidate_not_evaluated_count") or 0) > 0:
        return "NOT_EVALUABLE_RESEARCH_DEBT", "candidate research/certification debt remains"
    if proof.get("proof_status") != "PASS":
        return "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT", "run evaluation proof incomplete"

    if mode == RunMode.HUNT_ONLY:
        if proof.get("qualified_pool_authorized"):
            return "QUALIFIED_CANDIDATE_POOL", None
        if proof.get("clean_no_trade_authorized"):
            return "NO_TRADE", None
    return current_value, None


def _persist_run_proof(agent: Any, run_id: str, proof: dict[str, Any]) -> None:
    run = agent.store.get_run(run_id)
    agent.store.record_stage_result(
        run_id, None, "V8_RUN_EVALUATION_PROOF", None, proof, [],
        agent.store.dependency_hash([], run.rule_set.rule_set_hash, run.context_manifest_hash),
        agent.store.current_evidence_epoch_for([]),
    )
    agent.store.record_funnel(run_id, "V8_RUN_EVALUATION_PROOF", int(proof.get("scanner_executed_count") or 0), proof)


def install_v8_semantic_core_v22() -> type:
    """Patch the final sentinel class in place; never create an outer wrapper."""
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_semantic_core_version", None) == V8_SEMANTIC_CORE_VERSION:
        return current

    # Canonical zero-anchor firewall.
    v8_primary._DISCOVERY_ONLY_KEYS.update(CERTIFICATION_FORBIDDEN_KEYS)
    v8_primary._DISCOVERY_SCORE_KEYS.update(CERTIFICATION_FORBIDDEN_KEYS)
    v8_primary._BLIND_KEYS.update(CERTIFICATION_FORBIDDEN_KEYS)
    v8_primary.v8_blind_packet = blind_certification_packet  # type: ignore[assignment]

    # Operational probe counts are coverage metadata only.  Production search
    # closure now consumes the explicit universe/source proof above.
    pre_live._provider_exhaustion_v20 = provider_exhaustion_v22  # type: ignore[assignment]
    integrity._provider_exhaustion = provider_exhaustion_v22  # type: ignore[assignment]
    discovery_post._provider_exhaustion = provider_exhaustion_v22  # type: ignore[assignment]

    # Candidate semantic authority is one deterministic function.
    current._candidate_conservation = candidate_conservation_v22  # type: ignore[attr-defined,assignment]

    base_run_strict = current._run_strict

    def run_strict_v22(self: Any, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
        outcome = base_run_strict(self, mode, data)
        run_id = str(getattr(outcome, "run_id", "") or "")
        if not run_id or run_id == "unstarted":
            return outcome
        proof = build_run_evaluation_proof(self, run_id)
        _persist_run_proof(self, run_id, proof)
        terminal, reason = derive_authoritative_run_terminal_state(str(getattr(outcome, "outcome", "") or ""), mode, proof)
        if terminal != str(getattr(outcome, "outcome", "") or "") or reason is not None:
            status = "SUCCEEDED" if terminal in {"NO_TRADE", "QUALIFIED_CANDIDATE_POOL"} else "FAILED"
            with self.store.transaction() as db:
                db.execute("UPDATE runs SET status=?, outcome=? WHERE run_id=?", (status, terminal, run_id))
            return replace(outcome, outcome=terminal, blocked_reason=reason)
        return outcome

    current._run_strict = run_strict_v22  # type: ignore[assignment]
    current.v8_semantic_core_version = V8_SEMANTIC_CORE_VERSION
    current.v8_certification_forbidden_keys = frozenset(CERTIFICATION_FORBIDDEN_KEYS)
    _INSTALLED = True
    return current
