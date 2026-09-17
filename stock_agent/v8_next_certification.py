"""V8 NEXT research-certification pipeline helpers.

The LLM is an analysis/extraction worker only.  Python owns arithmetic, grade
thresholds, Grade Caps, evidence-lineage validation and the authoritative
Step-18 Research Grade receipt.
"""
from __future__ import annotations

import copy
import json
import math
from typing import Any

from .models import canonical_hash
from . import v8_primary

STEP15_STAGE = "V8_CAPITAL_STRUCTURE_BRIDGE"
STEP16_STAGE = "V8_ATOMIC_CLAIM_AUDIT"
STEP17_STAGE = "V8_CANONICAL_PACKET"
STEP17_5_STAGE = "V8_CRITICAL_ASSUMPTION_AUDIT"
STEP18_DRAFT_STAGE = "V8_CERTIFICATION_DRAFT"
STEP18_STAGE = "V8_CERTIFICATION"
STEP20_STAGE = "V8_RESEARCH_VALIDATOR"

PROMPT_STEP15 = "v8_next.step15_capital_structure_bridge"
PROMPT_STEP16 = "v8_next.step16_atomic_claim_audit"
PROMPT_STEP17_5 = "v8_next.step17_5_critical_assumption_audit"
PROMPT_STEP18 = "v8_next.step18_independent_certification"

ASSUMPTIONS = (
    "expectation_gap",
    "reverse_valuation",
    "target_value",
    "rnpv",
    "pw_ev",
    "scenario_probability",
    "catalyst_realization",
    "dilution_adjusted_per_share_value",
)

LEGACY_HARD_GATES = (
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

NEXT_HARD_GATES = (
    "critical_claim_robustness",
    "evidence_independence",
    "valuation_fragility",
    "realization_1_8w",
    "dilution_adjusted_economics",
    "probability_provenance",
)

SCORE_MAX = {
    "catalyst_strength": 25.0,
    "time_immediacy": 15.0,
    "numeric_evidence": 15.0,
    "supply_demand": 10.0,
    "price_stage_fit": 15.0,
    "strategic_fit": 15.0,
    "expected_value": 10.0,
}

GRADE_ORDER = {"EXCLUDE": -1, "B": 0, "B+": 1, "A-": 2, "A": 3}


def _object(properties: dict[str, Any], required: list[str] | tuple[str, ...]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _evidence_array() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}


def _status_evidence_schema() -> dict[str, Any]:
    return _object({
        "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_EVALUATED"]},
        "rationale": {"type": "string", "minLength": 1},
        "evidence_ids": _evidence_array(),
    }, ["status", "rationale", "evidence_ids"])


def step15_schema() -> dict[str, Any]:
    nullable_number = {"type": ["number", "null"], "minimum": 0}
    return _object({
        "status": {"type": "string", "enum": ["COMPLETE", "INCOMPLETE"]},
        "current_shares": nullable_number,
        "instrument_potential_shares": nullable_number,
        "fully_diluted_shares": nullable_number,
        "likely_near_term_dilution_shares": nullable_number,
        "probable_financing_shares": nullable_number,
        "projected_near_term_fd_shares": nullable_number,
        "cash_runway_months": nullable_number,
        "financing_need": {"type": "string", "enum": ["NONE", "POSSIBLE", "PROBABLE", "IMMINENT", "UNVERIFIED"]},
        "toxic_red_flag": {"type": "boolean"},
        "source_instruments": {"type": "array", "items": _object({
            "instrument_type": {"type": "string", "minLength": 1},
            "potential_shares": nullable_number,
            "summary": {"type": "string", "minLength": 1},
            "evidence_ids": _evidence_array(),
        }, ["instrument_type", "potential_shares", "summary", "evidence_ids"])},
        "per_share_impact_summary": {"type": "string", "minLength": 1},
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": _evidence_array(),
        "grade_authority": {"const": False},
    }, [
        "status", "current_shares", "instrument_potential_shares", "fully_diluted_shares",
        "likely_near_term_dilution_shares", "probable_financing_shares",
        "projected_near_term_fd_shares", "cash_runway_months", "financing_need",
        "toxic_red_flag", "source_instruments", "per_share_impact_summary", "unknowns",
        "evidence_ids", "grade_authority",
    ])


