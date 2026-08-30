from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from stock_agent.catalyst import CatalystGate, extract_catalyst_packet
from stock_agent.models import EffectiveRuleSet, GateDecision, RunMode
from tests.test_adversarial_provider_integration import fixture, strict_agent


def catalyst(*, now: datetime, **patch):
    item = {
        "catalyst_id": "C1",
        "event_type": "EARNINGS",
        "event_at": (now + timedelta(days=20)).isoformat(),
        "verification_status": "CONFIRMED",
        "binding_status": "NOT_APPLICABLE",
        "economic_transmission": {"metric": "revenue_growth_pct", "direction": "UP", "magnitude": 20.0},
        "confirmation_metric": "reported revenue and forward guidance",
        "source_url": "https://issuer.example/ir",
        "source_observed_at": now.isoformat(),
        "artifact_id": "A1",
        "evidence_id": "E1",
    }
    item.update(patch)
    return item


class CatalystGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.rules = EffectiveRuleSet()
        self.gate = CatalystGate()

    def test_verified_quantified_near_term_catalyst_passes(self):
        receipt = self.gate.evaluate({"catalysts": [catalyst(now=self.now)]}, self.rules, now=self.now)
        self.assertEqual(receipt.decision, GateDecision.PASS)
        self.assertTrue(receipt.core_input_complete)
        self.assertEqual(receipt.valid_catalyst_ids, ("C1",))

    def test_unverified_catalyst_fails_closed(self):
        receipt = self.gate.evaluate({"catalysts": [catalyst(now=self.now, verification_status="RUMOR")]}, self.rules, now=self.now)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)
        self.assertIn("CATALYST_UNVERIFIED", receipt.evaluations[0]["reason_codes"])

    def test_non_binding_mou_fails_closed(self):
        receipt = self.gate.evaluate({"catalysts": [catalyst(now=self.now, event_type="MOU", binding_status="NON_BINDING")]}, self.rules, now=self.now)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)
        self.assertIn("CATALYST_NON_BINDING_EVENT", receipt.evaluations[0]["reason_codes"])

    def test_theme_without_quantified_transmission_fails_closed(self):
        item = catalyst(now=self.now, economic_transmission={"metric": "AI demand", "direction": "UP"})
        receipt = self.gate.evaluate({"catalysts": [item]}, self.rules, now=self.now)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)
        self.assertIn("CATALYST_ECONOMIC_TRANSMISSION_UNQUANTIFIED", receipt.evaluations[0]["reason_codes"])

    def test_event_outside_strategy_window_fails_closed(self):
        item = catalyst(now=self.now, event_at=(self.now + timedelta(days=self.rules.strategy_max_days + 10)).isoformat())
        receipt = self.gate.evaluate({"catalysts": [item]}, self.rules, now=self.now)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)
        self.assertIn("CATALYST_OUTSIDE_STRATEGY_WINDOW", receipt.evaluations[0]["reason_codes"])

    def test_missing_provenance_fails_closed(self):
        item = catalyst(now=self.now, source_url=None, artifact_id=None)
        receipt = self.gate.evaluate({"catalysts": [item]}, self.rules, now=self.now)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)
        self.assertIn("CATALYST_SOURCE_URL_MISSING", receipt.evaluations[0]["reason_codes"])
        self.assertIn("CATALYST_PROVENANCE_RECEIPT_MISSING", receipt.evaluations[0]["reason_codes"])

    def test_extractor_inherits_artifact_provenance(self):
        payload = {
            "source": {
                "source_url": "https://issuer.example/ir",
                "observed_at": self.now.isoformat(),
                "catalysts": [{
                    "id": "C2",
                    "type": "INVESTOR_DAY",
                    "event_date": (self.now + timedelta(days=15)).isoformat(),
                    "verification": "OFFICIAL",
                    "economic_impact": {"metric": "margin_target_bps", "direction": "UP", "bps": 300},
                    "confirmation": "new long-term margin target",
                }],
            }
        }
        packet = extract_catalyst_packet(payload, artifact_id="AR1", evidence_id="ER1", fallback_source_observed_at=self.now.isoformat())
        self.assertEqual(packet["catalysts"][0]["artifact_id"], "AR1")
        self.assertEqual(packet["catalysts"][0]["evidence_id"], "ER1")
        self.assertEqual(packet["catalysts"][0]["source_url"], "https://issuer.example/ir")


class StrictCatalystGateIntegrationTests(unittest.TestCase):
    def test_valid_recorded_catalyst_preserves_11_work_item_hunt(self):
        data = fixture()
        research = data["provider_recordings"]["research"]["SEC1"]
        research["source_url"] = "https://issuer.example/ir"
        research["catalysts"] = [{
            "catalyst_id": "C1",
            "event_type": "EARNINGS",
            "event_date": "2026-09-15T20:00:00Z",
            "verification_status": "CONFIRMED",
            "binding_status": "NOT_APPLICABLE",
            "economic_transmission": {"metric": "revenue_growth_pct", "direction": "UP", "magnitude": 20.0},
            "confirmation_metric": "reported revenue and forward guidance",
        }]
        agent = strict_agent(data)
        try:
            outcome = agent.run(RunMode.HUNT_ONLY, {})
            self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
            self.assertEqual(agent.store.work_item_counts(outcome.run_id).get("SUCCEEDED"), 11)
            gate = agent.store.get_stage_result(outcome.run_id, "CATALYST_GATE", "SEC1")
            self.assertIsNotNone(gate)
            self.assertIn('"decision": "PASS"', gate["result_json"])
            funnel = {row["funnel_stage"]: row["count"] for row in agent.store.list_funnel(outcome.run_id)}
            self.assertEqual(funnel["CATALYST_PASS"], 1)
            self.assertEqual(funnel["CATALYST_UNKNOWN"], 0)
        finally:
            agent.close()

    def test_missing_catalyst_blocks_before_capability_and_deep_research(self):
        data = fixture()
        research = data["provider_recordings"]["research"]["SEC1"]
        research["source_url"] = "https://issuer.example/ir"
        research.pop("catalysts", None)
        agent = strict_agent(data)
        try:
            outcome = agent.run(RunMode.HUNT_ONLY, {})
            self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")
            gate = agent.store.get_stage_result(outcome.run_id, "CATALYST_GATE", "SEC1")
            self.assertIsNotNone(gate)
            self.assertIn('"decision": "INSUFFICIENT_EVIDENCE"', gate["result_json"])
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) FROM work_items WHERE run_id=? AND stage='CAP_FUNDAMENTAL_CHANGE'", (outcome.run_id,)).fetchone()[0], 0)
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) FROM work_items WHERE run_id=? AND stage='DEEP_RESEARCH'", (outcome.run_id,)).fetchone()[0], 0)
        finally:
            agent.close()


if __name__ == "__main__":
    unittest.main()

