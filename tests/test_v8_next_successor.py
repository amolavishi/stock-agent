from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from stock_agent.models import canonical_hash
from stock_agent.v8_next_successor import (
    V8_NEXT_MINIMUM_UNIQUE_TICKERS,
    V8_NEXT_POLICY_HASH,
    V8_NEXT_POLICY_VERSION,
    V8_NEXT_PREFERRED_UNIQUE_TICKERS,
    build_v8_next_discovery_contract,
    validate_v8_next_certification,
)


class V8NextSuccessorTests(unittest.TestCase):
    def _cert(self, grade: str = "A-", score: float = 82.0) -> dict:
        return {
            "source_sha256": V8_NEXT_POLICY_HASH,
            "policy_version": V8_NEXT_POLICY_VERSION,
            "grade_authority": "V8_NEXT_STEP18_CANONICAL",
            "discovery_score_used": False,
            "pre_a_metadata_used": False,
            "score_reset_from_zero": True,
            "research_grade": grade,
            "normalized_score": score,
            "why_not_one_grade_higher": ["one remaining non-critical limitation"],
            "critical_unknown_count": 0,
            "step17_5_complete": True,
            "hard_gate_statuses": {
                "critical_claim_robustness": "PASS",
                "evidence_independence": "PASS",
                "valuation_fragility": "PASS",
                "realization_1_8w": "PASS",
                "dilution_adjusted_economics": "PASS",
                "probability_provenance": "PASS",
            },
            "active_grade_caps": [],
            "candidate_shortage_influenced_grade": False,
            "certification_packet": {"ticker": "XYZ", "unknowns": []},
        }

    def test_repository_policy_contract_hash_is_pinned(self):
        path = Path(__file__).resolve().parents[1] / "docs" / "v8_next" / "V8_NEXT_POLICY_CONTRACT_2026-09-01.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(canonical_hash(payload), V8_NEXT_POLICY_HASH)
        self.assertEqual(payload["contract_version"], V8_NEXT_POLICY_VERSION)

    def test_00a_breadth_is_coverage_floor_not_grade_quota(self):
        packet = build_v8_next_discovery_contract(50)
        self.assertEqual(packet["minimum_unique_tickers"], 150)
        self.assertEqual(packet["preferred_unique_tickers"], 200)
        self.assertTrue(packet["grade_quota_forbidden"])
        self.assertTrue(packet["a_count_is_output_not_target"])
        self.assertTrue(packet["stop_before_minimum_only_if_source_exhaustion_proven"])
        self.assertNotIn("target_verified_a_minus_or_better", packet)
        self.assertEqual(set(packet["mandatory_lanes"]), {f"{n:02d}" for n in range(2, 15)})
        self.assertEqual(V8_NEXT_MINIMUM_UNIQUE_TICKERS, 150)
        self.assertEqual(V8_NEXT_PREFERRED_UNIQUE_TICKERS, 200)

    def test_valid_a_minus_requires_next_policy_and_all_robustness_gates(self):
        grade, failures = validate_v8_next_certification(self._cert())
        self.assertEqual(grade, "A-")
        self.assertEqual(failures, [])

    def test_legacy_step18_hash_cannot_certify_under_v8_next(self):
        payload = self._cert()
        payload["source_sha256"] = "26fddaa0b0ddec166427d89a50ad0f272d06ee6d43a6b91995f45fefaa039528"
        grade, failures = validate_v8_next_certification(payload)
        self.assertEqual(grade, "A-")
        self.assertIn("V8_NEXT_POLICY_HASH", failures)

    def test_critical_unknown_or_fragile_gate_blocks_a_minus(self):
        payload = self._cert()
        payload["critical_unknown_count"] = 1
        payload["hard_gate_statuses"]["valuation_fragility"] = "FAIL"
        _grade, failures = validate_v8_next_certification(payload)
        self.assertIn("CRITICAL_UNKNOWN_PRESENT", failures)
        self.assertIn("NEXT_GATE_VALUATION_FRAGILITY", failures)

    def test_candidate_shortage_can_never_influence_a_grade(self):
        payload = self._cert("A", 90.0)
        payload["candidate_shortage_influenced_grade"] = True
        _grade, failures = validate_v8_next_certification(payload)
        self.assertIn("GRADE_QUOTA_INFLUENCE", failures)

    def test_pre_a_or_discovery_metadata_leak_is_rejected(self):
        payload = self._cert()
        payload["certification_packet"] = {
            "ticker": "XYZ",
            "nested": {"promotion_readiness": "PRE_A_HIGH", "discovery_score": 99},
        }
        _grade, failures = validate_v8_next_certification(payload)
        self.assertIn("FORBIDDEN_METADATA_LEAK", failures)

    def test_b_plus_is_valid_research_grade_without_a_authority(self):
        payload = self._cert("B+", 75.0)
        payload["critical_unknown_count"] = 2
        payload["step17_5_complete"] = False
        payload["hard_gate_statuses"] = {}
        grade, failures = validate_v8_next_certification(payload)
        self.assertEqual(grade, "B+")
        self.assertEqual(failures, [])

    def test_production_composition_uses_v8_next_successor(self):
        code = (
            "import json; "
            "from stock_agent.production import production_composition; "
            "print(json.dumps(production_composition(), sort_keys=True))"
        )
        out = subprocess.check_output([sys.executable, "-c", code], text=True)
        composition = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(composition["v8_policy_version"], V8_NEXT_POLICY_VERSION)
        self.assertEqual(composition["v8_ruleset_hash"], V8_NEXT_POLICY_HASH)
        self.assertEqual(composition["v8_next_successor_version"], "V8_NEXT_SUCCESSOR_V1.0")


if __name__ == "__main__":
    unittest.main()
