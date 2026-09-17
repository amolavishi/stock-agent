from __future__ import annotations

import unittest
from types import SimpleNamespace

from stock_agent import v8_main_discovery_coach as coach
from stock_agent import v8_main_discovery_integrity as discovery_integrity
from stock_agent import v8_main_source_fidelity as source_fidelity
from stock_agent.models import RunMode
from stock_agent.store import SQLiteStore
from stock_agent.v8_semantic_core_v22 import candidate_conservation_v22


class V8SemanticFailureTruthTests(unittest.TestCase):
    def _run(self):
        store = SQLiteStore(":memory:")
        rules = store.resolve_rule_set()
        run = store.create_run(RunMode.HUNT_ONLY, rules, "f" * 64, 0)
        return store, run

    @staticmethod
    def _record(store, run, stage, sid, value, *, status="SUCCEEDED"):
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

    def _discover(self, store, run, sid):
        self._record(
            store,
            run,
            "STOCK_DISCOVERY",
            None,
            {"candidates": [{"security_id": sid, "recommended_discovery_action": "DEEP_DIVE_NOW"}]},
        )

    def test_failed_gate_row_with_reject_payload_is_not_investment_reject(self):
        store, run = self._run()
        try:
            sid = "FAILED_GATE"
            self._discover(store, run, sid)
            self._record(
                store,
                run,
                "EXPECTATION_GAP_GATE",
                sid,
                {"decision": "REJECT", "reason": "stale payload must not be issuer evidence"},
                status="FAILED",
            )
            ledger = candidate_conservation_v22(SimpleNamespace(store=store, _v18_candidate_failures={}), run.run_id)
            self.assertEqual(len(ledger), 1)
            self.assertNotEqual(ledger[0]["state"], "REJECT")
            self.assertFalse(ledger[0]["investment_reject"])
            self.assertIn(ledger[0]["state"], {"NOT_EVALUATED", "EVIDENCE_DEBT", "ENGINEERING_FAILURE"})
        finally:
            store.close()

    def test_failed_audit_row_with_challenges_continuation_is_not_investment_reject(self):
        store, run = self._run()
        try:
            sid = "FAILED_AUDIT"
            self._discover(store, run, sid)
            self._record(
                store,
                run,
                "ADVERSARIAL_AUDIT",
                sid,
                {"status": "INCOMPLETE", "audit_recommendation": "CHALLENGES_CONTINUATION"},
                status="FAILED",
            )
            ledger = candidate_conservation_v22(SimpleNamespace(store=store, _v18_candidate_failures={}), run.run_id)
            self.assertEqual(len(ledger), 1)
            self.assertNotEqual(ledger[0]["state"], "REJECT")
            self.assertFalse(ledger[0]["investment_reject"])
        finally:
            store.close()

    def test_explicit_failed_engineering_receipt_remains_engineering_failure(self):
        store, run = self._run()
        try:
            sid = "EXPLICIT_ENG"
            self._discover(store, run, sid)
            self._record(
                store,
                run,
                "CANDIDATE_ENGINEERING_FAILURE",
                sid,
                {"status": "ENGINEERING_FAILURE", "failed_stage": "DEEP_RESEARCH", "error_type": "TimeoutError"},
                status="FAILED",
            )
            ledger = candidate_conservation_v22(SimpleNamespace(store=store, _v18_candidate_failures={}), run.run_id)
            self.assertEqual(ledger[0]["state"], "ENGINEERING_FAILURE")
            self.assertFalse(ledger[0]["investment_reject"])
            self.assertTrue(ledger[0]["information_failure"])
        finally:
            store.close()

    def test_source_identity_is_reentrant_without_source_identity_guard(self):
        # Exercise the original module functions directly. Source fidelity must
        # be correct even when no later guard monkeypatch is consulted.
        expected = {sid: str(entry["sha256"]) for sid, entry in source_fidelity._scanner_entries().items()}
        discovery_integrity._PREPARED = False
        source_fidelity._PREPARED = True
        coach.V8_SCANNERS["08"]["sha256"] = "f" * 64

        discovery_integrity.prepare_v8_main_discovery_integrity()
        source_fidelity.prepare_v8_4_source_lock()

        for sid, sha in expected.items():
            self.assertEqual(coach.V8_SCANNERS[sid]["sha256"], sha, sid)

        # Repeated calls must repair subsequent drift despite _PREPARED=True.
        coach.V8_SCANNERS["08"]["sha256"] = "e" * 64
        source_fidelity.prepare_v8_4_source_lock()
        self.assertEqual(coach.V8_SCANNERS["08"]["sha256"], expected["08"])


if __name__ == "__main__":
    unittest.main()
