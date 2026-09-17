from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from stock_agent.models import RunMode, canonical_hash
from stock_agent.store import SQLiteStore
from stock_agent.v8_semantic_core_v22 import (
    V8_SEMANTIC_CORE_VERSION,
    blind_certification_packet,
    candidate_conservation_v22,
    derive_authoritative_run_terminal_state,
    source_exhaustion_proof,
)


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


def record(store: SQLiteStore, run, stage: str, sid: str | None, value: dict) -> None:
    store.record_stage_result(
        run.run_id, None, stage, sid, value, [],
        store.dependency_hash([], run.rule_set.rule_set_hash, run.context_manifest_hash),
        store.current_evidence_epoch_for([]),
    )


class V8SemanticCoreV22Tests(unittest.TestCase):
    def test_operational_1000_probe_is_not_source_exhaustion_with_2000_unresolved(self):
        store = FunnelStore([
            funnel("RAW_UNIVERSE", 3000),
            funnel("ADV_PROBED", 1000),
            funnel("ADV_NOT_EVALUATED", 2000),
            funnel("V8_4_UNIVERSE_SCOPE", 3000, {
                "scope_claim": "FULL_STRATEGY_UNIVERSE_SCAN",
                "full_scope_validated": True,
            }),
        ])
        proof = source_exhaustion_proof(store, "RUN", 600)
        self.assertTrue(proof["minimum_operational_probe_met"])
        self.assertFalse(proof["source_exhausted"])
        self.assertTrue(proof["search_debt_remains"])
        self.assertFalse(proof["operational_probe_threshold_is_source_exhaustion"])

    def test_small_fully_probed_non_authoritative_subset_is_not_source_exhaustion(self):
        store = FunnelStore([
            funnel("RAW_UNIVERSE", 150),
            funnel("ADV_PROBED", 150),
            funnel("ADV_NOT_EVALUATED", 0),
            funnel("V8_4_UNIVERSE_SCOPE", 150, {
                "scope_claim": "BOUNDED_STRATEGY_UNIVERSE_SCAN",
                "full_scope_validated": False,
            }),
        ])
        proof = source_exhaustion_proof(store, "RUN", 150)
        self.assertFalse(proof["full_universe_reconciled"])
        self.assertFalse(proof["source_exhausted"])

    def test_full_reconciled_universe_can_prove_source_exhaustion(self):
        store = FunnelStore([
            funnel("RAW_UNIVERSE", 3000),
            funnel("ADV_PROBED", 600),
            funnel("ADV_NOT_EVALUATED", 0),
            funnel("V8_4_UNIVERSE_SCOPE", 600, {
                "scope_claim": "FULL_STRATEGY_UNIVERSE_SCAN",
                "full_scope_validated": True,
            }),
        ])
        proof = source_exhaustion_proof(store, "RUN", 600)
        self.assertTrue(proof["full_universe_reconciled"])
        self.assertTrue(proof["source_exhausted"])
        self.assertEqual(proof["proof_status"], "PASS")

    def test_provider_budget_exhaustion_does_not_become_source_exhaustion(self):
        store = FunnelStore([
            funnel("RAW_UNIVERSE", 3000),
            funnel("ADV_PROBED", 1000),
            funnel("ADV_NOT_EVALUATED", 2000),
            funnel("PROVIDER_BUDGET_EXHAUSTED", 1),
        ])
        proof = source_exhaustion_proof(store, "RUN", 600)
        self.assertTrue(proof["provider_budget_exhausted"])
        self.assertFalse(proof["provider_budget_exhausted_is_source_exhaustion"])
        self.assertFalse(proof["source_exhausted"])

    def test_audit_engineering_failure_never_becomes_investment_reject(self):
        store = SQLiteStore(":memory:")
        try:
            rules = store.resolve_rule_set()
            run = store.create_run(RunMode.HUNT_ONLY, rules, "c" * 64, 0)
            sid = "ENGFAIL"
            record(store, run, "STOCK_DISCOVERY", None, {
                "candidates": [{"security_id": sid, "recommended_discovery_action": "DEEP_DIVE_NOW"}]
            })
            record(store, run, "ADVERSARIAL_AUDIT", sid, {
                "status": "INCOMPLETE",
                "audit_recommendation": "AUDIT_EVIDENCE_INCOMPLETE",
                "engineering_failure": True,
                "unresolved_critical": ["ENGINEERING_FAILURE"],
            })
            agent = SimpleNamespace(store=store, _v18_candidate_failures={})
            ledger = candidate_conservation_v22(agent, run.run_id)
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["state"], "ENGINEERING_FAILURE")
            self.assertFalse(ledger[0]["investment_reject"])
            self.assertTrue(ledger[0]["information_failure"])
        finally:
            store.close()

    def test_audit_evidence_incomplete_without_engineering_failure_is_evidence_debt(self):
        store = SQLiteStore(":memory:")
        try:
            rules = store.resolve_rule_set()
            run = store.create_run(RunMode.HUNT_ONLY, rules, "d" * 64, 0)
            sid = "EVIDENCE"
            record(store, run, "STOCK_DISCOVERY", None, {
                "candidates": [{"security_id": sid, "recommended_discovery_action": "DEEP_DIVE_NOW"}]
            })
            record(store, run, "ADVERSARIAL_AUDIT", sid, {
                "status": "INCOMPLETE",
                "audit_recommendation": "AUDIT_EVIDENCE_INCOMPLETE",
                "engineering_failure": False,
            })
            agent = SimpleNamespace(store=store, _v18_candidate_failures={})
            ledger = candidate_conservation_v22(agent, run.run_id)
            self.assertEqual(ledger[0]["state"], "EVIDENCE_DEBT")
            self.assertFalse(ledger[0]["investment_reject"])
        finally:
            store.close()

    def test_unverified_discovery_exclude_is_not_investment_reject(self):
        store = SQLiteStore(":memory:")
        try:
            rules = store.resolve_rule_set()
            run = store.create_run(RunMode.HUNT_ONLY, rules, "e" * 64, 0)
            sid = "UNVERIFIEDX"
            record(store, run, "STOCK_DISCOVERY", None, {
                "candidates": [{"security_id": sid, "recommended_discovery_action": "EXCLUDE"}]
            })
            agent = SimpleNamespace(store=store, _v18_candidate_failures={})
            ledger = candidate_conservation_v22(agent, run.run_id)
            self.assertEqual(ledger[0]["state"], "EVIDENCE_DEBT")
            self.assertFalse(ledger[0]["investment_reject"])
        finally:
            store.close()

    def test_certification_firewall_is_hash_invariant_to_discovery_pre_a_and_quota_metadata(self):
        factual = {"ticker": "ABC", "facts": {"revenue_delta": 0.4}, "evidence_ids": ["E1"]}
        a = blind_certification_packet({
            **factual,
            "discovery_priority_score": 99,
            "discovery_rank": 1,
            "pre_a_status": "PRE_A_HIGH",
            "promotion_readiness": "PRE_A",
            "remaining_a_needed": 5,
        })
        b = blind_certification_packet({
            **factual,
            "discovery_priority_score": 1,
            "discovery_rank": 999,
            "pre_a_status": "NONE",
            "promotion_readiness": "NONE",
            "remaining_a_needed": 0,
        })
        self.assertEqual(a, b)
        self.assertEqual(canonical_hash(a), canonical_hash(b))

    def test_clean_no_trade_cannot_be_authorized_by_upstream_outcome_string_alone(self):
        incomplete = {
            "source_integrity_pass": True,
            "scanner_required_count": 13,
            "scanner_executed_count": 13,
            "scanner_validated_count": 13,
            "scanner_coverage_complete": True,
            "sentinel_complete": True,
            "source_exhaustion_proven": False,
            "search_stop_allowed": False,
            "candidate_conservation_complete": True,
            "candidate_engineering_failure_count": 0,
            "candidate_not_evaluated_count": 0,
            "proof_status": "INCOMPLETE",
            "clean_no_trade_authorized": False,
            "qualified_pool_authorized": False,
        }
        terminal, _ = derive_authoritative_run_terminal_state(
            "NO_QUALIFIED_CANDIDATE", RunMode.HUNT_ONLY, incomplete
        )
        self.assertEqual(terminal, "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT")

    def test_production_final_owner_remains_sentinel_and_semantic_core_is_in_place(self):
        from stock_agent.production import production_composition
        composition = production_composition()
        self.assertEqual(composition["runtime_class"], "V8PreLiveSentinelProductionStockAgent")
        self.assertEqual(composition["v8_semantic_core_version"], V8_SEMANTIC_CORE_VERSION)
        self.assertFalse(composition["discovery_recall_lite_runtime_installed"])


if __name__ == "__main__":
    unittest.main()