def step16_schema() -> dict[str, Any]:
    return _object({
        "status": {"type": "string", "enum": ["COMPLETE", "INCOMPLETE"]},
        "atomic_claims": {"type": "array", "items": _object({
            "claim_id": {"type": "string", "minLength": 1},
            "statement": {"type": "string", "minLength": 1},
            "verification_status": {"type": "string", "enum": ["VERIFIED", "CONTRADICTED", "UNVERIFIED"]},
            "economic_event_id": {"type": "string", "minLength": 1},
            "independent_evidence_group": {"type": "string", "minLength": 1},
            "evidence_ids": _evidence_array(),
        }, ["claim_id", "statement", "verification_status", "economic_event_id", "independent_evidence_group", "evidence_ids"])},
        "evidence_independence": {"type": "string", "enum": ["PASS", "FAIL", "UNVERIFIED"]},
        "duplicate_economic_event_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "critical_unknowns": {"type": "array", "items": _object({
            "statement": {"type": "string", "minLength": 1},
            "evidence_ids": _evidence_array(),
        }, ["statement", "evidence_ids"])},
        "value_realization_bridge_1_8w": _object({
            "status": {"type": "string", "enum": ["ROBUST", "FRAGILE", "UNVERIFIED", "CONFLICT"]},
            "summary": {"type": "string", "minLength": 1},
            "evidence_ids": _evidence_array(),
        }, ["status", "summary", "evidence_ids"]),
        "probability_provenance": {"type": "string", "enum": ["DATA_BACKED", "CALIBRATED_RANGE", "SUBJECTIVE", "UNVERIFIED"]},
        "grade_authority": {"const": False},
    }, [
        "status", "atomic_claims", "evidence_independence", "duplicate_economic_event_ids",
        "critical_unknowns", "value_realization_bridge_1_8w", "probability_provenance", "grade_authority",
    ])


def step17_5_schema() -> dict[str, Any]:
    return _object({
        "status": {"type": "string", "enum": ["COMPLETE", "INCOMPLETE"]},
        "assumptions": {"type": "array", "minItems": len(ASSUMPTIONS), "items": _object({
            "assumption_id": {"type": "string", "enum": list(ASSUMPTIONS)},
            "status": {"type": "string", "enum": ["ROBUST", "FRAGILE", "UNVERIFIED", "CONFLICT"]},
            "rationale": {"type": "string", "minLength": 1},
            "evidence_ids": _evidence_array(),
            "unknowns": {"type": "array", "items": {"type": "string"}},
        }, ["assumption_id", "status", "rationale", "evidence_ids", "unknowns"])},
        "grade_authority": {"const": False},
    }, ["status", "assumptions", "grade_authority"])


def step18_schema() -> dict[str, Any]:
    scores = {key: {"type": "number", "minimum": 0, "maximum": maximum} for key, maximum in SCORE_MAX.items()}
    legacy = {key: _status_evidence_schema() for key in LEGACY_HARD_GATES}
    nxt = {key: _status_evidence_schema() for key in NEXT_HARD_GATES}
    return _object({
        "status": {"type": "string", "enum": ["COMPLETE", "INCOMPLETE"]},
        "score_components": _object(scores, list(scores)),
        "legacy_hard_gates": _object(legacy, list(legacy)),
        "next_hard_gates": _object(nxt, list(nxt)),
        "grade_caps": {"type": "array", "items": _object({
            "code": {"type": "string", "minLength": 1},
            "max_grade": {"type": "string", "enum": ["B+", "B", "EXCLUDE", "NOT_CERTIFIABLE"]},
            "rationale": {"type": "string", "minLength": 1},
            "evidence_ids": _evidence_array(),
        }, ["code", "max_grade", "rationale", "evidence_ids"])},
        "independent_improvement_axes": {"type": "array", "items": _object({
            "axis_id": {"type": "string", "minLength": 1},
            "summary": {"type": "string", "minLength": 1},
            "economic_event_id": {"type": "string", "minLength": 1},
            "evidence_ids": _evidence_array(),
        }, ["axis_id", "summary", "economic_event_id", "evidence_ids"])},
        "valuation_method_count": {"type": "integer", "minimum": 0},
        "scenarios": {"type": "array", "minItems": 3, "maxItems": 3, "items": _object({
            "scenario": {"type": "string", "enum": ["BEAR", "BASE", "BULL"]},
            "probability_pct": {"type": "number", "minimum": 0, "maximum": 100},
            "price_or_value": {"type": "number", "exclusiveMinimum": 0},
            "assumption": {"type": "string", "minLength": 1},
            "evidence_ids": _evidence_array(),
        }, ["scenario", "probability_pct", "price_or_value", "assumption", "evidence_ids"])},
        "probability_provenance": _object({
            "class": {"type": "string", "enum": ["DATA_BACKED", "CALIBRATED_RANGE", "SUBJECTIVE", "UNVERIFIED"]},
            "summary": {"type": "string", "minLength": 1},
            "evidence_ids": _evidence_array(),
        }, ["class", "summary", "evidence_ids"]),
        "target_reverse_engineered": {"type": "boolean"},
        "toxic_red_flag": {"type": "boolean"},
        "b_plus_devil_advocate": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "why_not_one_grade_higher": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "score_reset_from_zero": {"const": True},
        "discovery_score_used": {"const": False},
        "pre_a_metadata_used": {"const": False},
        "candidate_shortage_influenced_grade": {"const": False},
        "grade_authority": {"const": False},
    }, [
        "status", "score_components", "legacy_hard_gates", "next_hard_gates", "grade_caps",
        "independent_improvement_axes", "valuation_method_count", "scenarios", "probability_provenance",
        "target_reverse_engineered", "toxic_red_flag", "b_plus_devil_advocate",
        "why_not_one_grade_higher", "score_reset_from_zero", "discovery_score_used",
        "pre_a_metadata_used", "candidate_shortage_influenced_grade", "grade_authority",
    ])


