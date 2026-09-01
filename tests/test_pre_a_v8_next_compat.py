from __future__ import annotations

import unittest

from stock_agent.pre_a_source_v2 import _certification_grade
from stock_agent.v8_next_successor import V8_NEXT_POLICY_HASH, V8_NEXT_POLICY_VERSION


class PreAV8NextCompatibilityTests(unittest.TestCase):
    def test_v8_next_b_plus_receipt_is_readable_by_pre_a(self):
        receipt = {
            "source_sha256": V8_NEXT_POLICY_HASH,
            "policy_version": V8_NEXT_POLICY_VERSION,
            "grade_authority": "V8_NEXT_STEP18_CANONICAL",
            "discovery_score_used": False,
            "pre_a_metadata_used": False,
            "candidate_shortage_influenced_grade": False,
            "research_grade": "B+",
        }
        self.assertEqual(_certification_grade(receipt), "B+")

    def test_v8_next_receipt_rejects_pre_a_or_shortage_contamination(self):
        base = {
            "source_sha256": V8_NEXT_POLICY_HASH,
            "policy_version": V8_NEXT_POLICY_VERSION,
            "grade_authority": "V8_NEXT_STEP18_CANONICAL",
            "discovery_score_used": False,
            "pre_a_metadata_used": False,
            "candidate_shortage_influenced_grade": False,
            "research_grade": "B+",
        }
        contaminated = dict(base, pre_a_metadata_used=True)
        self.assertIsNone(_certification_grade(contaminated))
        quota = dict(base, candidate_shortage_influenced_grade=True)
        self.assertIsNone(_certification_grade(quota))


if __name__ == "__main__":
    unittest.main()
