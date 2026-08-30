from __future__ import annotations

import json
import unittest

from stock_agent.models import EffectiveRuleSet, GateDecision, RunMode
from stock_agent.valuation import (
    ExpectationGapGate,
    build_reverse_valuation_receipt,
    extract_valuation_inputs,
)
from tests.test_adversarial_provider_integration import fixture, strict_agent


BASE_INPUTS = {
    "valuation_basis": "EV_REVENUE",
    "metric_name": "FORWARD_REVENUE",
    "diluted_shares": 50_000_000,
    "net_cash": 50_000_000,
    "forward_metric_value": 100_000_000,
    "benchmark_multiple": 8.0,
    "benchmark_description": "peer/consensus forward revenue benchmark",
    "source_url": "https://issuer.example/valuation",
    "source_observed_at": "2026-08-20T00:00:00Z",
}


def receipt(inputs=None):
    return build_reverse_valuation_receipt(
        security_id="SEC1",
        current_price=11.0,
        valuation_inputs=dict(inputs or BASE_INPUTS),
        market_artifact_id="AMKT",
        research_artifact_id="ARES",
        market_evidence_id="EMKT",
        research_evidence_id="ERES",
        source_result_ids=["R-DEEP", "R-SEC", "R-AUDIT"],
    )


class HuntReverseValuationArithmeticTests(unittest.TestCase):
    def test_reverse_valuation_arithmetic_is_python_owned(self):
        result = receipt()
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.current_market_cap, 550_000_000.0)
        self.assertAlmostEqual(result.current_enterprise_value, 500_000_000.0)
        self.assertAlmostEqual(result.current_multiple, 5.0)
        self.assertAlmostEqual(result.benchmark_implied_price, 17.0)
        self.assertAlmostEqual(result.benchmark_implied_upside_pct, 6.0 / 11.0)
        self.assertAlmostEqual(result.target_30_price, 14.3)
        self.assertAlmostEqual(result.target_30_required_multiple, 6.65)
        self.assertAlmostEqual(result.target_30_required_metric, 83_125_000.0)
        self.assertAlmostEqual(result.target_30_required_metric_growth_pct, -0.16875)
        self.assertAlmostEqual(result.target_60_price, 17.6)
        self.assertAlmostEqual(result.target_60_required_multiple, 8.3)
        self.assertAlmostEqual(result.target_60_required_metric, 103_750_000.0)
        self.assertAlmostEqual(result.target_60_required_metric_growth_pct, 0.0375)
        self.assertEqual(len(result.calculation_hash), 64)

    def test_missing_numeric_evidence_does_not_create_receipt(self):
        inputs = dict(BASE_INPUTS)
        inputs.pop("net_cash")
        self.assertIsNone(receipt(inputs))

    def test_conflicting_net_cash_and_net_debt_fails_closed(self):
        inputs = dict(BASE_INPUTS)
        inputs["net_debt"] = 10_000_000
        self.assertIsNone(receipt(inputs))

    def test_metric_and_valuation_basis_must_match(self):
        inputs = dict(BASE_INPUTS)
        inputs["valuation_basis"] = "EV_EBITDA"
        self.assertIsNone(receipt(inputs))

    def test_expectation_gap_gate_uses_rule_owned_upside_threshold(self):
        rules = EffectiveRuleSet()
        result = receipt()
        gate = ExpectationGapGate().evaluate(result, rules)
        self.assertEqual(gate.decision, GateDecision.PASS)
        self.assertEqual(gate.strength, "QUALIFYING")
        self.assertAlmostEqual(gate.required_min_upside_pct, 0.30)

        weak = dict(BASE_INPUTS)
        weak["benchmark_multiple"] = 6.0
        weak_result = receipt(weak)
        weak_gate = ExpectationGapGate().evaluate(weak_result, rules)
        self.assertEqual(weak_gate.decision, GateDecision.REJECT)
        self.assertIn("EXPECTATION_GAP_BELOW_STRATEGY_THRESHOLD", weak_gate.reason_codes)

    def test_structured_research_payload_is_extracted_without_guessing(self):
        payload = {
            "source": {
                "source_url": "https://issuer.example/valuation",
                "observed_at": "2026-08-20T00:00:00Z",
                "valuation_inputs": {key: value for key, value in BASE_INPUTS.items() if key not in {"source_url", "source_observed_at"}},
            }
        }
        extracted = extract_valuation_inputs(payload)
        self.assertEqual(extracted["benchmark_multiple"], 8.0)
        self.assertEqual(extracted["source_url"], "https://issuer.example/valuation")
        self.assertEqual(extracted["source_observed_at"], "2026-08-20T00:00:00Z")