PROMPTS: dict[str, tuple[str, str, dict[str, Any]]] = {
    PROMPT_STEP15: (
        "V8NextStep15CapitalStructureBridgeV1",
        """# V8 NEXT Step 15 — Capital Structure / FD Share Bridge\nYou are an extraction and forensic-analysis worker, never a grade authority.\nUse only the supplied SEC/canonical evidence. Do not infer missing share counts.\nSeparate current basic shares, existing potentially dilutive instruments, likely near-term dilution and probable new financing.\n`fully_diluted_shares` means current shares plus existing dilutive instruments.\n`projected_near_term_fd_shares` means fully diluted shares plus probable new-financing shares; do not double count likely exercises already inside existing instruments.\nIf any number is not reproducible from evidence, return null and state INCOMPLETE. Shelf registration alone is not issuance. Toxic structures must be surfaced, never softened. `grade_authority` is false.""",
        step15_schema(),
    ),
    PROMPT_STEP16: (
        "V8NextStep16AtomicClaimAuditV1",
        """# V8 NEXT Step 16 — Blind Atomic Claim / Evidence Independence Audit\nYou are not a grade authority. Ignore Discovery priority, previous grade, PRE-A and promotional narrative.\nSplit the thesis into atomic factual/economic claims. Bind every claim to actual evidence IDs.\nAssign one ECONOMIC_EVENT_ID to claims that depend on the same underlying event so duplicate evidence cannot be counted twice.\nMark unverified or contradicted claims explicitly. Test whether the 1–8 week value-realization bridge exists. Probability provenance must be DATA_BACKED, CALIBRATED_RANGE, SUBJECTIVE or UNVERIFIED. `grade_authority` is false.""",
        step16_schema(),
    ),
    PROMPT_STEP17_5: (
        "V8NextStep17_5CriticalAssumptionAuditV1",
        """# V8 NEXT Step 17.5 — Critical Assumption Audit\nAudit exactly these eight assumptions: expectation_gap, reverse_valuation, target_value, rnpv, pw_ev, scenario_probability, catalyst_realization, dilution_adjusted_per_share_value.\nFor each return ROBUST, FRAGILE, UNVERIFIED or CONFLICT with evidence IDs and unknowns. Do not generate a Research Grade. Do not use Discovery or PRE-A metadata. `grade_authority` is false.""",
        step17_5_schema(),
    ),
    PROMPT_STEP18: (
        "V8NextStep18CertificationDraftV1",
        """# V8 NEXT Step 18 — Independent Certification Draft\nStart from zero previous score and zero previous grade. Discovery metadata and PRE-A metadata are forbidden. You are not the grade authority; Python computes the final grade.\nScore the legacy 105-point system: catalyst 25, time 15, numeric evidence 15, supply/demand 10, price/stage 15, strategic fit 15, expected value 10.\nAudit all 20 legacy A/A- Hard Gates and all six V8 NEXT gates independently. Failed/unknown gates cannot be offset by score.\nApply Grade Caps explicitly: numeric expectation gap unproven; weak 1–8w wake-up; fewer than two reproducible valuation methods; missing/unreproducible PW-EV; incomplete Full SEC; stale/unknown market data; stage uncertainty; Base that is really Bull/multiple expansion; unexplained post-results selloff; unverified product/coverage revenue timing; conference-only catalyst => max B+. Toxic dilution/accounting red flag or target reverse-engineered to desired return => EXCLUDE.\nConstruct BEAR/BASE/BULL probabilities in coarse 5–10 percentage-point precision, not fake decimals, and bind probability provenance. Do not invent evidence IDs.\nAttack the B+ hypothesis before claiming strength and provide WHY_NOT_ONE_GRADE_HIGHER. Candidate shortage must never affect any score or gate. `grade_authority` is false.""",
        step18_schema(),
    ),
}


