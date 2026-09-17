from __future__ import annotations

import unittest

from jsonschema import ValidationError, validate

from stock_agent import v8_main_discovery_coach as coach
from stock_agent import v8_main_discovery_integrity as integrity
from stock_agent.v8_main_discovery_integrity import (
    SCANNER_REQUIRED_DIMENSIONS,
    _contract_complete,
    _merge_candidate,
    _two_low_yield_rounds,
    prepare_v8_main_discovery_integrity,
)
from stock_agent.v8_main_discovery_post_v11 import (
    _system_rounds,
    _two_complete_low_yield_system_rounds,
)
from stock_agent.v8_main_scanner_contract_v12 import scanner_schema_v12
from stock_agent.v8_main_source_fidelity import prepare_v8_4_source_lock
from stock_agent.v8_semantic_core_v22 import provider_exhaustion_v22 as _provider_exhaustion


class FunnelStore:
    def __init__(self, rows):
        self.rows = rows
    def list_funnel(self, run_id):
        return list(self.rows)


class V8MainDiscoveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        prepare_v8_main_discovery_integrity()
        # V8.4 exact-source identity is the final production authority.
        prepare_v8_4_source_lock()

    def _result(self, scanner_id="02", count=75):
        dims = list(SCANNER_REQUIRED_DIMENSIONS[scanner_id])
        result = {
            "scanner_id": scanner_id,
            "scanner_source_sha256": coach.V8_SCANNERS[scanner_id]["sha256"],
            "execution_status": "COMPLETE",
            "screened_count": count,
            "candidates": [],
            "systemic_unknowns": [],
            "search_expansion_questions": [],
            "grade_authority": False,
            "output_contract_version": integrity.SCANNER_OUTPUT_CONTRACT_VERSION,
            "strategy_contract": {
                "scanner_id": scanner_id,
                "dimensions_evaluated": dims,
                "methodology_summary": "source-specific structured equivalent",
            },
            "source_exhaustion": False,
            "source_exhaustion_reason": "NOT_PROVEN",
        }
        schema = scanner_schema_v12()
        if "coverage_ledger" in schema.get("properties", {}):
            result["coverage_ledger"] = []
        return result

    def test_scanner_08_source_hash_is_exact_v8_4_manifest_hash(self):
        value = coach.V8_SCANNERS["08"]["sha256"]
        self.assertEqual(len(value), 64)
        self.assertEqual(value, "1a7c67f527456d0ae4d188250a046c472c41c96a08b46a017309ed8d31f8edad")

    def test_t14_missing_scanner_specific_output_equivalent_is_not_complete(self):
        result = self._result("05")
        result["strategy_contract"]["dimensions_evaluated"] = ["funded_policy"]
        complete, failures = _contract_complete("05", result, 75)
        self.assertFalse(complete)
        self.assertTrue(any(item.startswith("STRATEGY_DIMENSIONS:") for item in failures))

    def test_v12_schema_itself_rejects_generic_scanner_05_contract(self):
        result = self._result("05")
        result["strategy_contract"]["dimensions_evaluated"] = ["generic_signal"]
        with self.assertRaises(ValidationError):
            validate(result, scanner_schema_v12())

    def test_v12_schema_accepts_complete_scanner_specific_contract_without_candidates(self):
        validate(self._result("05"), scanner_schema_v12())

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
        with self.assertRaises(ValidationError):
            validate(result, scanner_schema_v12())

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

    @staticmethod
    def _system_round(scanner_id: str, sequence: int, *, secondary=False):
        sid = "SECONDARY" if secondary else ""
        return {
            "round_id": f"{scanner_id}-R{sequence:03d}",
            "scanner_id": scanner_id,
            "new_unique_tickers": 75,
            "new_signal": 0,
            "new_secondary": 1 if secondary else 0,
            "new_high_research_value": 0,
            "new_independent_evidence": 0,
            "new_deep_dive_now": 0,
            # V2.0 system-round aggregation deduplicates by IDs. Supplying both
            # representations keeps this historical compatibility test immune
            # to production import order.
            "new_signal_security_ids": [],
            "new_secondary_security_ids": [sid] if sid else [],
            "new_high_research_value_security_ids": [],
            "new_independent_evidence_ids": [],
            "new_deep_dive_security_ids": [],
        }

    def test_system_round_unique_breadth_is_not_multiplied_by_13_scanners(self):
        rounds = [self._system_round(scanner_id, 1) for scanner_id in SCANNER_REQUIRED_DIMENSIONS]
        system = _system_rounds(rounds)
        self.assertEqual(len(system), 1)
        self.assertEqual(system[0]["new_unique_tickers"], 75)
        self.assertEqual(system[0]["cumulative_unique_tickers"], 75)
        self.assertTrue(system[0]["scanner_family_complete"])

    def test_low_yield_system_round_requires_all_13_scanners_in_both_rounds(self):
        rounds = [
            self._system_round(scanner_id, sequence)
            for sequence in (1, 2)
            for scanner_id in SCANNER_REQUIRED_DIMENSIONS
        ]
        self.assertTrue(_two_complete_low_yield_system_rounds(rounds))
        rounds.pop()
        self.assertFalse(_two_complete_low_yield_system_rounds(rounds))

    def test_any_secondary_in_last_system_round_blocks_low_yield_stop(self):
        rounds = [
            self._system_round(scanner_id, sequence, secondary=(sequence == 2 and scanner_id == "14"))
            for sequence in (1, 2)
            for scanner_id in SCANNER_REQUIRED_DIMENSIONS
        ]
        self.assertFalse(_two_complete_low_yield_system_rounds(rounds))

    def test_t5_raw_150_is_not_source_exhaustion_when_names_remain_unprobed(self):
        store = FunnelStore([
            {"funnel_stage": "RAW_UNIVERSE", "count": 150},
            {"funnel_stage": "ADV_PROBED", "count": 150},
            {"funnel_stage": "ADV_NOT_EVALUATED", "count": 25},
        ])
        exhausted, details = _provider_exhaustion(store, "RUN", eligible=150)
        self.assertFalse(exhausted)
        self.assertEqual(details["canonical_universe_count"], 150)
        self.assertEqual(details["unresolved_count"], 25)

    def test_1000_name_operational_ceiling_is_not_source_exhaustion(self):
        store = FunnelStore([
            {"funnel_stage": "RAW_UNIVERSE", "count": 3000},
            {"funnel_stage": "ADV_PROBED", "count": 1000},
            {"funnel_stage": "ADV_NOT_EVALUATED", "count": 2000},
        ])
        exhausted, details = _provider_exhaustion(store, "RUN", eligible=600)
        self.assertFalse(exhausted)
        self.assertTrue(details["minimum_operational_probe_met"])
        self.assertTrue(details["search_debt_remains"])
        self.assertEqual(details["unresolved_count"], 2000)

    def test_secondary_signal_survives_weaker_exclude_from_another_round(self):
        secondary = {"security_id": "ABC", "recommended_discovery_action": "DEEP_DIVE_SECONDARY", "unknowns": ["x"], "strategy_evidence": []}
        exclude = {"security_id": "ABC", "recommended_discovery_action": "EXCLUDE", "unknowns": [], "strategy_evidence": []}
        merged = _merge_candidate(secondary, exclude)
        self.assertEqual(merged["recommended_discovery_action"], "DEEP_DIVE_SECONDARY")
        self.assertIn("x", merged["unknowns"])


if __name__ == "__main__":
    unittest.main()