class StrictHuntReverseValuationIntegrationTests(unittest.TestCase):
    @staticmethod
    def _funnel(agent, run_id):
        return {row["funnel_stage"]: row["count"] for row in agent.store.list_funnel(run_id)}

    def test_valid_evidence_creates_reverse_valuation_before_pool(self):
        data = fixture()
        agent = strict_agent(data)
        try:
            outcome = agent.run(RunMode.HUNT_ONLY, {})
            self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
            self.assertEqual(agent.store.work_item_counts(outcome.run_id).get("SUCCEEDED"), 11)
            gate = agent.store.get_stage_result(outcome.run_id, "EXPECTATION_GAP_GATE", "SEC1")
            self.assertIsNotNone(gate)
            self.assertIn('"decision": "PASS"', gate["result_json"])
            payload = json.loads(agent.store.connection.execute(
                "SELECT payload_json FROM raw_artifacts WHERE artifact_type='REVERSE_VALUATION' AND subject_id='SEC1'"
            ).fetchone()[0])
            self.assertAlmostEqual(payload["current_multiple"], 5.0)
            self.assertAlmostEqual(payload["benchmark_implied_price"], 17.0)
            self.assertEqual(self._funnel(agent, outcome.run_id)["EXPECTATION_GAP_KNOWN"], 1)
            qualified, missing = agent.store.qualified_candidate_status(outcome.run_id, "SEC1")
            self.assertTrue(qualified, missing)
        finally:
            agent.close()

    def test_missing_valuation_inputs_fails_closed_before_pool(self):
        data = fixture()
        data["provider_recordings"]["research"]["SEC1"].pop("valuation_inputs", None)
        agent = strict_agent(data)
        try:
            outcome = agent.run(RunMode.HUNT_ONLY, {})
            self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")
            gate = agent.store.get_stage_result(outcome.run_id, "EXPECTATION_GAP_GATE", "SEC1")
            self.assertIsNotNone(gate)
            self.assertIn('"decision": "INSUFFICIENT_EVIDENCE"', gate["result_json"])
            self.assertEqual(self._funnel(agent, outcome.run_id)["EXPECTATION_GAP_UNKNOWN"], 1)
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) FROM raw_artifacts WHERE artifact_type='REVERSE_VALUATION'").fetchone()[0], 0)
        finally:
            agent.close()

    def test_below_30_percent_gap_is_rejected_even_if_caller_claims_bull_case(self):
        data = fixture()
        data["provider_recordings"]["research"]["SEC1"]["valuation_inputs"]["benchmark_multiple"] = 6.0
        data["provider_recordings"]["candidates"][0]["economic_assessment"] = {
            "current_price": 11.0,
            "bull_value": 1000.0,
            "base_value": 900.0,
            "bear_value": 800.0,
            "bull_probability": 0.9,
            "base_probability": 0.05,
            "bear_probability": 0.05,
            "opportunity_cost_score": 0.0,
        }
        agent = strict_agent(data)
        try:
            outcome = agent.run(RunMode.HUNT_ONLY, {})
            self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")
            gate = agent.store.get_stage_result(outcome.run_id, "EXPECTATION_GAP_GATE", "SEC1")
            gate_payload = json.loads(gate["result_json"])
            self.assertEqual(gate_payload["decision"], "REJECT")
            self.assertLess(gate_payload["implied_upside_pct"], 0.30)
            self.assertEqual(self._funnel(agent, outcome.run_id)["EXPECTATION_GAP_REJECT"], 1)
        finally:
            agent.close()


if __name__ == "__main__":
    unittest.main()