def install_runtime_prompt_contracts(prompt_runtime: Any) -> None:
    """Install repository-owned NEXT schemas into this PromptRuntime instance."""
    existing = {str(item.get("prompt_id")) for item in prompt_runtime.manifest.get("prompts", []) if isinstance(item, dict)}
    for prompt_id, (schema_id, body, schema) in PROMPTS.items():
        prompt_runtime.registry.setdefault("schemas", {})[schema_id] = copy.deepcopy(schema)
        prompt_runtime.prompts[prompt_id] = {
            "prompt_id": prompt_id,
            "version": "1.0",
            "prompt_kind": "LEAF",
            "output_schema": schema_id,
            "required_inputs": ["effective_rule_pack", "certification_packet"],
            "optional_inputs": [],
            "compose_with": [],
            "requires_results": [],
            "requires_capabilities": [],
            "allowed_run_modes": ["HUNT_ONLY", "HUNT_AND_EXECUTION_REVIEW"],
            "_body": body,
        }
        if prompt_id not in existing:
            prompt_runtime.manifest.setdefault("prompts", []).append({
                "prompt_id": prompt_id,
                "content_hash": canonical_hash(body),
                "file": f"RUNTIME:{prompt_id}",
            })


def _collect_evidence_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in {"evidence_ids", "source_evidence_ids"} and isinstance(child, list):
                result.update(str(item) for item in child if str(item))
            result.update(_collect_evidence_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_collect_evidence_ids(child))
    return result


def evidence_reference_failures(value: Any, allowed_evidence_ids: list[str]) -> list[str]:
    allowed = {str(item) for item in allowed_evidence_ids}
    referenced = _collect_evidence_ids(value)
    return sorted(referenced - allowed)


def finalize_fd_bridge(draft: dict[str, Any], allowed_evidence_ids: list[str]) -> dict[str, Any]:
    value = copy.deepcopy(draft) if isinstance(draft, dict) else {}
    failures = evidence_reference_failures(value, allowed_evidence_ids)
    for field in (
        "current_shares", "instrument_potential_shares", "fully_diluted_shares",
        "likely_near_term_dilution_shares", "probable_financing_shares", "projected_near_term_fd_shares",
    ):
        raw = value.get(field)
        if raw is not None and (not isinstance(raw, (int, float)) or isinstance(raw, bool) or not math.isfinite(float(raw)) or float(raw) < 0):
            failures.append(f"INVALID_{field.upper()}")
    current = value.get("current_shares")
    instruments = value.get("instrument_potential_shares")
    fd = value.get("fully_diluted_shares")
    financing = value.get("probable_financing_shares")
    projected = value.get("projected_near_term_fd_shares")
    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (current, instruments, fd)):
        expected_fd = float(current) + float(instruments)
        if not math.isclose(float(fd), expected_fd, rel_tol=1e-6, abs_tol=max(1.0, expected_fd * 1e-6)):
            failures.append("FD_SHARE_BRIDGE_ARITHMETIC")
    else:
        failures.append("FD_SHARE_BRIDGE_INCOMPLETE")
    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (fd, financing, projected)):
        expected_projected = float(fd) + float(financing)
        if not math.isclose(float(projected), expected_projected, rel_tol=1e-6, abs_tol=max(1.0, expected_projected * 1e-6)):
            failures.append("PROJECTED_FD_ARITHMETIC")
    else:
        failures.append("PROJECTED_FD_INCOMPLETE")
    dilution_pct = None
    if isinstance(current, (int, float)) and not isinstance(current, bool) and float(current) > 0 and isinstance(projected, (int, float)) and not isinstance(projected, bool):
        dilution_pct = (float(projected) / float(current) - 1.0) * 100.0
    value.update({
        "status": "COMPLETE" if str(value.get("status")) == "COMPLETE" and not failures else "INCOMPLETE",
        "arithmetic_authority": "PYTHON_V8_NEXT_FD_BRIDGE_V1",
        "projected_dilution_pct": dilution_pct,
        "validation_failures": sorted(set(failures)),
        "grade_authority": False,
    })
    return value


