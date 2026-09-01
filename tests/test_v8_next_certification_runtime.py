from __future__ import annotations

import json
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from stock_agent.models import EffectiveRuleSet, GateDecision, RunMode
from stock_agent.paths import canonical_prompt_library_root
from stock_agent.providers import FakeProvider
from stock_agent.runtime import StockAgentConfig
from stock_agent.v8_next_successor import (
    V8_NEXT_POLICY_HASH,
    V8_NEXT_POLICY_VERSION,
    validate_v8_next_certification,
)
from stock_agent import v8_next_certification as cert
from stock_agent.v8_next_certification_v11 import install_v8_next_certification_v11


install_v8_next_certification_v11()


def robust_assumption_audit() -> dict:
    return {
        "status": "COMPLETE",
        "assumptions": [
            {"assumption_id": item, "status": "ROBUST", "rationale": "source backed", "evidence_ids": ["E1"], "unknowns": []}
            for item in cert.ASSUMPTIONS
        ],
        "grade_authority": False,
    }


def robust_atomic_audit() -> dict:
    return {
        "status": "COMPLETE",
        "atomic_claims": [
            {
                "claim_id": f"C{i}", "statement": f"claim {i}", "verification_status": "VERIFIED",
                "economic_event_id": f"EVENT-{i}", "independent_evidence_group": f"GROUP-{i}",
                "evidence_ids": ["E1"],
            }
            for i in range(1, 5)
        ],
        "evidence_independence": "PASS",
        "duplicate_economic_event_ids": [],
        "critical_unknowns": [],
        "value_realization_bridge_1_8w": {"status": "ROBUST", "summary": "1-8w bridge is explicit", "evidence_ids": ["E1"]},
        "probability_provenance": "DATA_BACKED",
        "grade_authority": False,
    }


def robust_fd_bridge() -> dict:
    return {
        "status": "COMPLETE",
        "current_shares": 100.0,
        "instrument_potential_shares": 20.0,
        "fully_diluted_shares": 120.0,
        "likely_near_term_dilution_shares": 10.0,
        "probable_financing_shares": 5.0,
        "projected_near_term_fd_shares": 125.0,
        "cash_runway_months": 24.0,
        "financing_need": "NONE",
        "toxic_red_flag": False,
        "source_instruments": [],
        "per_share_impact_summary": "fully diluted economics reproduced",
        "unknowns": [],
        "evidence_ids": ["E1"],
        "grade_authority": False,
        "arithmetic_authority": "PYTHON_V8_NEXT_FD_BRIDGE_V1",
        "projected_dilution_pct": 25.0,
        "validation_failures": [],
    }


def robust_step18_draft() -> dict:
    legacy = {key: {"status": "PASS", "rationale": "verified", "evidence_ids": ["E1"]} for key in cert.LEGACY_HARD_GATES}
    nxt = {key: {"status": "PASS", "rationale": "verified", "evidence_ids": ["E1"]} for key in cert.NEXT_HARD_GATES}
    return {
        "status": "COMPLETE",
        "score_components": {
            "catalyst_strength": 23.0,
            "time_immediacy": 14.0,
            "numeric_evidence": 14.0,
            "supply_demand": 9.0,
            "price_stage_fit": 14.0,
            "strategic_fit": 14.0,
            "expected_value": 9.0,
        },
        "legacy_hard_gates": legacy,
        "next_hard_gates": nxt,
        "grade_caps": [],
        "independent_improvement_axes": [
            {"axis_id": f"A{i}", "summary": f"axis {i}", "economic_event_id": f"EVENT-{i}", "evidence_ids": ["E1"]}
            for i in range(1, 5)
        ],
        "valuation_method_count": 2,
        "scenarios": [
            {"scenario": "BEAR", "probability_pct": 20, "price_or_value": 8.0, "assumption": "bear", "evidence_ids": ["E1"]},
            {"scenario": "BASE", "probability_pct": 50, "price_or_value": 13.0, "assumption": "base", "evidence_ids": ["E1"]},
            {"scenario": "BULL", "probability_pct": 30, "price_or_value": 18.0, "assumption": "bull", "evidence_ids": ["E1"]},
        ],
        "probability_provenance": {"class": "DATA_BACKED", "summary": "historical and evidence backed", "evidence_ids": ["E1"]},
        "target_reverse_engineered": False,
        "toxic_red_flag": False,
        "b_plus_devil_advocate": ["Could still be market-known."],
        "why_not_one_grade_higher": ["A is already the highest grade."],
        "score_reset_from_zero": True,
        "discovery_score_used": False,
        "pre_a_metadata_used": False,
        "candidate_shortage_influenced_grade": False,
        "grade_authority": False,
    }


