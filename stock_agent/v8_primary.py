"""Canonical V8 policy bridge for the daily PRIMARY runtime.

This module makes the V8 Grade Firewall the authoritative *research-process*
contract for PRIMARY without turning discovery priority into investment
authority. It intentionally reuses the existing Python gates for final
qualification/action while correcting the front-end failure mode exposed by
RUN-20260831-008: incomplete cheap SEC evidence must become evidence debt and
full-forensic escalation, not a silent candidate disappearance.

Canonical V8 invariants implemented here:

* Discovery Recall is broad; certification precision is narrow.
* Discovery Priority != Research Grade != PRE-A Readiness != Execution Action.
* 02~14 are HUNT_ONLY lanes. No A/A-/B+/B grade may be created there.
* UNKNOWN is evidence debt, not a negative fact.
* Cheap prescreen is fatal-veto only. Explicit hard exclusions still reject.
* Step 16-style adversarial verification is score/rank blind.
* Step 18-style certification must start from zero and remains downstream of
  full research/SEC/audit gates. This bridge does not manufacture a grade.
* If verified A-/A supply is scarce, search breadth expands; thresholds never
  relax and B+ is never promoted to satisfy a quota.

The exact source contract is pinned by docs/v8_canonical/SOURCE_MANIFEST.json.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from . import runtime as runtime_module
from .gates import CapitalPrescreenGate
from .models import RawArtifact, canonical_hash, utc_now


V8_PRIMARY_VERSION = "V8_PRIMARY_CANONICAL_V1.0"
V8_SOURCE_ARCHIVE = "STOCK_SCANNING_PROMPTS_V8_A_GRADE_PIPELINE(4).zip"
V8_SOURCE_README_SHA256 = "78296f4f098d5dfeb8f46a06099a442a3eff3eb9a2dcbe5ab8e34440b62d2f34"
TARGET_UNIQUE_TICKERS = 150
TARGET_DEEP_VERIFY = (15, 30)
# Retained only as a legacy constant for compatibility with historical tests
# and reports. It is forbidden from active Discovery/certification packets.
TARGET_VERIFIED_A_MINUS_OR_BETTER = 5

# Exact 02~14 lane identities from the authoritative V8 source manifest.
V8_DISCOVERY_LANES: dict[str, str] = {
    "02": "NON_AI_NON_SEMI_BROAD_BLIND",
    "03": "RECENT_IPO_BUSTED_IPO_REVALUATION",
    "04": "TURNAROUND_EARNINGS",
    "05": "POLICY_DEFENSE_NUCLEAR_URANIUM_CRITICAL_MINERALS_ENERGY_SECURITY",
    "06": "SPACE_DEFENSE_ISR_AEROSPACE_COMPONENTS",
    "07": "UNDERFOLLOWED_PROFITABILITY_IMPROVING_SMALLCAP",
    "08": "SECONDARY_BLOCK_ABSORPTION_RECOVERY",
    "09": "INSIDER_BUY_BUYBACK_DEFENSIVE_TURNAROUND",
    "10": "DEBT_REFINANCING_BANKRUPTCY_RISK_REMOVAL",
    "11": "POST_EARNINGS_REVISION_LAG",
    "12": "CUSTOMER_CONCENTRATION_BREAK_SECOND_LARGE_CUSTOMER",
    "13": "FINTECH_HEALTHCARE_NON_SEMI_SOFTWARE_ROTATION",
    "14": "AI_BOTTLENECK_EXPANSION_EXCEPTION",
}

# These keys are intrinsically non-authoritative Discovery/routing metadata.
# They are scrubbed by v8_blind_packet even when optional bootstrap patches
# have not run.  This makes Step16/17/17.5/18 blindness independent of import
# order and prevents quota/trajectory anchoring from reappearing in tests,
# notebooks, library imports, or future entry points.
_DISCOVERY_ONLY_KEYS = {
    "research_value", "signal_strength", "scanner_id", "scanner_name",
    "scanner_priority", "scanner_receipt", "secondary_status",
    "secondary_queue", "near_miss", "near_miss_status",
    "rejection_sentinel", "sentinel_history", "discovery_disposition",
    "recommended_discovery_action", "verification_path", "recheck_trigger",
    "expected_resolution", "expiry", "secondary_is_pre_a",
    "research_value_is_research_grade", "fatal_fail",
    "research_route_allowed", "why_not_deep_dive", "queue_status",
}
_GRADE_QUOTA_KEYS = {
    "target_verified_a_minus_or_better", "verified_a_minus_or_better_count",
    "verified_a_count", "verified_a_minus_count", "candidate_shortage",
    "grade_quota", "grade_target", "required_a_count", "remaining_a_needed",
}
_DISCOVERY_SCORE_KEYS = {
    "discovery_priority_score", "discovery_score", "discovery_rank",
    "primary_rank", "primary_score", "rank",
} | _DISCOVERY_ONLY_KEYS | _GRADE_QUOTA_KEYS
_GRADE_KEYS = {
    "research_grade", "primary_grade", "final_grade", "certification",
    "certification_score",
}
_EXECUTION_KEYS = {
    "authoritative_action", "primary_action", "final_allocation",
    "final_allocation_action", "position_shares", "current_position_shares",
    "risk_target_position_shares", "transaction_shares",
    "resulting_position_shares", "target_price", "entry_price", "stop_price",
}
_BLIND_KEYS = _DISCOVERY_SCORE_KEYS | _GRADE_KEYS | _EXECUTION_KEYS


def _contains_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in keys or _contains_key(item, keys):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_key(item, keys) for item in value)
    return False


def v8_blind_packet(value: Any) -> Any:
    """Remove Discovery anchoring, grade quota, prior grades and execution authority."""
    if isinstance(value, Mapping):
        return {
            str(key): v8_blind_packet(item)
            for key, item in value.items()
            if str(key).casefold() not in _BLIND_KEYS
        }
    if isinstance(value, list):
        return [v8_blind_packet(item) for item in value]
    if isinstance(value, tuple):
        return [v8_blind_packet(item) for item in value]
    return copy.deepcopy(value)


def assert_pre18_grade_firewall(value: Any) -> None:
    """02~17 may not emit Research Grade or execution authority."""
    if _contains_key(value, _GRADE_KEYS):
        raise ValueError("V8 grade firewall violation: Research Grade appeared before Step 18")
    if _contains_key(value, _EXECUTION_KEYS):
        raise ValueError("V8 authority firewall violation: execution field appeared before Step 19")


def _tri_state(value: Any) -> str:
    return CapitalPrescreenGate.normalize_tri_state(value)


def normalize_v8_cheap_sec_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Convert cheap-SEC incompleteness into explicit V8 evidence debt.

    UNKNOWN is never converted to FALSE. Explicit TRUE hard exclusions are
    preserved. The goal is to let CapitalPrescreenGate observe incomplete
    fields and escalate them instead of the runtime silently dropping the
    candidate before that gate.
    """
    normalized = dict(payload or {})
    debt = [str(item) for item in (normalized.get("v8_evidence_debt") or normalized.get("unknowns") or [])]
    for field in sorted(CapitalPrescreenGate.CANONICAL_FIELDS):
        if field not in normalized:
            normalized[field] = "UNKNOWN"
            debt.append(f"cheap_sec_missing:{field}")
        elif _tri_state(normalized[field]) == "UNKNOWN":
            debt.append(f"cheap_sec_unknown:{field}")

    original_status = str(normalized.get("extraction_status") or "").upper()
    all_known = all(_tri_state(normalized.get(field)) in {"TRUE", "FALSE"} for field in CapitalPrescreenGate.CANONICAL_FIELDS)
    if original_status == "COMPLETE" and all_known:
        normalized["extraction_status"] = "COMPLETE"
    else:
        normalized["extraction_status"] = "PARTIAL"
        debt.append("full_sec_forensic_required")

    normalized["v8_evidence_debt"] = sorted(set(debt))
    normalized["v8_prescreen_semantics"] = "FATAL_VETO_ONLY_UNKNOWN_ESCALATES"
    normalized["v8_primary_version"] = V8_PRIMARY_VERSION
    return normalized