def _stage_payload(store: Any, run_id: str, stage: str, subject_id: str) -> dict[str, Any] | None:
    row = store.get_stage_result(run_id, stage, subject_id)
    if not row or row.get("status") != "SUCCEEDED":
        return None
    try:
        value = json.loads(row.get("result_json") or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def build_step17_packet(
    store: Any,
    run_id: str,
    subject_id: str,
    candidate: dict[str, Any],
    evidence_ids: list[str],
    research_artifact_payload: dict[str, Any],
    policy_version: str,
    policy_hash: str,
) -> dict[str, Any]:
    """Create a metadata-scrubbed canonical packet for blind certification."""
    raw = {
        "ticker": subject_id,
        "policy_version": policy_version,
        "policy_hash": policy_hash,
        "evidence_ids": sorted(set(str(item) for item in evidence_ids)),
        "stage_gate": _stage_payload(store, run_id, "STAGE_GATE", subject_id),
        "capital_prescreen_gate": _stage_payload(store, run_id, "CAPITAL_PRESCREEN_GATE", subject_id),
        "catalyst_gate": _stage_payload(store, run_id, "CATALYST_GATE", subject_id),
        "expectation_gap_gate": _stage_payload(store, run_id, "EXPECTATION_GAP_GATE", subject_id),
        "fundamental_change": _stage_payload(store, run_id, "CAP_FUNDAMENTAL_CHANGE", subject_id),
        "catalyst_expectation_research": _stage_payload(store, run_id, "CAP_CATALYST_EXPECTATION_RESEARCH", subject_id),
        "directional_probability": _stage_payload(store, run_id, "CAP_DIRECTIONAL_PROBABILITY", subject_id),
        "deep_research": _stage_payload(store, run_id, "DEEP_RESEARCH", subject_id),
        "full_sec_forensic": _stage_payload(store, run_id, "FULL_SEC_FORENSIC", subject_id),
        "capital_structure_bridge": _stage_payload(store, run_id, STEP15_STAGE, subject_id),
        "standard_audit": _stage_payload(store, run_id, "STANDARD_AUDIT", subject_id),
        "adversarial_audit": _stage_payload(store, run_id, "ADVERSARIAL_AUDIT", subject_id),
        "reverse_valuation": candidate.get("reverse_valuation"),
        "research_evidence": research_artifact_payload,
        "material_claims": (candidate.get("research_result") or {}).get("material_claims"),
        "why_now": (candidate.get("research_result") or {}).get("why_now"),
        "why_not_priced": (candidate.get("research_result") or {}).get("why_not_priced"),
        "wakeup_event": (candidate.get("research_result") or {}).get("wakeup_event"),
        "failure_paths": candidate.get("failure_paths") or [],
        "technical_features": candidate.get("technical_features") or {},
        "current_market_data": {
            "price": candidate.get("price", candidate.get("last_price")),
            "market_cap": candidate.get("market_cap"),
            "average_dollar_volume": candidate.get("average_dollar_volume"),
        },
    }
    blinded = v8_primary.v8_blind_packet(raw)
    if not isinstance(blinded, dict):
        raise ValueError("V8 NEXT canonical packet scrub failed")
    blinded["packet_hash"] = canonical_hash(blinded)
    return blinded


def default_step15(evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "status": "INCOMPLETE", "current_shares": None, "instrument_potential_shares": None,
        "fully_diluted_shares": None, "likely_near_term_dilution_shares": None,
        "probable_financing_shares": None, "projected_near_term_fd_shares": None,
        "cash_runway_months": None, "financing_need": "UNVERIFIED", "toxic_red_flag": False,
        "source_instruments": [], "per_share_impact_summary": "unverified recorded/fake fixture",
        "unknowns": ["FD share bridge requires source-backed extraction"],
        "evidence_ids": list(evidence_ids), "grade_authority": False,
    }


def default_step16(packet: dict[str, Any], evidence_ids: list[str]) -> dict[str, Any]:
    claims = []
    for index, claim in enumerate(packet.get("material_claims") or []):
        if not isinstance(claim, dict):
            continue
        summary = str(claim.get("summary") or claim.get("statement") or f"claim {index + 1}")
        claim_eids = [str(item) for item in (claim.get("evidence_ids") or []) if str(item) in set(evidence_ids)]
        claims.append({
            "claim_id": f"C{index + 1}", "statement": summary,
            "verification_status": "UNVERIFIED", "economic_event_id": f"EVENT-{index + 1}",
            "independent_evidence_group": f"GROUP-{index + 1}", "evidence_ids": claim_eids,
        })
    return {
        "status": "INCOMPLETE", "atomic_claims": claims, "evidence_independence": "UNVERIFIED",
        "duplicate_economic_event_ids": [],
        "critical_unknowns": [{"statement": "atomic claim verification requires independent evidence review", "evidence_ids": []}],
        "value_realization_bridge_1_8w": {"status": "UNVERIFIED", "summary": "unverified fixture", "evidence_ids": []},
        "probability_provenance": "UNVERIFIED", "grade_authority": False,
    }


def default_step17_5() -> dict[str, Any]:
    return {
        "status": "INCOMPLETE",
        "assumptions": [{
            "assumption_id": item, "status": "UNVERIFIED", "rationale": "unverified fixture",
            "evidence_ids": [], "unknowns": ["requires independent audit"],
        } for item in ASSUMPTIONS],
        "grade_authority": False,
    }


def default_step18(current_price: float | None, evidence_ids: list[str]) -> dict[str, Any]:
    price = float(current_price) if isinstance(current_price, (int, float)) and not isinstance(current_price, bool) and current_price > 0 else 10.0
    zero_status = {key: {"status": "NOT_EVALUATED", "rationale": "unverified fixture", "evidence_ids": []} for key in LEGACY_HARD_GATES}
    next_status = {key: {"status": "NOT_EVALUATED", "rationale": "unverified fixture", "evidence_ids": []} for key in NEXT_HARD_GATES}
    return {
        "status": "INCOMPLETE",
        "score_components": {key: 0.0 for key in SCORE_MAX},
        "legacy_hard_gates": zero_status,
        "next_hard_gates": next_status,
        "grade_caps": [{"code": "FIXTURE_UNVERIFIED", "max_grade": "NOT_CERTIFIABLE", "rationale": "no model-backed certification", "evidence_ids": []}],
        "independent_improvement_axes": [], "valuation_method_count": 0,
        "scenarios": [
            {"scenario": "BEAR", "probability_pct": 30, "price_or_value": price * 0.8, "assumption": "fixture", "evidence_ids": []},
            {"scenario": "BASE", "probability_pct": 50, "price_or_value": price, "assumption": "fixture", "evidence_ids": []},
            {"scenario": "BULL", "probability_pct": 20, "price_or_value": price * 1.2, "assumption": "fixture", "evidence_ids": []},
        ],
        "probability_provenance": {"class": "UNVERIFIED", "summary": "fixture", "evidence_ids": []},
        "target_reverse_engineered": False, "toxic_red_flag": False,
        "b_plus_devil_advocate": ["Evidence remains unverified."],
        "why_not_one_grade_higher": ["Critical certification evidence remains unverified."],
        "score_reset_from_zero": True, "discovery_score_used": False, "pre_a_metadata_used": False,
        "candidate_shortage_influenced_grade": False, "grade_authority": False,
    }


def _cap_grade(grade: str, maximum: str) -> str:
    if maximum == "NOT_CERTIFIABLE":
        return grade
    return maximum if GRADE_ORDER.get(grade, -99) > GRADE_ORDER.get(maximum, -99) else grade


def finalize_certification(
    draft: dict[str, Any],
    assumption_audit: dict[str, Any],
    atomic_audit: dict[str, Any],
    fd_bridge: dict[str, Any],
    packet: dict[str, Any],
    current_price: float | None,
    evidence_ids: list[str],
    policy_version: str,
    policy_hash: str,
) -> dict[str, Any]:
    """Python-owned Step-18 grading and arithmetic authority."""
    allowed = sorted(set(str(item) for item in evidence_ids))
    lineage_failures = evidence_reference_failures(draft, allowed)
    lineage_failures += evidence_reference_failures(assumption_audit, allowed)
    lineage_failures += evidence_reference_failures(atomic_audit, allowed)
    lineage_failures += evidence_reference_failures(fd_bridge, allowed)

    score_components: dict[str, float] = {}
    score_errors: list[str] = []
    for key, maximum in SCORE_MAX.items():
        raw = (draft.get("score_components") or {}).get(key) if isinstance(draft, dict) else None
        if not isinstance(raw, (int, float)) or isinstance(raw, bool) or not math.isfinite(float(raw)) or not 0 <= float(raw) <= maximum:
            score_errors.append(f"INVALID_SCORE_{key.upper()}")
            score_components[key] = 0.0
        else:
            score_components[key] = float(raw)
    raw_score = sum(score_components.values())
    normalized = raw_score / 105.0 * 100.0
    grade = "A" if normalized >= 85 else "A-" if normalized >= 80 else "B+" if normalized >= 72 else "B"

    active_caps: list[str] = []
    reasons: list[str] = []
    not_certifiable = str(draft.get("status") or "") != "COMPLETE" or bool(lineage_failures or score_errors)

    assumption_map = {
        str(item.get("assumption_id")): item
        for item in (assumption_audit.get("assumptions") or [])
        if isinstance(item, dict)
    }
    missing_assumptions = [item for item in ASSUMPTIONS if item not in assumption_map]
    critical_unknowns = [
        item for item in ASSUMPTIONS
        if str((assumption_map.get(item) or {}).get("status") or "") in {"UNVERIFIED", "CONFLICT"}
    ]
    if str(assumption_audit.get("status") or "") != "COMPLETE" or missing_assumptions:
        not_certifiable = True
        reasons.append("STEP17_5_INCOMPLETE")
    if critical_unknowns:
        active_caps.append("CRITICAL_ASSUMPTION_UNKNOWN_MAX_B_PLUS")
        grade = _cap_grade(grade, "B+")

    atomic_complete = str(atomic_audit.get("status") or "") == "COMPLETE"
    atomic_unknown = bool(atomic_audit.get("critical_unknowns"))
    atomic_independence = str(atomic_audit.get("evidence_independence") or "")
    duplicate_events = list(atomic_audit.get("duplicate_economic_event_ids") or [])
    if not atomic_complete:
        not_certifiable = True
        reasons.append("STEP16_ATOMIC_AUDIT_INCOMPLETE")
    if atomic_unknown or atomic_independence != "PASS" or duplicate_events:
        active_caps.append("ATOMIC_CLAIM_OR_EVIDENCE_INDEPENDENCE_MAX_B_PLUS")
        grade = _cap_grade(grade, "B+")

    if str(fd_bridge.get("status") or "") != "COMPLETE" or fd_bridge.get("validation_failures"):
        active_caps.append("FD_SHARE_BRIDGE_MAX_B_PLUS")
        grade = _cap_grade(grade, "B+")
    if bool(fd_bridge.get("toxic_red_flag")) or bool(draft.get("toxic_red_flag")):
        grade = "EXCLUDE"
        active_caps.append("TOXIC_DILUTION_EXCLUDE")
    if bool(draft.get("target_reverse_engineered")):
        grade = "EXCLUDE"
        active_caps.append("TARGET_REVERSE_ENGINEERED_EXCLUDE")

    legacy_gates = draft.get("legacy_hard_gates") if isinstance(draft.get("legacy_hard_gates"), dict) else {}
    failed_legacy = [key for key in LEGACY_HARD_GATES if str((legacy_gates.get(key) or {}).get("status") or "") != "PASS"]
    if failed_legacy:
        active_caps.append("LEGACY_HARD_GATE_MAX_B_PLUS")
        grade = _cap_grade(grade, "B+")

    next_gates = draft.get("next_hard_gates") if isinstance(draft.get("next_hard_gates"), dict) else {}
    failed_next = [key for key in NEXT_HARD_GATES if str((next_gates.get(key) or {}).get("status") or "") != "PASS"]
    if failed_next:
        active_caps.append("V8_NEXT_HARD_GATE_MAX_B_PLUS")
        grade = _cap_grade(grade, "B+")

    axes = [item for item in (draft.get("independent_improvement_axes") or []) if isinstance(item, dict)]
    unique_events = {str(item.get("economic_event_id") or "") for item in axes if str(item.get("economic_event_id") or "")}
    independent_axis_count = len(unique_events)
    if independent_axis_count < 3:
        active_caps.append("INDEPENDENT_AXES_LT3_MAX_B_PLUS")
        grade = _cap_grade(grade, "B+")
    elif independent_axis_count == 3 and grade == "A":
        active_caps.append("INDEPENDENT_AXES_3_MAX_A_MINUS")
        grade = "A-"

    valuation_methods = int(draft.get("valuation_method_count") or 0) if isinstance(draft.get("valuation_method_count"), int) else 0
    if valuation_methods < 2:
        active_caps.append("VALUATION_METHODS_LT2_MAX_B_PLUS")
        grade = _cap_grade(grade, "B+")

    provenance = draft.get("probability_provenance") if isinstance(draft.get("probability_provenance"), dict) else {}
    provenance_class = str(provenance.get("class") or "UNVERIFIED")
    if provenance_class not in {"DATA_BACKED", "CALIBRATED_RANGE"}:
        active_caps.append("PROBABILITY_PROVENANCE_MAX_B_PLUS")
        grade = _cap_grade(grade, "B+")

    scenarios = [item for item in (draft.get("scenarios") or []) if isinstance(item, dict)]
    labels = {str(item.get("scenario") or "") for item in scenarios}
    probabilities = [float(item.get("probability_pct")) for item in scenarios if isinstance(item.get("probability_pct"), (int, float)) and not isinstance(item.get("probability_pct"), bool)]
    probability_sum = sum(probabilities)
    coarse_probability = len(probabilities) == 3 and all(math.isclose(value / 5.0, round(value / 5.0), abs_tol=1e-8) for value in probabilities)
    scenario_valid = labels == {"BEAR", "BASE", "BULL"} and len(probabilities) == 3 and math.isclose(probability_sum, 100.0, abs_tol=1e-6) and coarse_probability
    pw_ev = None
    price = float(current_price) if isinstance(current_price, (int, float)) and not isinstance(current_price, bool) and current_price > 0 else None
    if scenario_valid and price is not None:
        try:
            pw_ev = sum((float(item["probability_pct"]) / 100.0) * (float(item["price_or_value"]) / price - 1.0) for item in scenarios)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pw_ev = None
    if not scenario_valid or pw_ev is None:
        active_caps.append("PW_EV_UNREPRODUCIBLE_MAX_B_PLUS")
        grade = _cap_grade(grade, "B+")
    elif pw_ev <= 0 and grade in {"A", "A-"}:
        active_caps.append("PW_EV_NOT_POSITIVE_MAX_B_PLUS")
        grade = "B+"

    for cap in draft.get("grade_caps") or []:
        if not isinstance(cap, dict):
            continue
        code = str(cap.get("code") or "MODEL_GRADE_CAP")
        maximum = str(cap.get("max_grade") or "NOT_CERTIFIABLE")
        active_caps.append(code)
        if maximum == "NOT_CERTIFIABLE":
            not_certifiable = True
        elif maximum in GRADE_ORDER:
            grade = _cap_grade(grade, maximum)

    if lineage_failures:
        reasons.append("EVIDENCE_LINEAGE_FAILURE")
    if score_errors:
        reasons.append("SCORE_COMPONENT_INVALID")

    if not_certifiable and grade not in {"EXCLUDE"}:
        certification_status = "NOT_CERTIFIABLE"
    else:
        certification_status = {
            "A": "A_CERTIFIED", "A-": "A_MINUS_CERTIFIED", "B+": "B_PLUS_ONLY",
            "B": "B_ONLY", "EXCLUDE": "EXCLUDE",
        }[grade]

    why_not = [str(item) for item in (draft.get("why_not_one_grade_higher") or []) if str(item).strip()]
    if grade != "A" and not why_not:
        why_not = sorted(set(reasons + active_caps)) or ["One or more certification conditions are not satisfied."]
    if grade == "A" and not why_not:
        why_not = ["A is the highest Research Grade."]

    return {
        "source_sha256": policy_hash,
        "policy_version": policy_version,
        "grade_authority": "V8_NEXT_STEP18_CANONICAL",
        "certification_status": certification_status,
        "research_grade": grade,
        "raw_score": round(raw_score, 6),
        "normalized_score": round(normalized, 6),
        "score_components": score_components,
        "score_reset_from_zero": True,
        "discovery_score_used": False,
        "pre_a_metadata_used": False,
        "candidate_shortage_influenced_grade": False,
        "step17_5_complete": str(assumption_audit.get("status") or "") == "COMPLETE" and not missing_assumptions,
        "critical_unknown_count": len(critical_unknowns) + len(atomic_audit.get("critical_unknowns") or []),
        "critical_unknowns": sorted(set(critical_unknowns + [str((item or {}).get("statement") or "atomic_unknown") for item in atomic_audit.get("critical_unknowns") or [] if isinstance(item, dict)])),
        "legacy_hard_gate_statuses": {key: str((legacy_gates.get(key) or {}).get("status") or "NOT_EVALUATED") for key in LEGACY_HARD_GATES},
        "hard_gate_statuses": {key: str((next_gates.get(key) or {}).get("status") or "NOT_EVALUATED") for key in NEXT_HARD_GATES},
        "active_grade_caps": sorted(set(active_caps)),
        "independent_improvement_axis_count": independent_axis_count,
        "valuation_method_count": valuation_methods,
        "probability_provenance": provenance_class,
        "probability_sum_pct": round(probability_sum, 6),
        "pw_ev": None if pw_ev is None else round(pw_ev, 8),
        "scenarios": scenarios,
        "fd_share_bridge_hash": canonical_hash(fd_bridge),
        "atomic_audit_hash": canonical_hash(atomic_audit),
        "step17_5_hash": canonical_hash(assumption_audit),
        "certification_packet": {"packet_hash": str(packet.get("packet_hash") or canonical_hash(packet))},
        "evidence_ids": allowed,
        "lineage_failures": sorted(set(lineage_failures)),
        "why_not_one_grade_higher": why_not,
        "b_plus_devil_advocate": [str(item) for item in (draft.get("b_plus_devil_advocate") or []) if str(item).strip()],
        "python_grade_engine": "V8_NEXT_STEP18_PYTHON_AUTHORITY_V1",
    }


def research_validator(
    fd_bridge: dict[str, Any] | None,
    atomic_audit: dict[str, Any] | None,
    packet: dict[str, Any] | None,
    assumption_audit: dict[str, Any] | None,
    certification: dict[str, Any] | None,
    certification_validation_failures: list[str],
) -> dict[str, Any]:
    """Pure Step-20-style validator.  It never creates a grade or thesis."""
    checks = {
        "fd_share_bridge": bool(isinstance(fd_bridge, dict) and fd_bridge.get("arithmetic_authority") == "PYTHON_V8_NEXT_FD_BRIDGE_V1"),
        "atomic_claim_audit": bool(isinstance(atomic_audit, dict) and atomic_audit.get("grade_authority") in {False, "NO", "FALSE"}),
        "canonical_packet": bool(isinstance(packet, dict) and packet.get("packet_hash") == canonical_hash({k: v for k, v in packet.items() if k != "packet_hash"})),
        "step17_5": bool(isinstance(assumption_audit, dict) and assumption_audit.get("grade_authority") in {False, "NO", "FALSE"}),
        "certification_receipt": bool(isinstance(certification, dict) and not certification_validation_failures),
    }
    if not checks["fd_share_bridge"]:
        route = "RETURN_TO_STEP15"
    elif not checks["atomic_claim_audit"]:
        route = "RETURN_TO_STEP16"
    elif not checks["canonical_packet"]:
        route = "RETURN_TO_STEP17"
    elif not checks["step17_5"]:
        route = "RETURN_TO_STEP17_5"
    elif not checks["certification_receipt"]:
        route = "RETURN_TO_STEP18"
    else:
        route = "PASS"
    return {
        "status": "PASS" if route == "PASS" else "RESEARCH_REQUIRED",
        "route": route,
        "checks": checks,
        "certification_validation_failures": list(certification_validation_failures),
        "pure_validator": True,
        "grade_authority": False,
        "execution_authority": False,
    }
