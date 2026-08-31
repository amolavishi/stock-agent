from __future__ import annotations

import unittest

from stock_agent.gates import CapitalPrescreenGate, GateDecision
from stock_agent.models import EffectiveRuleSet, RawArtifact, canonical_hash
from stock_agent.v8_primary import (
    V8_DISCOVERY_LANES,
    V8CheapSECProviderProxy,
    assert_pre18_grade_firewall,
    build_v8_discovery_contract,
    normalize_v8_cheap_sec_payload,
    v8_blind_packet,
)


class V8PrimaryPolicyTests(unittest.TestCase):
    def test_run008_partial_cheap_sec_becomes_evidence_debt_not_silent_drop(self):
        normalized = normalize_v8_cheap_sec_payload({
            "extraction_status": "INCOMPLETE",
            "active_atm": False,
        })
        self.assertEqual(normalized["extraction_status"], "PARTIAL")
        self.assertEqual(normalized["active_atm"], False)
        for field in CapitalPrescreenGate.CANONICAL_FIELDS:
            self.assertIn(field, normalized)
        self.assertIn("full_sec_forensic_required", normalized["v8_evidence_debt"])
        gate = CapitalPrescreenGate().evaluate(
            {**normalized, "complete": False, "allow_full_forensic_escalation": True},
            EffectiveRuleSet(),
        )
        self.assertEqual(gate.decision, GateDecision.PASS_WITH_CONSTRAINTS)

    def test_explicit_toxic_convertible_remains_fatal_reject(self):
        normalized = normalize_v8_cheap_sec_payload({
            "extraction_status": "PARTIAL",
            "toxic_convertible": True,
        })
        gate = CapitalPrescreenGate().evaluate(
            {**normalized, "complete": False, "allow_full_forensic_escalation": True},
            EffectiveRuleSet(),
        )
        self.assertEqual(gate.decision, GateDecision.REJECT)

    def test_blind_packet_removes_discovery_grade_and_execution_anchors(self):
        source = {
            "ticker": "TEST",
            "discovery_priority_score": 97,
            "nested": {
                "discovery_rank": 1,
                "research_grade": "A",
                "target_price": 99,
                "facts": {"revenue": 10},
            },
        }
        blinded = v8_blind_packet(source)
        self.assertEqual(blinded["ticker"], "TEST")
        self.assertEqual(blinded["nested"]["facts"], {"revenue": 10})
        self.assertNotIn("discovery_priority_score", blinded)
        self.assertNotIn("discovery_rank", blinded["nested"])
        self.assertNotIn("research_grade", blinded["nested"])
        self.assertNotIn("target_price", blinded["nested"])

    def test_pre18_grade_firewall_rejects_grade_or_action(self):
        with self.assertRaises(ValueError):
            assert_pre18_grade_firewall({"research_grade": "A-"})
        with self.assertRaises(ValueError):
            assert_pre18_grade_firewall({"final_allocation_action": "STARTER"})
        assert_pre18_grade_firewall({"discovery_priority_score": 88, "unknowns": ["catalyst"]})

    def test_v8_contract_contains_all_02_to_14_lanes_without_grade(self):
        packet = build_v8_discovery_contract(199)
        self.assertEqual(set(V8_DISCOVERY_LANES), {f"{n:02d}" for n in range(2, 15)})
        self.assertEqual(packet["discovery_candidate_count"], 199)
        self.assertFalse(packet["research_grade_allowed"])
        self.assertFalse(packet["grade_relaxation_allowed"])
        self.assertTrue(packet["mandatory_bottom_up"])
        self.assertTrue(packet["unknowns_become_evidence_debt"])

    def test_proxy_synthesizes_partial_packet_when_provider_has_no_cheap_builder(self):
        class Delegate:
            provider_name = "fake-sec"

        proxy = V8CheapSECProviderProxy(Delegate())
        payload = {"x": 1}
        a = RawArtifact("sub", "fake-sec", "SEC_SUBMISSIONS", "XYZ", "2026-08-31T00:00:00Z", payload, canonical_hash(payload), "2026-08-31T00:00:00Z", "2026-08-31T00:01:00Z")
        b = RawArtifact("facts", "fake-sec", "SEC_FACTS", "XYZ", "2026-08-31T00:00:00Z", payload, canonical_hash(payload), "2026-08-31T00:00:00Z", "2026-08-31T00:01:00Z")
        out = proxy.fetch_cheap_facts({"security_id": "XYZ"}, a, b)
        self.assertEqual(out.payload["extraction_status"], "PARTIAL")
        self.assertIn("XYZ", proxy.normalized_candidates)
        for field in CapitalPrescreenGate.CANONICAL_FIELDS:
            self.assertEqual(CapitalPrescreenGate.normalize_tri_state(out.payload[field]), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
