from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest

from stock_agent.discovery_recall_failure_guard_v16 import (
    DISCOVERY_RECALL_FAILURE_GUARD_VERSION,
    aggregate_failure_guarded,
    evaluate_failure_guarded,
    search_stop_allowed,
)
from stock_agent.models import canonical_hash
from stock_agent.v8_next_successor import (
    V8_NEXT_POLICY_HASH,
    V8_NEXT_POLICY_VERSION,
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


def cert(ticker: str = "XYZ", grade: str = "A-", score: float = 82.0) -> dict:
    factor = score / 100.0
    components = {key: maximum * factor for key, maximum in SCORE_MAX.items()}
    status = {"A": "A_CERTIFIED", "A-": "A_MINUS_CERTIFIED", "B+": "B_PLUS_ONLY", "B": "B_ONLY", "EXCLUDE": "EXCLUDE"}[grade]
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
        "raw_score": sum(components.values()),
        "normalized_score": score,
        "score_components": components,
        "why_not_one_grade_higher": ["remaining limitation"],
        "critical_unknown_count": 0,
        "critical_unknowns": [],
        "step17_5_complete": True,
        "hard_gate_statuses": {key: "PASS" for key in NEXT_GATES},
        "legacy_hard_gate_statuses": {key: "PASS" for key in LEGACY_GATES},
        "active_grade_caps": [],
        "lineage_failures": [],
        "evidence_ids": ["E1"],
        "python_grade_engine": "V8_NEXT_CERTIFICATION_ENGINE_V1.1",
        "certification_packet": {"packet_hash": canonical_hash({"ticker": ticker, "evidence": ["E1"]})},
    }


