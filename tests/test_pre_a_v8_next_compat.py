from __future__ import annotations

import unittest

from stock_agent.models import canonical_hash
from stock_agent.pre_a_source_v2 import _certification_grade
from stock_agent.v8_next_successor import V8_NEXT_POLICY_HASH, V8_NEXT_POLICY_VERSION


SCORE_MAX = {
    "catalyst_strength": 25.0, "time_immediacy": 15.0, "numeric_evidence": 15.0,
    "supply_demand": 10.0, "price_stage_fit": 15.0, "strategic_fit": 15.0,
    "expected_value": 10.0,
}


def valid_b_plus() -> dict:
    factor = 0.75
    components = {key: maximum * factor for key, maximum in SCORE_MAX.items()}
    return {
        "source_sha256": V8_NEXT_POLICY_HASH,
        "policy_version": V8_NEXT_POLICY_VERSION,
        "grade_authority": "V8_NEXT_STEP18_CANONICAL",
        "certification_status": "B_PLUS_ONLY",
        "discovery_score_used": False,
        "pre_a_metadata_used": False,
        "candidate_shortage_influenced_grade": False,
        "score_reset_from_zero": True,
        "research_grade": "B+",
        "raw_score": sum(components.values()),
        "normalized_score": 75.0,
        "score_components": components,
        "why_not_one_grade_higher": ["A/A- robustness not fully proven"],
        "critical_unknown_count": 1,
        "critical_unknowns": ["one unresolved non-A-grade assumption"],
        "step17_5_complete": False,
        "hard_gate_statuses": {},
        "legacy_hard_gate_statuses": {},
        "active_grade_caps": ["CRITICAL_ASSUMPTION_UNKNOWN_MAX_B_PLUS"],
        "lineage_failures": [],
        "evidence_ids": ["E1"],
        "python_grade_engine": "V8_NEXT_CERTIFICATION_ENGINE_V1.1",
        "certification_packet": {"packet_hash": canonical_hash({"ticker": "XYZ"})},
    }


class PreAV8NextCompatibilityTests(unittest.TestCase):
    def test_v8_next_b_plus_receipt_is_readable_by_pre_a(self):
        self.assertEqual(_certification_grade(valid_b_plus()), "B+")

    def test_v8_next_receipt_rejects_pre_a_or_shortage_contamination(self):
        base = valid_b_plus()
        contaminated = dict(base, pre_a_metadata_used=True)
        self.assertIsNone(_certification_grade(contaminated))
        quota = dict(base, candidate_shortage_influenced_grade=True)
        self.assertIsNone(_certification_grade(quota))

    def test_not_certifiable_b_plus_cannot_enter_pre_a(self):
        receipt = valid_b_plus()
        receipt["certification_status"] = "NOT_CERTIFIABLE"
        self.assertIsNone(_certification_grade(receipt))


if __name__ == "__main__":
    unittest.main()
