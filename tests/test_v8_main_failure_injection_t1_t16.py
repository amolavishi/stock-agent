from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from stock_agent import v8_primary
from stock_agent.models import canonical_hash
from stock_agent.v8_main_discovery_integrity import (
    SCANNER_REQUIRED_DIMENSIONS,
    _contract_complete,
    _expire_secondary,
    _merge_candidate,
    _provider_exhaustion,
    _two_low_yield_rounds,
    prepare_v8_main_discovery_integrity,
)
from stock_agent.v8_main_discovery_post_v11 import _system_rounds, _two_complete_low_yield_system_rounds
from stock_agent.v8_main_discovery_coach import V8_SCANNERS
from stock_agent.v8_next_successor import (
    V8_NEXT_POLICY_HASH,
    V8_NEXT_POLICY_VERSION,
    validate_v8_next_certification,
)


class FunnelStore:
    def __init__(self, rows): self.rows = rows
    def list_funnel(self, run_id): return list(self.rows)


class ExpiryStore:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE discovery_secondary_queue (security_id TEXT PRIMARY KEY, expiry TEXT, status TEXT, updated_at TEXT)"
        )


class V8MainFailureInjectionT1T16(unittest.TestCase):
    def setUp(self):
        prepare_v8_main_discovery_integrity()

    @staticmethod
    def candidate(sid="ABC", action="DEEP_DIVE_SECONDARY", research_value="HIGH", failure_class="DISCOVERY_INSUFFICIENT", unknowns=None):
        return {
            "security_id": sid,
            "recommended_discovery_action": action,
            "research_value": research_value,
            "failure_class": failure_class,
            "unknowns": list(unknowns or []),
            "strategy_evidence": [],
        }

    @staticmethod
    def scanner_result(scanner_id: str, candidate=None, count=75):
        return {
            "scanner_id": scanner_id,
            "scanner_source_sha256": V8_SCANNERS[scanner_id]["sha256"],
            "execution_status": "COMPLETE",
            "screened_count": count,
            "candidates": [] if candidate is None else [candidate],
            "systemic_unknowns": [],
            "search_expansion_questions": [],
            "grade_authority": False,
            "output_contract_version": "V8_MAIN_SCANNER_OUTPUT_V1.1",
            "strategy_contract": {
                "scanner_id": scanner_id,
                "dimensions_evaluated": list(SCANNER_REQUIRED_DIMENSIONS[scanner_id]),
                "methodology_summary": "fixture",
            },
            "source_exhaustion": False,
            "source_exhaustion_reason": "NOT_PROVEN",
        }

    def test_t1_partial_catalyst_with_real_delta_may_route_secondary_but_has_no_grade(self):
        item = self.candidate(unknowns=["exact catalyst timing"])
        packet = {"candidate": item, "research_grade": "A-"}
        blinded = v8_primary.v8_blind_packet(packet)
        self.assertEqual(item["recommended_discovery_action"], "DEEP_DIVE_SECONDARY")
        self.assertNotIn("research_grade", blinded)

    def test_t2_consensus_unknown_is_not_structural_or_thesis_fail(self):
        item = self.candidate(unknowns=["consensus_estimate"])
        self.assertEqual(item["failure_class"], "DISCOVERY_INSUFFICIENT")
        self.assertNotIn(item["failure_class"], {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"})

    def test_t3_toxic_structure_cannot_route_to_near_miss_secondary(self):
        sid = "TOX"
        item = self.candidate(sid=sid, failure_class="STRUCTURAL_HARD_FAIL")
        item["strategy_evidence"] = [
            {"dimension": dim, "status": "VERIFIED", "summary": dim, "evidence_ids": [f"E-{dim}"]}
            for dim in SCANNER_REQUIRED_DIMENSIONS["10"]
        ]
        result = self.scanner_result("10", item)
        complete, failures = _contract_complete("10", result, 75)
        self.assertFalse(complete)
        self.assertIn(f"STRUCTURAL_FAIL_ROUTING:{sid}", failures)

    def test_t4_zero_deep_with_three_high_secondary_does_not_prove_stop(self):
        rounds = [
            {"new_signal": 0, "new_secondary": 2, "new_independent_evidence": 0},
            {"new_signal": 0, "new_secondary": 1, "new_independent_evidence": 0},
        ]
        self.assertFalse(_two_low_yield_rounds(rounds))

    def test_t5_raw_150_without_complete_signal_scan_cannot_be_treated_as_exhausted(self):
        exhausted, details = _provider_exhaustion(FunnelStore([
            {"funnel_stage": "RAW_UNIVERSE", "count": 150},
            {"funnel_stage": "ADV_PROBED", "count": 150},
            {"funnel_stage": "ADV_NOT_EVALUATED", "count": 40},
        ]), "RUN", 150)
        self.assertFalse(exhausted)
        self.assertEqual(details["raw_unique_ticker_coverage"], 150)

    def test_t6_scanner02_has_no_two-name_hard_cap(self):
        candidates = [self.candidate(sid=f"S{i}") for i in range(3)]
        merged = {}
        for item in candidates:
            merged[item["security_id"]] = _merge_candidate(merged.get(item["security_id"]), item)
        self.assertEqual(len(merged), 3)

    def test_t7_no_1_8w_event_is_time_horizon_mismatch_not_grade(self):
        item = self.candidate(action="WATCH_STAGE0", failure_class="TIME_HORIZON_MISMATCH")
        self.assertEqual(item["recommended_discovery_action"], "WATCH_STAGE0")
        self.assertEqual(item["failure_class"], "TIME_HORIZON_MISMATCH")
        v8_primary.assert_pre18_grade_firewall({"candidate": item})

    def test_t8_same_canonical_evidence_scrubs_different_discovery_history_to_same_packet(self):
        base = {"security_id": "ABC", "evidence_ids": ["E1", "E2"], "fact": {"kpi": 42}}
        a = {**base, "discovery_score": 99, "discovery_rank": 1, "target_verified_a_minus_or_better": 5}
        b = {**base, "discovery_score": 3, "discovery_rank": 200, "remaining_a_needed": 4}
        self.assertEqual(v8_primary.v8_blind_packet(a), v8_primary.v8_blind_packet(b))

    def test_t9_historical_false_positive_tickers_cannot_bypass_invalid_certification(self):
        for ticker in ("ACAD", "RARE", "MIRM", "IONS", "MLTX"):
            payload = {
                "source_sha256": V8_NEXT_POLICY_HASH,
                "policy_version": V8_NEXT_POLICY_VERSION,
                "grade_authority": "V8_NEXT_STEP18_CANONICAL",
                "certification_status": "NOT_CERTIFIABLE",
                "discovery_score_used": False,
                "pre_a_metadata_used": False,
                "score_reset_from_zero": True,
                "candidate_shortage_influenced_grade": False,
                "research_grade": "A",
                "raw_score": 94.5,
                "normalized_score": 90.0,
                "score_components": {
                    "catalyst_strength": 22.5, "time_immediacy": 13.5, "numeric_evidence": 13.5,
                    "supply_demand": 9.0, "price_stage_fit": 13.5, "strategic_fit": 13.5, "expected_value": 9.0,
                },
                "why_not_one_grade_higher": ["none"],
                "critical_unknown_count": 1,
                "critical_unknowns": ["unresolved"],
                "step17_5_complete": False,
                "hard_gate_statuses": {},
                "legacy_hard_gate_statuses": {},
                "active_grade_caps": [],
                "lineage_failures": [],
                "evidence_ids": ["E1"],
                "python_grade_engine": "V8_NEXT_CERTIFICATION_ENGINE_V1.1",
                "certification_packet": {"ticker": ticker, "packet_hash": canonical_hash({"ticker": ticker})},
            }
            _grade, failures = validate_v8_next_certification(payload)
            self.assertTrue(failures, ticker)
            self.assertIn("CERTIFICATION_NOT_CERTIFIABLE", failures)

    def test_t10_structural_fail_is_not_merged_down_to_secondary(self):
        structural = self.candidate("TOX", action="EXCLUDE", failure_class="STRUCTURAL_HARD_FAIL")
        secondary = self.candidate("TOX", action="DEEP_DIVE_SECONDARY", failure_class="DISCOVERY_INSUFFICIENT")
        merged = _merge_candidate(structural, secondary)
        # Routing priority alone may retain Secondary, but the authoritative
        # contract validator must still reject that routing because one scanner
        # found a structural fatality. This guards against hiding the fatality.
        result = self.scanner_result("10", merged)
        merged["strategy_evidence"] = [
            {"dimension": dim, "status": "VERIFIED", "summary": dim, "evidence_ids": [f"E-{dim}"]}
            for dim in SCANNER_REQUIRED_DIMENSIONS["10"]
        ]
        merged["failure_class"] = "STRUCTURAL_HARD_FAIL"
        result["candidates"] = [merged]
        complete, failures = _contract_complete("10", result, 75)
        self.assertFalse(complete)
        self.assertTrue(any(value.startswith("STRUCTURAL_FAIL_ROUTING") for value in failures))

    def test_t11_four_unknowns_with_decisive_resolvable_question_can_remain_secondary(self):
        item = self.candidate(unknowns=["u1", "u2", "u3", "decisive_resolvable"])
        self.assertEqual(item["recommended_discovery_action"], "DEEP_DIVE_SECONDARY")
        self.assertEqual(len(item["unknowns"]), 4)

    def test_t12_duplicate_scanner_evaluations_do_not_multiply_unique_breadth(self):
        rounds = [{
            "round_id": f"{sid}-R001", "scanner_id": sid, "new_unique_tickers": 75,
            "new_signal": 0, "new_secondary": 0, "new_high_research_value": 0,
            "new_independent_evidence": 0, "new_deep_dive_now": 0,
        } for sid in SCANNER_REQUIRED_DIMENSIONS]
        system = _system_rounds(rounds)
        self.assertEqual(system[0]["new_unique_tickers"], 75)

    def test_t13_context_only_names_cannot_satisfy_strategy_eligible_coverage(self):
        exhausted, details = _provider_exhaustion(FunnelStore([
            {"funnel_stage": "RAW_UNIVERSE", "count": 100},
            {"funnel_stage": "ADV_PROBED", "count": 50},
            {"funnel_stage": "ADV_NOT_EVALUATED", "count": 50},
        ]), "RUN", 50)
        self.assertFalse(exhausted)
        self.assertEqual(details["strategy_eligible_unique_coverage"], 50)
        self.assertNotEqual(details["raw_unique_ticker_coverage"], details["strategy_eligible_unique_coverage"])

    def test_t14_scanner_marked_complete_without_specific_dimensions_is_invalid(self):
        result = self.scanner_result("14")
        result["strategy_contract"]["dimensions_evaluated"] = ["generic_ai"]
        complete, failures = _contract_complete("14", result, 75)
        self.assertFalse(complete)
        self.assertTrue(any(value.startswith("STRATEGY_DIMENSIONS") for value in failures))

    def test_t15_secondary_expiry_moves_to_watch_not_pre_a(self):
        store = ExpiryStore()
        expired = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        store.connection.execute(
            "INSERT INTO discovery_secondary_queue VALUES('ABC',?,'OPEN','old')", (expired,)
        )
        count = _expire_secondary(store)
        row = store.connection.execute("SELECT status FROM discovery_secondary_queue WHERE security_id='ABC'").fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(row["status"], "EXPIRED_WATCH")
        self.assertNotIn("PRE_A", row["status"])

    def test_t16_new_discovery_metadata_is_scrubbed_before_step18(self):
        value = {
            "security_id": "ABC",
            "scanner_id": "14",
            "research_value": "HIGH",
            "secondary_queue": {"status": "OPEN"},
            "near_miss_status": "WATCH",
            "remaining_a_needed": 3,
            "fact": "keep",
        }
        blind = v8_primary.v8_blind_packet(value)
        self.assertEqual(blind, {"security_id": "ABC", "fact": "keep"})

    def test_two_low_yield_rounds_requires_complete_13_scanner_family(self):
        rounds = []
        for seq in (1, 2):
            for sid in SCANNER_REQUIRED_DIMENSIONS:
                rounds.append({
                    "round_id": f"{sid}-R{seq:03d}", "scanner_id": sid,
                    "new_unique_tickers": 75, "new_signal": 0, "new_secondary": 0,
                    "new_high_research_value": 0, "new_independent_evidence": 0,
                    "new_deep_dive_now": 0,
                })
        self.assertTrue(_two_complete_low_yield_system_rounds(rounds))


if __name__ == "__main__":
    unittest.main()