class V8CheapSECProviderProxy:
    """Read-only SEC proxy that preserves UNKNOWN and explicit hard vetoes."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.provider_name = str(getattr(delegate, "provider_name", delegate.__class__.__name__))
        self.normalized_candidates: dict[str, tuple[str, ...]] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def fetch_cheap_facts(self, identity: Mapping[str, Any], submissions: RawArtifact, facts: RawArtifact) -> RawArtifact:
        builder = getattr(self._delegate, "fetch_cheap_facts", None)
        if callable(builder):
            artifact = builder(dict(identity), submissions, facts)
            base_payload = artifact.payload if isinstance(artifact.payload, Mapping) else {}
            provider = artifact.provider
            artifact_type = artifact.artifact_type
            subject_id = artifact.subject_id
            observed_at = artifact.observed_at
            source_observed_at = artifact.source_observed_at
            retrieved_at = artifact.retrieved_at
        else:
            subject_id = str(identity.get("security_id") or "") or None
            base_payload = {"security_id": subject_id, "unknowns": ["provider_missing_cheap_facts"]}
            provider = self.provider_name
            artifact_type = "SEC_CHEAP_FACTS"
            observed_at = submissions.observed_at or facts.observed_at or utc_now()
            source_observed_at = submissions.source_observed_at or facts.source_observed_at or observed_at
            retrieved_at = utc_now()

        normalized = normalize_v8_cheap_sec_payload(base_payload)
        sid = str(identity.get("security_id") or subject_id or "").upper()
        if sid:
            self.normalized_candidates[sid] = tuple(normalized.get("v8_evidence_debt") or ())
        payload_hash = canonical_hash(normalized)
        return RawArtifact(
            artifact_id=f"artifact-v8-cheap-sec-{payload_hash[:32]}",
            provider=provider,
            artifact_type=artifact_type or "SEC_CHEAP_FACTS",
            subject_id=subject_id or sid or None,
            observed_at=observed_at or source_observed_at or utc_now(),
            payload=normalized,
            payload_hash=payload_hash,
            source_observed_at=source_observed_at or observed_at,
            retrieved_at=retrieved_at or utc_now(),
        )


def build_v8_discovery_contract(candidate_count: int) -> dict[str, Any]:
    """Deterministic 00A/02~14 contract metadata, never a grade or grade quota."""
    count = max(0, int(candidate_count))
    packet = {
        "run_mode": "HUNT_ONLY_RECALL_FIRST",
        "v8_primary_version": V8_PRIMARY_VERSION,
        "source_archive": V8_SOURCE_ARCHIVE,
        "discovery_priority_is_research_grade": False,
        "research_grade_allowed": False,
        "mandatory_bottom_up": True,
        "market_regime_may_auto_exclude_idiosyncratic_candidate": False,
        "target_unique_tickers": TARGET_UNIQUE_TICKERS,
        "target_deep_verify_min": TARGET_DEEP_VERIFY[0],
        "target_deep_verify_max": TARGET_DEEP_VERIFY[1],
        "grade_quota_forbidden": True,
        "a_count_is_output_not_target": True,
        "candidate_shortage_may_only_expand_search": True,
        "candidate_shortage_may_never_relax_certification": True,
        "grade_relaxation_allowed": False,
        "discovery_candidate_count": count,
        "lanes": dict(V8_DISCOVERY_LANES),
        "mandatory_lanes": ["02", "11", "BOTTOM_UP_IDIOSYNCRATIC"],
        "weakness_first": True,
        "minimum_weaknesses_per_candidate": 3,
        "unknowns_become_evidence_debt": True,
        "full_sec_deferred_until_after_cheap_fatal_veto": True,
        "blind_verification_required": True,
        "score_reset_at_certification": True,
    }
    if _GRADE_QUOTA_KEYS.intersection(packet):
        raise ValueError("V8 grade quota leaked into Discovery contract")
    assert_pre18_grade_firewall(packet)
    return packet


def _decision_counts(rows: list[dict[str, Any]], stage: str) -> dict[str, int]:
    counts = {"PASS": 0, "PASS_WITH_CONSTRAINTS": 0, "REJECT": 0, "INSUFFICIENT_EVIDENCE": 0, "OTHER": 0}
    for row in rows:
        if str(row.get("stage")) != stage or str(row.get("status")) != "SUCCEEDED":
            continue
        try:
            payload = json.loads(row.get("result_json") or "{}")
        except (TypeError, ValueError):
            counts["OTHER"] += 1
            continue
        decision = str(payload.get("decision") or "OTHER")
        counts[decision if decision in counts else "OTHER"] += 1
    return counts


def install_v8_primary_policy() -> type:
    """Promote V8 process semantics into PRIMARY after Alpha V1.4 installs."""
    current_base = runtime_module.ProductionStockAgent
    if getattr(current_base, "v8_primary_version", None) == V8_PRIMARY_VERSION:
        return current_base

    class V8PrimaryProductionStockAgent(current_base):  # type: ignore[misc,valid-type]
        v8_primary_version = V8_PRIMARY_VERSION

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            provider = getattr(self.config, "sec_provider", None)
            if provider is not None and not isinstance(provider, V8CheapSECProviderProxy):
                self.config.sec_provider = V8CheapSECProviderProxy(provider)

        def _work_stage(
            self,
            run,
            stage: str,
            prompt_id: str,
            payload: dict[str, Any],
            subject_id: str | None,
            dependency_ids: list[str],
            context_inputs: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            # V8 Step 16 blindness applies to the untrusted/narrative packet.
            # Typed persisted receipts remain exact because changing them would
            # break the repository's content-hash lineage contract.
            if stage == "ADVERSARIAL_AUDIT":
                payload = copy.deepcopy(payload)
                payload["raw_input"] = v8_blind_packet(payload.get("raw_input", {}))
                if _contains_key(payload["raw_input"], _BLIND_KEYS):
                    raise ValueError("V8 blind verification packet still contains forbidden anchoring fields")
            result = super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            if stage in {
                "MARKET_ANALYSIS", "SECTOR_ANALYSIS", "STOCK_DISCOVERY",
                "CAPITAL_PRESCREEN", "CAP_FUNDAMENTAL_CHANGE",
                "CAP_CATALYST_EXPECTATION_RESEARCH", "CAP_DIRECTIONAL_PROBABILITY",
                "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "STANDARD_AUDIT",
                "ADVERSARIAL_AUDIT",
            }:
                assert_pre18_grade_firewall(result)
            return result

        def _run_strict(self, mode, data):
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            funnel = {str(row["funnel_stage"]): int(row["count"]) for row in self.store.list_funnel(run_id)}
            stage_rows = self.store.list_stage_results(run_id)
            discovered = int(funnel.get("STAGE_DISCOVERY_READY", funnel.get("STAGE_ELIGIBLE", 0)))
            prescreen = _decision_counts(stage_rows, "CAPITAL_PRESCREEN_GATE")
            prescreen_seen = sum(prescreen.values())
            research_queue = prescreen["PASS"] + prescreen["PASS_WITH_CONSTRAINTS"]
            debt_provider = getattr(self.config, "sec_provider", None)
            debt_map = getattr(debt_provider, "normalized_candidates", {}) if debt_provider is not None else {}
            debt_count = sum(bool(items) for items in debt_map.values())

            self.store.record_funnel(run_id, "V8_CANONICAL_PRIMARY", 1, build_v8_discovery_contract(discovered))
            # Legacy lane telemetry is intentionally labelled as coverage only;
            # actual SCANNER_EXECUTED receipts are owned by v8_main_* integrity.
            self.store.record_funnel(run_id, "V8_LANE_02_BROAD_BLIND", discovered, {
                "semantics": "coverage metadata only; LANE_TOUCHED != SCANNER_EXECUTED",
                "scanner_executed": False,
                "other_lanes": V8_DISCOVERY_LANES,
            })
            self.store.record_funnel(run_id, "V8_FATAL_VETO_REJECT", prescreen["REJECT"], {"decisions": prescreen})
            self.store.record_funnel(run_id, "V8_EVIDENCE_DEBT", debt_count, {
                "candidate_ids": sorted(str(key) for key, value in debt_map.items() if value)[:200],
                "semantics": "UNKNOWN -> research/full-forensic queue, never FALSE",
            })
            self.store.record_funnel(run_id, "V8_RESEARCH_QUEUE", research_queue, {"decisions": prescreen})
            self.store.record_funnel(run_id, "V8_GRADE_FIREWALL", 1, {
                "step18_grade_writer": "V8_NEXT_STEP18_CANONICAL",
                "pre18_grade_allowed": False,
                "blind_adversarial": True,
                "threshold_relaxation_allowed": False,
            })

            if discovered > 0 and prescreen_seen == 0 and not (
                int(funnel.get("SEC_PROVIDER_FAILURE", 0))
                or int(funnel.get("SEC_STALE_DATA", 0))
            ):
                self.store.record_funnel(run_id, "V8_PIPELINE_STARVATION", discovered, {
                    "reason": "discovered candidates produced no prescreen receipts",
                    "status": "ENGINEERING_INCIDENT",
                })
            else:
                self.store.record_funnel(run_id, "V8_PIPELINE_STARVATION", 0, {"status": "PASS"})
            return outcome

    runtime_module.ProductionStockAgent = V8PrimaryProductionStockAgent
    return V8PrimaryProductionStockAgent
