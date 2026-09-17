from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from jsonschema import validate

from stock_agent import v8_4_discovery_consistency as v84
from stock_agent import v8_main_discovery_coach as coach
from stock_agent import v8_pre_live_integrity_v20 as v20
from stock_agent.models import RunMode, canonical_hash
from stock_agent.store import SQLiteStore
from stock_agent.v8_pre_live_integrity_v202 import source_lineage_v202
from stock_agent.v8_semantic_core_v22 import (
    blind_certification_packet,
    candidate_conservation_v22,
    derive_authoritative_run_terminal_state,
    source_exhaustion_proof,
)
from stock_agent.v8_system_semantics_v21 import certification_terminal_state


class FunnelStore:
    def __init__(self, rows):
        self.rows = list(rows)
    def list_funnel(self, run_id):
        return list(self.rows)


def funnel(stage: str, count: int, details=None):
    return {
        "funnel_stage": stage,
        "count": count,
        "details_json": json.dumps(details or {}, sort_keys=True),
    }


def complete_proof() -> dict:
    return {
        "source_integrity_pass": True,
        "scanner_required_count": 13,
        "scanner_executed_count": 13,
        "scanner_validated_count": 13,
        "scanner_coverage_complete": True,
        "sentinel_complete": True,
        "candidate_engineering_failure_count": 0,
        "scanner_data_block_count": 0,
        "source_exhaustion_proven": True,
        "search_stop_allowed": True,
        "candidate_conservation_complete": True,
        "candidate_not_evaluated_count": 0,
        "proof_status": "PASS",
        "qualified_pool_authorized": False,
        "clean_no_trade_authorized": True,
    }


def record(store: SQLiteStore, run, stage: str, sid: str | None, value: dict, status: str = "SUCCEEDED") -> None:
    store.record_stage_result(
        run.run_id,
        None,
        stage,
        sid,
        value,
        [],
        store.dependency_hash([], run.rule_set.rule_set_hash, run.context_manifest_hash),
        store.current_evidence_epoch_for([]),
        status=status,
    )


