"""V8 NEXT successor policy adopted on 2026-09-01.

This module supersedes the legacy V8 source pin without pretending that the
uploaded V8 NEXT archive was byte-for-byte mirrored into this repository.  The
repository-owned policy contract is docs/v8_next/V8_NEXT_POLICY_CONTRACT_2026-09-01.json
and is pinned by its canonical JSON hash.

Authority invariants:
- Investment Rules Source remains above V8 NEXT.
- Discovery Priority != Research Grade != PRE-A Readiness != Execution Action.
- A/A- count is an output, never a search/grade quota.
- A broad live U.S. scan may not cleanly stop before 150 unique researched
  candidates; 200 is preferred while marginal yield/search debt remains.
- Step 18 is the sole Research Grade authority.
- A/A- requires Step 17.5 completion, zero critical unknowns and robust
  certification gates.  B+ may enter PRE-A but PRE-A has no grade/execution
  authority.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from . import hunt_integrity_v18 as v18
from . import runtime as runtime_module
from . import store as store_module
from . import v8_primary
from .models import RunMode, RunOutcome, canonical_hash

V8_NEXT_POLICY_VERSION = "V8_NEXT_PRE_A_2026-09-01_R1"
V8_NEXT_POLICY_HASH = "15587aaee03dd137ded09c951350ce26a222f73a02230ee5a68aab4c224fbc4b"
V8_NEXT_SOURCE_PACKAGE = "STOCK_SCANNING_PROMPTS_V8_NEXT_PRE_A_2026-09-01(2).zip"
V8_NEXT_MINIMUM_UNIQUE_TICKERS = 150
V8_NEXT_PREFERRED_UNIQUE_TICKERS = 200
V8_NEXT_SUCCESSOR_VERSION = "V8_NEXT_SUCCESSOR_V1.0"

_REQUIRED_A_GATES = (
    "critical_claim_robustness",
    "evidence_independence",
    "valuation_fragility",
    "realization_1_8w",
    "dilution_adjusted_economics",
    "probability_provenance",
)
_REQUIRED_LEGACY_A_GATES = (
    "wake_up_1_8w",
    "independent_economic_improvement_axes",
    "numeric_expectation_gap",
    "why_now",
    "why_not_priced",
    "market_wakeup_mechanism",
    "extreme_bull_not_priced",
    "base_upside_economic",
    "bull_upside_additional_evidence",
    "target_not_reverse_engineered",
    "two_independent_valuation_methods",
    "scenario_probabilities_sum_100",
    "pw_ev_positive_meaningful",
    "execution_rr_not_arbitrary",
    "structural_asymmetry_separate",
    "full_sec_complete_non_toxic",
    "not_stage_3",
    "liquidity_pass",
    "market_data_fresh",
    "failure_scenarios_three_plus",
)
_SCORE_MAX = {
    "catalyst_strength": 25.0,
    "time_immediacy": 15.0,
    "numeric_evidence": 15.0,
    "supply_demand": 10.0,
    "price_stage_fit": 15.0,
    "strategic_fit": 15.0,
    "expected_value": 10.0,
}
_FORBIDDEN_CERT_KEYS = {
    "discovery_rank", "discovery_score", "discovery_priority_score",
    "previous_grade", "previous_target", "previous_probability", "previous_pw_ev",
    "pre_a_metadata", "pre_a_status", "promotion_readiness", "trajectory",
}


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_CERT_KEYS or _contains_forbidden(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden(child) for child in value)
    return False


def validate_v8_next_certification(payload: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    """Validate a Step-18 receipt against the V8 NEXT successor contract.

    A surface grade is never sufficient.  This validator independently checks
    the Python-owned certification status, arithmetic, lineage, gate matrix,
    cap semantics and contamination fences so a NOT_CERTIFIABLE receipt can
    never masquerade as A/A- merely because a score/grade field survived.
    """
    if not isinstance(payload, dict):
        return None, ["CERTIFICATION_MISSING"]
    failures: list[str] = []
    if str(payload.get("source_sha256") or "") != V8_NEXT_POLICY_HASH:
        failures.append("V8_NEXT_POLICY_HASH")
    if str(payload.get("policy_version") or "") != V8_NEXT_POLICY_VERSION:
        failures.append("V8_NEXT_POLICY_VERSION")
    if payload.get("grade_authority") != "V8_NEXT_STEP18_CANONICAL":
        failures.append("STEP18_GRADE_AUTHORITY")
    if payload.get("discovery_score_used") not in {False, "NO", "FALSE"}:
        failures.append("DISCOVERY_SCORE_CONTAMINATION")
    if payload.get("pre_a_metadata_used") not in {False, "NO", "FALSE"}:
        failures.append("PRE_A_CONTAMINATION")
    if payload.get("candidate_shortage_influenced_grade") not in {False, "NO", "FALSE"}:
        failures.append("GRADE_QUOTA_INFLUENCE")
    if payload.get("score_reset_from_zero") is not True:
        failures.append("SCORE_NOT_RESET")
    if _contains_forbidden(payload.get("certification_packet") or {}):
        failures.append("FORBIDDEN_METADATA_LEAK")

    grade = str(payload.get("research_grade") or payload.get("grade") or "").upper()
    if grade not in {"A", "A-", "B+", "B", "EXCLUDE"}:
        failures.append("INVALID_GRADE")
        grade = ""
    why_not = payload.get("why_not_one_grade_higher")
    if grade != "A" and not (isinstance(why_not, list) and any(str(x).strip() for x in why_not)):
        failures.append("WHY_NOT_ONE_GRADE_HIGHER_MISSING")

    status_expected = {
        "A": "A_CERTIFIED",
        "A-": "A_MINUS_CERTIFIED",
        "B+": "B_PLUS_ONLY",
        "B": "B_ONLY",
        "EXCLUDE": "EXCLUDE",
    }.get(grade)
    if status_expected and str(payload.get("certification_status") or "") != status_expected:
        failures.append("CERTIFICATION_STATUS_GRADE_MISMATCH")
    if str(payload.get("certification_status") or "") == "NOT_CERTIFIABLE":
        failures.append("CERTIFICATION_NOT_CERTIFIABLE")

    lineage_failures = payload.get("lineage_failures")
    if not isinstance(lineage_failures, list):
        failures.append("LINEAGE_VALIDATION_MISSING")
    elif lineage_failures:
        failures.append("EVIDENCE_LINEAGE_FAILURE")
    evidence_ids = payload.get("evidence_ids")
    if grade in {"A", "A-"} and (not isinstance(evidence_ids, list) or not any(str(item).strip() for item in evidence_ids)):
        failures.append("A_GRADE_EVIDENCE_LINEAGE_EMPTY")

    engine = str(payload.get("python_grade_engine") or "")
    if not engine.startswith("V8_NEXT_CERTIFICATION_ENGINE_"):
        failures.append("PYTHON_GRADE_ENGINE_MISSING")

    score = payload.get("normalized_score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(float(score)) or not 0 <= float(score) <= 100:
        failures.append("NORMALIZED_SCORE_INVALID")
        score_value = -1.0
    else:
        score_value = float(score)
    raw_score = payload.get("raw_score")
    components = payload.get("score_components")
    if not isinstance(components, dict) or set(components) != set(_SCORE_MAX):
        failures.append("SCORE_COMPONENTS_INVALID")
    else:
        calculated_raw = 0.0
        for key, maximum in _SCORE_MAX.items():
            value = components.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or not 0 <= float(value) <= maximum:
                failures.append(f"SCORE_COMPONENT_{key.upper()}_INVALID")
                continue
            calculated_raw += float(value)
        if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool) or not math.isclose(float(raw_score), calculated_raw, abs_tol=1e-5):
            failures.append("RAW_SCORE_ARITHMETIC")
        calculated_normalized = calculated_raw / 105.0 * 100.0
        if score_value >= 0 and not math.isclose(score_value, calculated_normalized, abs_tol=1e-5):
            failures.append("NORMALIZED_SCORE_ARITHMETIC")

    minimum = {"A": 85.0, "A-": 80.0, "B+": 72.0, "B": 65.0}.get(grade)
    if minimum is not None and score_value < minimum:
        failures.append("GRADE_EXCEEDS_SCORE")
    if grade == "EXCLUDE" and score_value >= 65.0:
        # EXCLUDE above the numeric threshold is allowed only when a hard
        # exclusion/cap is explicitly present below.
        caps_for_exclude = payload.get("active_grade_caps") or []
        if not isinstance(caps_for_exclude, list) or not caps_for_exclude:
            failures.append("EXCLUDE_WITHOUT_HARD_FAIL")

    critical_unknowns = payload.get("critical_unknowns")
    critical_count = payload.get("critical_unknown_count")
    if not isinstance(critical_unknowns, list) or not isinstance(critical_count, int) or isinstance(critical_count, bool):
        failures.append("CRITICAL_UNKNOWN_LEDGER_INVALID")
    elif critical_count != len(critical_unknowns):
        failures.append("CRITICAL_UNKNOWN_COUNT_MISMATCH")

    if grade in {"A", "A-"}:
        if critical_count != 0:
            failures.append("CRITICAL_UNKNOWN_PRESENT")
        if payload.get("step17_5_complete") is not True:
            failures.append("STEP17_5_INCOMPLETE")
        gates = payload.get("hard_gate_statuses")
        if not isinstance(gates, dict):
            failures.append("NEXT_HARD_GATES_MISSING")
        else:
            for gate in _REQUIRED_A_GATES:
                if str(gates.get(gate) or "").upper() != "PASS":
                    failures.append(f"NEXT_GATE_{gate.upper()}")
        legacy_gates = payload.get("legacy_hard_gate_statuses")
        if not isinstance(legacy_gates, dict):
            failures.append("LEGACY_HARD_GATES_MISSING")
        else:
            for gate in _REQUIRED_LEGACY_A_GATES:
                if str(legacy_gates.get(gate) or "").upper() != "PASS":
                    failures.append(f"LEGACY_GATE_{gate.upper()}")
        active_caps = payload.get("active_grade_caps") or []
        if not isinstance(active_caps, list):
            failures.append("ACTIVE_GRADE_CAP_LEDGER_INVALID")
        else:
            allowed_a_minus_caps = {"INDEPENDENT_AXES_3_MAX_A_MINUS"} if grade == "A-" else set()
            disallowed_caps = {str(item) for item in active_caps} - allowed_a_minus_caps
            if disallowed_caps:
                failures.append("ACTIVE_GRADE_CAP")
        if grade == "A-" and score_value >= 85.0:
            caps = {str(item) for item in (payload.get("active_grade_caps") or [])}
            if "INDEPENDENT_AXES_3_MAX_A_MINUS" not in caps:
                failures.append("A_MINUS_HIGH_SCORE_WITHOUT_CAP")
        if grade == "A" and score_value < 85.0:
            failures.append("A_SCORE_THRESHOLD")

    packet = payload.get("certification_packet")
    packet_hash = packet.get("packet_hash") if isinstance(packet, dict) else None
    if not isinstance(packet_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", packet_hash):
        failures.append("CERTIFICATION_PACKET_HASH_INVALID")

    return (grade or None), sorted(set(failures))


def build_v8_next_discovery_contract(candidate_count: int) -> dict[str, Any]:
    count = max(0, int(candidate_count))
    return {
        "run_mode": "HUNT_ONLY_RECALL_FIRST",
        "v8_primary_version": V8_NEXT_POLICY_VERSION,
        "v8_ruleset_hash": V8_NEXT_POLICY_HASH,
        "source_package": V8_NEXT_SOURCE_PACKAGE,
        "discovery_priority_is_research_grade": False,
        "research_grade_allowed": False,
        "grade_quota_forbidden": True,
        "a_count_is_output_not_target": True,
        "minimum_unique_tickers": V8_NEXT_MINIMUM_UNIQUE_TICKERS,
        "preferred_unique_tickers": V8_NEXT_PREFERRED_UNIQUE_TICKERS,
        "stop_before_minimum_only_if_source_exhaustion_proven": True,
        "after_minimum_stop_requires": [
            "scanner_family_coverage_complete",
            "marginal_yield_low_or_source_exhausted",
            "duplicate_saturation_recorded",
            "unresolved_high_priority_search_debt_absent",
        ],
        "round_telemetry_required": [
            "new_unique_tickers", "deep_dive_entries", "new_independent_evidence",
            "duplicate_saturation", "marginal_yield", "source_exhaustion",
        ],
        "discovery_candidate_count": count,
        "lanes": dict(v8_primary.V8_DISCOVERY_LANES),
        "mandatory_lanes": sorted(v8_primary.V8_DISCOVERY_LANES),
        "weakness_first": True,
        "unknowns_become_evidence_debt": True,
        "blind_verification_required": True,
        "score_reset_at_certification": True,
    }


def _latest_payload(store: Any, run_id: str, stage: str, subject_id: str) -> dict[str, Any] | None:
    row = store.get_stage_result(run_id, stage, subject_id)
    if not row or row.get("status") != "SUCCEEDED":
        return None
    try:
        value = json.loads(row.get("result_json") or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _run_round_telemetry(store: Any, run_id: str, coverage: int) -> dict[str, Any]:
    rows = store.list_stage_results(run_id)
    evidence_refs: list[str] = []
    source_exhausted: set[str] = set()
    for row in rows:
        try:
            evidence_refs.extend(str(x) for x in json.loads(row.get("dependency_ids_json") or "[]"))
            payload = json.loads(row.get("result_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        if isinstance(payload, dict) and (
            payload.get("source_exhausted") is True
            or str(payload.get("evaluation_status") or "").upper() == "SOURCE_EXHAUSTED"
        ) and row.get("subject_id"):
            source_exhausted.add(str(row["subject_id"]))
    distinct = len(set(evidence_refs))
    total = len(evidence_refs)
    duplicate_saturation = 0.0 if total == 0 else max(0.0, 1.0 - distinct / total)
    funnel = {str(row["funnel_stage"]): int(row["count"]) for row in store.list_funnel(run_id)}
    deep = int(funnel.get("DEEP_RESEARCH", 0))
    return {
        "new_unique_tickers": coverage,
        "deep_dive_entries": deep,
        "new_independent_evidence": distinct,
        "duplicate_saturation": round(duplicate_saturation, 6),
        "marginal_yield": round(deep / coverage, 6) if coverage else 0.0,
        "source_exhaustion": len(source_exhausted),
        "source_exhausted_security_ids": sorted(source_exhausted)[:300],
    }


def install_v8_next_successor() -> type:
    current = runtime_module.ProductionStockAgent
    if getattr(current, "v8_next_successor_version", None) == V8_NEXT_SUCCESSOR_VERSION:
        return current

    # Supersede legacy V8 identifiers at runtime.  The legacy manifest remains
    # frozen in docs/v8_canonical for historical regression only.
    v8_primary.V8_PRIMARY_VERSION = V8_NEXT_POLICY_VERSION
    v8_primary.V8_PRIMARY_POLICY_VERSION = V8_NEXT_POLICY_VERSION
    v8_primary.V8_SOURCE_ARCHIVE = V8_NEXT_SOURCE_PACKAGE
    v8_primary.TARGET_UNIQUE_TICKERS = V8_NEXT_MINIMUM_UNIQUE_TICKERS
    v8_primary.TARGET_VERIFIED_A_MINUS_OR_BETTER = 0
    v8_primary.build_v8_discovery_contract = build_v8_next_discovery_contract
    v18.STEP18_SOURCE_SHA256 = V8_NEXT_POLICY_HASH

    base_qualified = store_module.SQLiteStore.qualified_candidate_status

    def qualified_candidate_status_v8_next(self: Any, run_id: str, subject_id: str, strict: bool = True) -> tuple[bool, list[str]]:
        qualified, missing = base_qualified(self, run_id, subject_id, strict=strict)
        if not strict:
            return qualified, missing
        audit = _latest_payload(self, run_id, "V8_CRITICAL_ASSUMPTION_AUDIT", subject_id)
        if not isinstance(audit, dict) or audit.get("status") != "COMPLETE" or audit.get("grade_authority") not in {False, "NO", "FALSE"}:
            missing = [*missing, "V8_STEP17_5_CRITICAL_ASSUMPTION_AUDIT"]
            qualified = False
        cert = _latest_payload(self, run_id, "V8_CERTIFICATION", subject_id)
        grade, cert_failures = validate_v8_next_certification(cert)
        if grade not in {"A", "A-"} or cert_failures:
            missing.extend([f"V8_NEXT_CERT:{item}" for item in cert_failures] or ["V8_NEXT_CERT:A_OR_A_MINUS"])
            qualified = False
        return qualified, sorted(set(missing))

    store_module.SQLiteStore.qualified_candidate_status = qualified_candidate_status_v8_next

    class V8NextProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_next_successor_version = V8_NEXT_SUCCESSOR_VERSION
        v8_primary_version = V8_NEXT_POLICY_VERSION
        v8_ruleset_hash = V8_NEXT_POLICY_HASH

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            provider = getattr(self.config, "market_data_provider", None)
            query = data.get("universe_query") if isinstance(data.get("universe_query"), dict) else {}
            bounded = bool(query.get("symbols") or query.get("tickers"))
            recorded_type = getattr(runtime_module, "RecordedMarketDataProvider", ())
            is_recorded = bool(recorded_type and isinstance(provider, recorded_type))
            funnel = {str(row["funnel_stage"]): int(row["count"]) for row in self.store.list_funnel(run_id)}
            coverage = max(
                int(funnel.get("STAGE_DISCOVERY_READY", 0)),
                int(funnel.get("STAGE_ELIGIBLE", 0)),
                int(funnel.get("TECHNICAL_FEATURES", 0)),
                int(funnel.get("ADV30_DISCOVERED", 0)),
            )
            telemetry = _run_round_telemetry(self.store, run_id, coverage)
            if bounded or is_recorded:
                coverage_status = "FIXTURE_OR_BOUNDED_NOT_ENFORCED"
            elif coverage >= V8_NEXT_PREFERRED_UNIQUE_TICKERS:
                coverage_status = "PASS_PREFERRED"
            elif coverage >= V8_NEXT_MINIMUM_UNIQUE_TICKERS:
                coverage_status = "PASS_MINIMUM_PREFERRED_NOT_REACHED"
            else:
                coverage_status = "FAIL_MINIMUM_COVERAGE"
            self.store.record_funnel(run_id, "V8_NEXT_DISCOVERY_COVERAGE", coverage, {
                "status": coverage_status,
                "minimum_unique_tickers": V8_NEXT_MINIMUM_UNIQUE_TICKERS,
                "preferred_unique_tickers": V8_NEXT_PREFERRED_UNIQUE_TICKERS,
                "grade_quota_forbidden": True,
                "a_count_is_output_not_target": True,
                "telemetry": telemetry,
            })
            if coverage_status == "FAIL_MINIMUM_COVERAGE":
                with self.store.transaction() as db:
                    db.execute("UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?", ("NOT_EVALUABLE_DISCOVERY_COVERAGE", run_id))
                return RunOutcome(
                    run_id,
                    mode,
                    "NOT_EVALUABLE_DISCOVERY_COVERAGE",
                    getattr(outcome, "qualified_candidates", ()),
                    blocked_reason=f"V8 NEXT broad discovery coverage {coverage} < {V8_NEXT_MINIMUM_UNIQUE_TICKERS}",
                )
            return outcome

    runtime_module.ProductionStockAgent = V8NextProductionStockAgent
    return V8NextProductionStockAgent