class V8NextCertificationRuntimeTests(unittest.TestCase):
    def test_python_grade_engine_can_issue_valid_a_without_model_grade_authority(self):
        packet = {"ticker": "XYZ", "packet_hash": "packet-hash"}
        receipt = cert.finalize_certification(
            robust_step18_draft(), robust_assumption_audit(), robust_atomic_audit(), robust_fd_bridge(),
            packet, 10.0, ["E1"], V8_NEXT_POLICY_VERSION, V8_NEXT_POLICY_HASH,
        )
        grade, failures = validate_v8_next_certification(receipt)
        self.assertEqual(receipt["research_grade"], "A")
        self.assertEqual(receipt["grade_authority"], "V8_NEXT_STEP18_CANONICAL")
        self.assertEqual(receipt["python_grade_engine"], "V8_NEXT_CERTIFICATION_ENGINE_V1.1")
        self.assertEqual(grade, "A")
        self.assertEqual(failures, [])

    def test_fragile_critical_assumption_caps_high_score_at_b_plus(self):
        audit = robust_assumption_audit()
        audit["assumptions"][0]["status"] = "FRAGILE"
        receipt = cert.finalize_certification(
            robust_step18_draft(), audit, robust_atomic_audit(), robust_fd_bridge(),
            {"packet_hash": "x"}, 10.0, ["E1"], V8_NEXT_POLICY_VERSION, V8_NEXT_POLICY_HASH,
        )
        self.assertEqual(receipt["research_grade"], "B+")
        self.assertIn("CRITICAL_ASSUMPTION_NOT_ROBUST_MAX_B_PLUS", receipt["active_grade_caps"])

    def test_score_below_65_is_exclude_not_b(self):
        draft = robust_step18_draft()
        draft["score_components"] = {key: 0.0 for key in cert.SCORE_MAX}
        receipt = cert.finalize_certification(
            draft, robust_assumption_audit(), robust_atomic_audit(), robust_fd_bridge(),
            {"packet_hash": "x"}, 10.0, ["E1"], V8_NEXT_POLICY_VERSION, V8_NEXT_POLICY_HASH,
        )
        self.assertEqual(receipt["research_grade"], "EXCLUDE")
        self.assertIn("NORMALIZED_SCORE_LT65_EXCLUDE", receipt["active_grade_caps"])

    def test_non_coarse_probabilities_cannot_remain_a(self):
        draft = robust_step18_draft()
        draft["scenarios"][0]["probability_pct"] = 33
        draft["scenarios"][1]["probability_pct"] = 34
        draft["scenarios"][2]["probability_pct"] = 33
        receipt = cert.finalize_certification(
            draft, robust_assumption_audit(), robust_atomic_audit(), robust_fd_bridge(),
            {"packet_hash": "x"}, 10.0, ["E1"], V8_NEXT_POLICY_VERSION, V8_NEXT_POLICY_HASH,
        )
        self.assertEqual(receipt["research_grade"], "B+")
        self.assertIn("PW_EV_UNREPRODUCIBLE_MAX_B_PLUS", receipt["active_grade_caps"])

    def test_actual_runtime_override_persists_step15_through_step20(self):
        from stock_agent.production import ProductionStockAgent, production_composition

        config = StockAgentConfig(canonical_prompt_library_root(), Path(":memory:"), strict_inputs=True)
        agent = ProductionStockAgent(config, provider=FakeProvider())
        run = agent.store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "ctx", 0)
        candidate = {
            "security_id": "XYZ", "price": 10.0, "evidence_ids": ["E1"],
            "research_result": {"material_claims": [{"summary": "verified claim", "evidence_ids": ["E1"]}]},
            "failure_paths": [{"category": "FUNDAMENTAL"}, {"category": "CAPITAL_STRUCTURE"}, {"category": "PRICING_EXPECTATION"}],
        }
        artifact = SimpleNamespace(payload={"content": "research", "evidence_items": []})

        runtime_cls = agent.__class__
        runtime_layer = next(cls for cls in runtime_cls.__mro__ if cls.__name__ == "V8NextRuntimeProductionStockAgent")
        parent = runtime_layer.__mro__[1]
        original_parent = parent._persist_hunt_reverse_valuation
        original_model_stage = agent._run_next_model_stage

        def fake_parent(self, *args, **kwargs):
            candidate["reverse_valuation"] = {"current_price": 10.0}
            return True, SimpleNamespace(decision=GateDecision.PASS)

        def fake_model_stage(self, run, stage, prompt_id, packet, subject_id, evidence_ids, default_payload):
            if prompt_id == cert.PROMPT_STEP15:
                return {
                    "status": "COMPLETE", "current_shares": 100.0, "instrument_potential_shares": 20.0,
                    "fully_diluted_shares": 120.0, "likely_near_term_dilution_shares": 10.0,
                    "probable_financing_shares": 5.0, "projected_near_term_fd_shares": 125.0,
                    "cash_runway_months": 24.0, "financing_need": "NONE", "toxic_red_flag": False,
                    "source_instruments": [], "per_share_impact_summary": "reproduced", "unknowns": [],
                    "evidence_ids": ["E1"], "grade_authority": False,
                }
            if prompt_id == cert.PROMPT_STEP16:
                return robust_atomic_audit()
            if prompt_id == cert.PROMPT_STEP17_5:
                return robust_assumption_audit()
            if prompt_id == cert.PROMPT_STEP18:
                return robust_step18_draft()
            raise AssertionError(prompt_id)

        parent._persist_hunt_reverse_valuation = fake_parent
        agent._run_next_model_stage = types.MethodType(fake_model_stage, agent)
        try:
            known, gate = agent._persist_hunt_reverse_valuation(run, candidate, ["E1"], artifact, "E1", artifact, candidate)
        finally:
            parent._persist_hunt_reverse_valuation = original_parent
            agent._run_next_model_stage = original_model_stage

        self.assertTrue(known)
        self.assertEqual(gate.decision, GateDecision.PASS)
        for stage in (
            cert.STEP15_STAGE, cert.STEP16_STAGE, cert.STEP17_STAGE,
            cert.STEP17_5_STAGE, cert.STEP18_STAGE, cert.STEP20_STAGE,
        ):
            self.assertIsNotNone(agent.store.get_stage_result(run.run_id, stage, "XYZ"), stage)
        certification = json.loads(agent.store.get_stage_result(run.run_id, cert.STEP18_STAGE, "XYZ")["result_json"])
        validator = json.loads(agent.store.get_stage_result(run.run_id, cert.STEP20_STAGE, "XYZ")["result_json"])
        self.assertEqual(certification["research_grade"], "A")
        self.assertEqual(validator["route"], "PASS")
        composition = production_composition()
        self.assertEqual(composition["v8_next_runtime_version"], "V8_NEXT_CERTIFICATION_RUNTIME_V1.0")
        agent.close()


if __name__ == "__main__":
    unittest.main()