class V8SemanticMutationTruthV224Tests(unittest.TestCase):
    def test_m01_engineering_failure_cannot_mutate_into_reject(self):
        store = SQLiteStore(":memory:")
        try:
            run = store.create_run(RunMode.HUNT_ONLY, store.resolve_rule_set(), "1" * 64, 0)
            record(store, run, "STOCK_DISCOVERY", None, {
                "candidates": [{"security_id": "M01", "recommended_discovery_action": "DEEP_DIVE_NOW"}]
            })
            record(store, run, "ADVERSARIAL_AUDIT", "M01", {
                "status": "INCOMPLETE",
                "audit_recommendation": "AUDIT_EVIDENCE_INCOMPLETE",
                "engineering_failure": True,
            })
            row = candidate_conservation_v22(SimpleNamespace(store=store, _v18_candidate_failures={}), run.run_id)[0]
            self.assertEqual(row["state"], "ENGINEERING_FAILURE")
            self.assertNotEqual(row["state"], "REJECT")
            self.assertFalse(row["investment_reject"])
        finally:
            store.close()

    def test_m02_b_plus_cannot_mutate_into_not_evaluated(self):
        state, _ = certification_terminal_state("B+", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False)
        self.assertEqual(state, "NEXT_STAGE")
        self.assertNotEqual(state, "NOT_EVALUATED")

    def test_m03_b_cannot_mutate_into_reject(self):
        state, _ = certification_terminal_state("B", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False)
        self.assertEqual(state, "WATCH")
        self.assertNotEqual(state, "REJECT")

    def test_m04_exclude_cannot_mutate_into_not_evaluated(self):
        state, _ = certification_terminal_state("EXCLUDE", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False)
        self.assertEqual(state, "REJECT")
        self.assertNotEqual(state, "NOT_EVALUATED")

    def test_m05_unknown_is_valid_discovery_uncertainty_not_fail(self):
        schema = v84._patch_schema(v20._scanner_schema_v13())
        candidate_schema = schema["properties"]["candidates"]["items"]
        candidate = {
            "security_id": "M05",
            "discovery_priority_score": 50,
            "signal_strength": "UNKNOWN",
            "research_value": "HIGH",
            "recommended_discovery_action": "DEEP_DIVE_SECONDARY",
            "rationale": "decision-critical evidence remains unresolved",
            "strengths": [],
            "weaknesses": [],
            "unknowns": ["contract economics not yet verified"],
            "verification_questions": ["verify contract economics"],
            "cheap_hard_gate_status": "UNKNOWN",
            "partial_signal": True,
            "failure_class": "DISCOVERY_INSUFFICIENT",
            "strategy_evidence": [],
        }
        validate(candidate, candidate_schema)
        self.assertEqual(candidate["signal_strength"], "UNKNOWN")
        self.assertNotEqual(candidate["cheap_hard_gate_status"], "FAIL")
        self.assertNotEqual(candidate["recommended_discovery_action"], "EXCLUDE")

    def test_m06_operational_probe_threshold_cannot_mutate_into_source_exhaustion(self):
        rows = [
            funnel("RAW_UNIVERSE", 3000),
            funnel("ADV_PROBED", 1000),
            funnel("ADV_NOT_EVALUATED", 2000),
            funnel("V8_4_UNIVERSE_SCOPE", 3000, {
                "scope_claim": "FULL_STRATEGY_UNIVERSE_SCAN",
                "full_scope_validated": True,
            }),
        ]
        store = FunnelStore(rows)
        proof = source_exhaustion_proof(store, "RUN", 600)
        legacy_exhausted, legacy_details = v20._provider_exhaustion_v20(store, "RUN", 600)
        self.assertTrue(proof["minimum_operational_probe_met"])
        self.assertFalse(proof["source_exhausted"])
        self.assertFalse(legacy_exhausted)
        self.assertFalse(legacy_details["operational_probe_threshold_is_source_exhaustion"])

    def test_m07_no_qualified_candidate_string_cannot_unconditionally_mutate_into_no_trade(self):
        proof = complete_proof()
        proof["source_exhaustion_proven"] = False
        proof["search_stop_allowed"] = False
        proof["proof_status"] = "INCOMPLETE"
        proof["clean_no_trade_authorized"] = False
        terminal, _ = derive_authoritative_run_terminal_state("NO_QUALIFIED_CANDIDATE", RunMode.HUNT_ONLY, proof)
        self.assertEqual(terminal, "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT")
        self.assertNotEqual(terminal, "NO_TRADE")

    def test_m08_pre_a_high_cannot_boost_step18_input(self):
        facts = {"ticker": "M08", "facts": {"revenue_delta": 0.4}, "evidence_ids": ["E1"]}
        high = blind_certification_packet({**facts, "pre_a_status": "PRE_A_HIGH", "promotion_readiness": "PRE_A", "a_trajectory": "HIGH"})
        none = blind_certification_packet({**facts, "pre_a_status": "NONE", "promotion_readiness": "NONE", "a_trajectory": "NONE"})
        self.assertEqual(high, none)
        self.assertEqual(canonical_hash(high), canonical_hash(none))

    def test_m09_discovery_rank_cannot_boost_step18_input(self):
        facts = {"ticker": "M09", "facts": {"revenue_delta": 0.4}, "evidence_ids": ["E1"]}
        first = blind_certification_packet({**facts, "discovery_priority_score": 99, "discovery_rank": 1})
        last = blind_certification_packet({**facts, "discovery_priority_score": 1, "discovery_rank": 999})
        self.assertEqual(first, last)
        self.assertEqual(canonical_hash(first), canonical_hash(last))

    def test_m10_fallback_payload_cannot_mutate_into_scanner_executed(self):
        legacy = coach._default_scanner("02", 100)
        hardened = v20._default_scanner_v13("02", 100)
        self.assertEqual(legacy["execution_status"], "PARTIAL")
        self.assertEqual(hardened["execution_status"], "PARTIAL")
        self.assertFalse(hardened["source_exhaustion"])
        self.assertEqual(hardened["source_exhaustion_reason"], "DEFAULT_PAYLOAD_NOT_MODEL_EXECUTION")
        self.assertNotIn("model_call_executed", hardened)

    def test_m11_duplicate_reprint_sources_cannot_mutate_into_independent_evidence(self):
        parent = "M11-PARENT"
        sources = [
            {"source_class": "COMPANY_PR", "content": "original event"},
            {"source_class": "REUTERS", "content": "reworded event"},
            {"source_class": "YAHOO", "content": "second rewording"},
        ]
        origins = {source_lineage_v202(source, parent)[0] for source in sources}
        self.assertEqual(len(origins), 1)

    def test_m12_step20_return_cannot_mutate_into_qualified_candidate(self):
        state, _ = certification_terminal_state("A", step20_route="RETURN_TO_STEP17_5", expectation_gap_pass=True, has_evidence_debt=False)
        self.assertEqual(state, "NOT_EVALUATED")
        self.assertNotEqual(state, "PASS")

    def test_m13_incomplete_sentinel_cannot_mutate_into_clean_no_trade(self):
        proof = complete_proof()
        proof["sentinel_complete"] = False
        proof["proof_status"] = "INCOMPLETE"
        proof["clean_no_trade_authorized"] = False
        terminal, _ = derive_authoritative_run_terminal_state("NO_QUALIFIED_CANDIDATE", RunMode.HUNT_ONLY, proof)
        self.assertEqual(terminal, "NOT_EVALUABLE_DISCOVERY_COVERAGE")
        self.assertNotEqual(terminal, "NO_TRADE")


if __name__ == "__main__":
    unittest.main()
