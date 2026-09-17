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


LEGACY_GATES = (
    "wake_up_1_8w", "independent_economic_improvement_axes", "numeric_expectation_gap",
    "why_now", "why_not_priced", "market_wakeup_mechanism", "extreme_bull_not_priced",
    "base_upside_economic", "bull_upside_additional_evidence", "target_not_reverse_engineered",
    "two_independent_valuation_methods", "scenario_probabilities_sum_100",
    "pw_ev_positive_meaningful", "execution_rr_not_arbitrary", "structural_asymmetry_separate",
    "full_sec_complete_non_toxic", "not_stage_3", "liquidity_pass", "market_data_fresh",
    "failure_scenarios_three_plus",
)
NEXT_GATES = (
    "critical_claim_robustness", "evidence_independence", "valuation_fragility",
    "realization_1_8w", "dilution_adjusted_economics", "probability_provenance",
)
SCORE_MAX = {
    "catalyst_strength": 25.0, "time_immediacy": 15.0, "numeric_evidence": 15.0,
    "supply_demand": 10.0, "price_stage_fit": 15.0, "strategic_fit": 15.0,
    "expected_value": 10.0,
}


class V8NextSuccessorTests(unittest.TestCase):
    def _cert(self, grade: str = "A-", score: float = 82.0) -> dict:
        factor = score / 100.0
        components = {key: maximum * factor for key, maximum in SCORE_MAX.items()}
        raw_score = sum(components.values())
        status = {
            "A": "A_CERTIFIED", "A-": "A_MINUS_CERTIFIED", "B+": "B_PLUS_ONLY",
            "B": "B_ONLY", "EXCLUDE": "EXCLUDE",
        }[grade]
        packet_hash = canonical_hash({"ticker": "XYZ", "unknowns": []})
        return {
            "source_sha256": V8_NEXT_POLICY_HASH,
            "policy_version": V8_NEXT_POLICY_VERSION,
            "grade_authority": "V8_NEXT_STEP18_CANONICAL",
            "certification_status": status,
            "discovery_score_used": False,
            "pre_a_metadata_used": False,
            "score_reset_from_zero": True,
            "candidate_shortage_influenced_grade": False,
            "research_grade": grade,
            "raw_score": raw_score,
            "normalized_score": score,
            "score_components": components,
            "why_not_one_grade_higher": ["one remaining non-critical limitation"] if grade != "A" else ["A is highest"],
            "critical_unknown_count": 0,
            "critical_unknowns": [],
            "step17_5_complete": True,
            "hard_gate_statuses": {key: "PASS" for key in NEXT_GATES},
            "legacy_hard_gate_statuses": {key: "PASS" for key in LEGACY_GATES},
            "active_grade_caps": [],
            "lineage_failures": [],
            "evidence_ids": ["E1"],
            "python_grade_engine": "V8_NEXT_CERTIFICATION_ENGINE_V1.1",
            "certification_packet": {"packet_hash": packet_hash},
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
        payload["critical_unknowns"] = ["valuation_fragility"]
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
            "packet_hash": canonical_hash({"ticker": "XYZ"}),
            "nested": {"promotion_readiness": "PRE_A_HIGH", "discovery_score": 99},
        }
        _grade, failures = validate_v8_next_certification(payload)
        self.assertIn("FORBIDDEN_METADATA_LEAK", failures)

    def test_b_plus_is_valid_research_grade_without_a_authority(self):
        payload = self._cert("B+", 75.0)
        payload["critical_unknown_count"] = 2
        payload["critical_unknowns"] = ["one", "two"]
        payload["step17_5_complete"] = False
        payload["hard_gate_statuses"] = {}
        grade, failures = validate_v8_next_certification(payload)
        self.assertEqual(grade, "B+")
        self.assertEqual(failures, [])

    def test_not_certifiable_surface_a_can_never_pass(self):
        payload = self._cert("A", 90.0)
        payload["certification_status"] = "NOT_CERTIFIABLE"
        _grade, failures = validate_v8_next_certification(payload)
        self.assertIn("CERTIFICATION_NOT_CERTIFIABLE", failures)
        self.assertIn("CERTIFICATION_STATUS_GRADE_MISMATCH", failures)

    def test_lineage_failure_blocks_surface_a(self):
        payload = self._cert("A", 90.0)
        payload["lineage_failures"] = ["E-FAKE"]
        _grade, failures = validate_v8_next_certification(payload)
        self.assertIn("EVIDENCE_LINEAGE_FAILURE", failures)

    def test_score_arithmetic_tamper_is_rejected(self):
        payload = self._cert("A", 90.0)
        payload["normalized_score"] = 99.0
        _grade, failures = validate_v8_next_certification(payload)
        self.assertIn("NORMALIZED_SCORE_ARITHMETIC", failures)

    def test_high_score_a_minus_requires_explicit_a_minus_cap(self):
        payload = self._cert("A-", 90.0)
        _grade, failures = validate_v8_next_certification(payload)
        self.assertIn("A_MINUS_HIGH_SCORE_WITHOUT_CAP", failures)
        payload["active_grade_caps"] = ["INDEPENDENT_AXES_3_MAX_A_MINUS"]
        _grade, failures = validate_v8_next_certification(payload)
        self.assertNotIn("A_MINUS_HIGH_SCORE_WITHOUT_CAP", failures)
        self.assertNotIn("ACTIVE_GRADE_CAP", failures)

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
        self.assertEqual(composition["v8_next_runtime_version"], "V8_NEXT_CERTIFICATION_RUNTIME_V1.0")


if __name__ == "__main__":
    unittest.main()
