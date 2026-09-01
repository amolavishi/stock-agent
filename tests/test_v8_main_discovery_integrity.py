from __future__ import annotations

import unittest

from stock_agent import v8_main_discovery_coach as coach
from stock_agent.v8_main_discovery_integrity import (
    SCANNER_OUTPUT_CONTRACT_VERSION,
    SCANNER_REQUIRED_DIMENSIONS,
    _contract_complete,
    _merge_candidate,
    _provider_exhaustion,
    _two_low_yield_rounds,
    prepare_v8_main_discovery_integrity,
)


class FunnelStore:
    def __init__(self, rows):
        self.rows = rows
    def list_funnel(self, run_id):
        return list(self.rows)


class V8MainDiscoveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        prepare_v8_main_discovery_integrity()

    def _result(self, scanner_id="02", count=75):
        dims = list(SCANNER_REQUIRED_DIMENSIONS[scanner_id])
        return {
            "scanner_id": scanner_id,
            "scanner_source_sha256": coach.V8_SCANNERS[scanner_id]["sha256"],
            "execution_status": "COMPLETE",
            "screened_count": count,
            "candidates": [],
            "systemic_unknowns": [],
            "search_expansion_questions": [],
            "grade_authority": False,
            "output_contract_version": SCANNER_OUTPUT_CONTRACT_VERSION,
            "strategy_contract": {
                "scanner_id": scanner_id,
                "dimensions_evaluated": dims,
                "methodology_summary": "source-specific structured equivalent",
            },
            "source_exhaustion": False,
            "source_exhaustion_reason": "NOT_PROVEN",
        }

    def test_scanner_08_source_hash_is_valid_64_char_manifest_hash(self):
        value = coach.V8_SCANNERS["08"]["sha256"]
        self.assertEqual(len(value), 64)
        self.assertEqual(value, "a1c713679274209b99b7c1e165a2cb2b350d25fd002b70038da1b1aedf9408c4")

    def test_t14_missing_scanner_specific_output_equivalent_is_not_complete(self):
        result = self._result("05")
        result["strategy_contract"]["dimensions_evaluated"] = ["funded_policy"]
        complete, failures = _contract_complete("05", result, 75)
        self.assertFalse(complete)
        self.assertTrue(any(item.startswith("STRATEGY_DIMENSIONS:") for item in failures))

    def test_retained_candidate_requires_each_scanner_specific_dimension(self):
        result = self._result("12")
        result["candidates"] = [{
            "security_id": "XYZ",
            "discovery_priority_score": 70,
            "signal_strength": "MODERATE",
            "research_value": "HIGH",
            "recommended_discovery_action": "DEEP_DIVE_SECONDARY",
            "rationale": "test",
            "strengths": ["s"],
            "weaknesses": ["w1", "w2", "w3"],
            "unknowns": ["customer economics"],
            "verification_questions": ["verify second customer economics"],
            "cheap_hard_gate_status": "UNKNOWN",
            "partial_signal": True,
            "failure_class": "DISCOVERY_INSUFFICIENT",
            "strategy_evidence": [{
                "dimension": "customer_concentration",
                "status": "VERIFIED",
                "summary": "known concentration",
                "evidence_ids": ["E1"],
            }],
        }]
        complete, failures = _contract_complete("12", result, 75)
        self.assertFalse(complete)
        self.assertTrue(any(item.startswith("CANDIDATE_DIMENSIONS:XYZ") for item in failures))

    def test_t3_structural_hard_fail_must_route_to_exclude(self):
        result = self._result("10")
        dims = list(SCANNER_REQUIRED_DIMENSIONS["10"])
        result["candidates"] = [{
            "security_id": "TOX",
            "recommended_discovery_action": "DEEP_DIVE_SECONDARY",
            "failure_class": "STRUCTURAL_HARD_FAIL",
            "strategy_evidence": [
                {"dimension": dim, "status": "VERIFIED", "summary": dim, "evidence_ids": [f"E-{dim}"]}
                for dim in dims
            ],
        }]
        complete, failures = _contract_complete("10", result, 75)
        self.assertFalse(complete)
        self.assertIn("STRUCTURAL_FAIL_ROUTING:TOX", failures)

    def test_t4_zero_deep_with_high_secondary_does_not_satisfy_low_yield_stop(self):
        rounds = [
            {"new_signal": 0, "new_secondary": 2, "new_independent_evidence": 0},
            {"new_signal": 0, "new_secondary": 1, "new_independent_evidence": 0},
        ]
        self.assertFalse(_two_low_yield_rounds(rounds))

    def test_two_consecutive_true_low_yield_rounds_are_required(self):
        self.assertFalse(_two_low_yield_rounds([{"new_signal": 0, "new_secondary": 0, "new_independent_evidence": 0}]))
        self.assertTrue(_two_low_yield_rounds([
            {"new_signal": 0, "new_secondary": 0, "new_independent_evidence": 0},
            {"new_signal": 0, "new_secondary": 0, "new_independent_evidence": 0},
        ]))

    def test_t5_raw_150_is_not_source_exhaustion_when_names_remain_unprobed(self):
        store = FunnelStore([
            {"funnel_stage": "RAW_UNIVERSE", "count": 150},
            {"funnel_stage": "ADV_PROBED", "count": 150},
            {"funnel_stage": "ADV_NOT_EVALUATED", "count": 25},
        ])
        exhausted, details = _provider_exhaustion(store, "RUN", eligible=150)
        self.assertFalse(exhausted)
        self.assertEqual(details["raw_unique_ticker_coverage"], 150)
        self.assertEqual(details["adv_not_evaluated"], 25)

    def test_explicit_1000_name_operational_ceiling_is_documented_not_hidden(self):
        store = FunnelStore([
            {"funnel_stage": "RAW_UNIVERSE", "count": 3000},
            {"funnel_stage": "ADV_PROBED", "count": 1000},
            {"funnel_stage": "ADV_NOT_EVALUATED", "count": 2000},
        ])
        exhausted, details = _provider_exhaustion(store, "RUN", eligible=600)
        self.assertTrue(exhausted)
        self.assertTrue(details["explicit_operational_ceiling"])
        self.assertEqual(details["adv_not_evaluated"], 2000)

    def test_secondary_signal_survives_weaker_exclude_from_another_round(self):
        secondary = {"security_id": "ABC", "recommended_discovery_action": "DEEP_DIVE_SECONDARY", "unknowns": ["x"], "strategy_evidence": []}
        exclude = {"security_id": "ABC", "recommended_discovery_action": "EXCLUDE", "unknowns": [], "strategy_evidence": []}
        merged = _merge_candidate(secondary, exclude)
        self.assertEqual(merged["recommended_discovery_action"], "DEEP_DIVE_SECONDARY")
        self.assertIn("x", merged["unknowns"])


if __name__ == "__main__":
    unittest.main()