class DiscoveryRecallFailureInjectionV16Tests(unittest.TestCase):
    def base_row(self, sid: str = "TEST") -> dict:
        return {
            "security_id": sid,
            "ticker": sid,
            "issuer_name": "Generic Industrial Software Company",
            "market_cap": 1_000_000_000,
            "price": 10.0,
        }

    def base_tech(self) -> dict:
        return {
            "last_price": 10.0,
            "sma_window": 9.5,
            "return_window": 0.05,
            "return_1": 0.03,
            "volatility_window": 0.20,
            "volume_ratio": 1.30,
        }

    def test_01_verified_fundamental_unknown_catalyst_partial_window_is_not_excluded(self):
        row = self.base_row("FI01")
        row.update({
            "fundamental_delta": "VERIFIED",
            "catalyst_economics": "UNKNOWN",
            "one_eight_week_window": "PARTIAL",
        })
        item = evaluate_failure_guarded("02", row, self.base_tech())
        self.assertNotIn(item["disposition"], {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"})
        self.assertFalse(item.get("fatal_fail", False))

    def test_02_unknown_consensus_cannot_be_discovery_hard_fail(self):
        row = self.base_row("FI02")
        row["consensus"] = "UNKNOWN"
        item = evaluate_failure_guarded("11", row, self.base_tech())
        self.assertNotIn(item["disposition"], {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"})

    def test_03_verified_toxic_discounted_vwap_convert_stays_structural_hard_fail(self):
        row = self.base_row("FI03")
        row["toxic_discounted_vwap_convert"] = "VERIFIED"
        item = evaluate_failure_guarded("10", row, self.base_tech())
        self.assertEqual(item["disposition"], "STRUCTURAL_HARD_FAIL")
        self.assertTrue(item["fatal_fail"])
        self.assertFalse(item["research_route_allowed"])

    def test_04_zero_deep_dive_with_high_value_near_miss_cannot_stop_search(self):
        self.assertFalse(search_stop_allowed(
            scanner_execution_complete=True,
            sentinel_complete=True,
            systematic_misclassification=False,
            strategy_eligible_signal_coverage=200,
            open_high_research_value_secondary=0,
            high_research_value_near_miss=1,
            last_rounds_low_signal_secondary_evidence_yield=True,
            source_exhausted=False,
            explicit_operational_ceiling_documented=True,
        ))

    def test_05_shallow_150_without_scanner_execution_is_not_complete(self):
        self.assertFalse(search_stop_allowed(
            scanner_execution_complete=False,
            sentinel_complete=True,
            systematic_misclassification=False,
            strategy_eligible_signal_coverage=150,
            open_high_research_value_secondary=0,
            high_research_value_near_miss=0,
            last_rounds_low_signal_secondary_evidence_yield=True,
            source_exhausted=True,
            explicit_operational_ceiling_documented=True,
        ))

    def test_06_three_independent_high_value_candidates_are_not_capped_at_two(self):
        items = []
        for sid in ("FI06A", "FI06B", "FI06C"):
            item = evaluate_failure_guarded("07", self.base_row(sid), self.base_tech())
            self.assertEqual(item["research_value"], "HIGH")
            self.assertEqual(item["disposition"], "DEEP_DIVE_SECONDARY")
            items.append(item)
        routed, secondary, _near = aggregate_failure_guarded(items)
        self.assertEqual(set(routed), {"FI06A", "FI06B", "FI06C"})
        self.assertEqual(len(secondary), 3)

    def test_07_no_1_8w_event_is_time_horizon_mismatch_and_blocks_a_gate(self):
        row = self.base_row("FI07")
        row["one_eight_week_event"] = False
        item = evaluate_failure_guarded("07", row, self.base_tech())
        self.assertEqual(item["disposition"], "TIME_HORIZON_MISMATCH")
        payload = cert("FI07", "A-", 82.0)
        payload["hard_gate_statuses"]["realization_1_8w"] = "FAIL"
        payload["legacy_hard_gate_statuses"]["wake_up_1_8w"] = "FAIL"
        _grade, failures = validate_v8_next_certification(payload)
        self.assertIn("NEXT_GATE_REALIZATION_1_8W", failures)
        self.assertIn("LEGACY_GATE_WAKE_UP_1_8W", failures)

    def test_08_same_canonical_evidence_different_discovery_path_has_same_step18_grade(self):
        code = r'''
import json
from stock_agent.discovery_recall_firewall_v15 import install_discovery_recall_firewall_v15
from stock_agent import v8_primary
install_discovery_recall_firewall_v15()
a = {"ticker":"FI08","facts":{"revenue":100},"scanner_id":"02","research_value":"HIGH","recommended_discovery_action":"DEEP_DIVE_NOW"}
b = {"ticker":"FI08","facts":{"revenue":100},"scanner_id":"11","research_value":"MEDIUM","recommended_discovery_action":"DEEP_DIVE_SECONDARY"}
print(json.dumps([v8_primary.v8_blind_packet(a), v8_primary.v8_blind_packet(b)], sort_keys=True))
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        first, second = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(first, second)
        p1 = cert("FI08", "A-", 82.0)
        p2 = copy.deepcopy(p1)
        self.assertEqual(validate_v8_next_certification(p1), validate_v8_next_certification(p2))

    def test_09_known_false_positive_names_cannot_regain_a_minus_when_certification_gate_fails(self):
        for ticker in ("ACAD", "RARE", "MIRM", "IONS", "MLTX"):
            payload = cert(ticker, "A-", 82.0)
            payload["hard_gate_statuses"]["critical_claim_robustness"] = "FAIL"
            _grade, failures = validate_v8_next_certification(payload)
            self.assertIn("NEXT_GATE_CRITICAL_CLAIM_ROBUSTNESS", failures, ticker)

    def test_10_structural_hard_fail_is_archived_near_miss_but_never_research_routed(self):
        row = self.base_row("FI10")
        row["financing_structure"] = "toxic discounted VWAP convertible note"
        item = evaluate_failure_guarded("10", row, self.base_tech())
        routed, secondary, near = aggregate_failure_guarded([item])
        self.assertEqual(routed, {})
        self.assertEqual(secondary, [])
        self.assertEqual(len(near), 1)
        self.assertTrue(near[0]["fatal_fail"])
        self.assertFalse(near[0]["research_route_allowed"])
        self.assertEqual(near[0]["queue_status"], "ARCHIVED_FATAL")

    def test_production_composition_reports_failure_guard(self):
        code = "import json; from stock_agent.bootstrap import production_composition; print(json.dumps(production_composition(), sort_keys=True))"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        value = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(value["discovery_recall_failure_guard_version"], DISCOVERY_RECALL_FAILURE_GUARD_VERSION)


if __name__ == "__main__":
    unittest.main()
