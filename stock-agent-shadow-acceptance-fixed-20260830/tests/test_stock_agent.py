from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root

from stock_agent.dependencies import DependencyFence
from stock_agent.gates import ContractViolation, validate_failure_paths, make_economic_assessment_receipt
from stock_agent.models import EffectiveRuleSet, Evidence, ExecutionAction, RunMode, WorkStatus, canonical_hash, utc_now
from stock_agent.runtime import StockAgent, StockAgentConfig
from stock_agent.store import SQLiteStore
from stock_agent.providers import FakeProvider


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = canonical_prompt_library_root()


def candidate(security_id: str = "SEC1") -> dict:
    return {"security_id": security_id, "recommended_discovery_action": "DEEP_DIVE_NOW", "proposed_stage": "STAGE_1", "stage_eligible": True, "capital_prescreen": {"complete": True}, "research_status": "COMPLETE", "audit_recommendation": "SUPPORTS_CONTINUATION", "failure_paths": [{"category": c, "scenario": f"scenario-{i}", "causal_path": f"cause-{i}", "probability_direction": "INCREASES_DOWNSIDE", "severity": "MAJOR", "source_evidence_ids": [f"E{i}"]} for i, c in enumerate(["FUNDAMENTAL", "CAPITAL_STRUCTURE", "PRICING_EXPECTATION"], 1)]}


def starter_plan() -> dict:
    analysis = {"status": "COMPLETE", "details": "validated", "evidence_ids": ["E1"]}
    response = {"response": "HOLD_WITHIN_PLAN", "conditions": ["condition"]}
    return {"starter_zone": analysis, "starter_shares": 1, "starter_capital_pct": 1.0, "maximum_position": {"shares": 2, "capital_pct": 2.0}, "execution_stop": analysis, "thesis_stop": analysis, "structural_bear": analysis, "worst_plausible_gap": analysis, "maximum_account_loss": analysis, "maximum_holding_period": {"minimum_days": 7, "maximum_days": 56}, "time_stop_or_reassessment_condition": "reassess at 56 days", "breakout_response": response, "pullback_response": response, "planned_add": {"trigger_id": "T1", "trigger_type": "CONTRACT", "trigger_description": "contract confirmation", "required_evidence_classes": ["COMPANY"], "planned_add_shares": 1, "planned_add_capital_pct": 1.0, "resulting_position_cap": {"shares": 2, "capital_pct": 2.0}}}


def market_context_fixture() -> dict:
    stamp = utc_now()
    assets = {
        symbol: {"symbol": symbol, "observed_at": stamp, "source": "recorded-test", "observation_count": 3}
        for symbol in ("SPY", "QQQ", "IWM", "SOXX", "VIX", "US10Y", "DXY", "WTI", "BTC", "ETH")
    }
    return {"complete": True, "regime": "RISK_ON", "breadth": "BROAD", "volatility": "NORMAL", "normalization_status": "COMPLETE", "assets": assets}


def execution_input(action: str = "STARTER") -> dict:
    return {"context_ids": ["run_mode", "effective_rule_pack"], "market_context": market_context_fixture(), "sector": {"eligible": True}, "market_execution": {"core_input_complete": True}, "candidates": [candidate()], "requested_action": action, "shares": 1, "capital_pct": 1.0, "starter_plan": starter_plan()}


class AgentIntegrationTests(unittest.TestCase):
    def make_agent(self) -> StockAgent:
        def responder(request):
            payload = copy.deepcopy(request["default_payload"])
            if request.get("prompt_id") == "workflow.final_synthesis_agent":
                action = ((request.get("runtime_input") or {}).get("requested_action") or "STARTER")
                payload.update({"recommendation_status": "READY", "recommended_action": action, "blocking_reason_codes": []})
                if action == "STARTER":
                    base = starter_plan(); summary = {"summary": "validated", "evidence_ids": ["E1"], "unknowns": []}
                    payload["starter_plan"] = {**base, "starter_zone": summary, "execution_stop": summary, "thesis_stop": summary, "structural_bear": summary, "worst_plausible_gap": summary, "maximum_account_loss": summary}
                if action == "ADD":
                    stamp = "2026-08-18T00:00:00Z"; digest = "0" * 64
                    payload["action_scope"] = "EXISTING_POSITION"
                    payload["add_plan"] = {"trigger_id": "T1", "trigger_type": "CONTRACT", "trigger_description": "contract confirmation", "strengthening_evidence_ids": ["E2"], "shares": 1}
                    payload["position_snapshot_receipt"] = {"receipt_type": "PositionSnapshotReceiptV2", "subject_id": "SEC1", "position_exists": True, "snapshot_hash": digest, "as_of": stamp, "receipt_hash": digest}
                    payload["prior_add_trigger_receipt"] = {"receipt_type": "PriorAddTriggerReceiptV2", "subject_id": "SEC1", "trigger_id": "T1", "trigger_type": "CONTRACT", "defined_at": stamp, "receipt_hash": digest}
                    payload["fresh_evidence_delta_receipt"] = {"receipt_type": "FreshnessDeltaReceiptV2", "subject_id": "SEC1", "delta_state": "STRENGTHENED", "strengthening_evidence_ids": ["E2"], "evidence_snapshot_hash": digest, "evaluated_at": stamp, "receipt_hash": digest}
                    payload["strengthening_evidence_receipt"] = {"receipt_type": "StrengtheningEvidenceReceiptV2", "subject_id": "SEC1", "strengthening_evidence_ids": ["E2"], "evidence_snapshot_hash": digest, "evaluated_at": stamp, "receipt_hash": digest}
            return payload
        return StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")), provider=FakeProvider(responder))

    def test_hunt_only_candidate_terminates_without_execution(self) -> None:
        agent = self.make_agent()
        outcome = agent.run(RunMode.HUNT_ONLY, execution_input())
        self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
        self.assertIsNone(outcome.authoritative_action)
        self.assertIsNone(outcome.recommendation)

    def test_hunt_only_empty_pool(self) -> None:
        agent = self.make_agent()
        data = execution_input(); data["candidates"] = []
        outcome = agent.run(RunMode.HUNT_ONLY, data)
        self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")

    def test_legacy_execution_starter_cannot_commit_without_authoritative_lineage(self) -> None:
        agent = self.make_agent()
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, execution_input())
        self.assertIsNone(outcome.authoritative_action)
        self.assertEqual(outcome.outcome, "BLOCKED_BY_CRITICAL_ISSUE")

    def test_add_requires_strengthening_not_price_decline(self) -> None:
        agent = self.make_agent()
        data = execution_input("ADD")
        data["position_snapshot"] = {"receipt_type": "PositionSnapshotReceiptV2", "subject_id": "SEC1", "position_exists": True}
        data["add_plan"] = {"trigger_id": "T1", "trigger_type": "CONTRACT", "strengthening_evidence_ids": ["E2"]}
        data["prior_add_trigger_receipt"] = {"receipt_type": "PriorAddTriggerReceiptV2", "subject_id": "SEC1", "trigger_id": "T1", "trigger_type": "CONTRACT"}
        data["fresh_evidence_delta_receipt"] = {"receipt_type": "FreshnessDeltaReceiptV2", "subject_id": "SEC1", "delta_state": "UNCHANGED", "strengthening_evidence_ids": ["E2"]}
        data["strengthening_evidence_receipt"] = {"receipt_type": "StrengtheningEvidenceReceiptV2", "subject_id": "SEC1", "strengthening_evidence_ids": ["E2"]}
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, data)
        self.assertEqual(outcome.outcome, "BLOCKED_BY_CRITICAL_ISSUE")

    def test_legacy_add_with_strengthening_evidence_cannot_commit_without_authoritative_lineage(self) -> None:
        agent = self.make_agent(); data = execution_input("ADD")
        data["position_snapshot"] = {"receipt_type": "PositionSnapshotReceiptV2", "subject_id": "SEC1", "position_exists": True}
        data["add_plan"] = {"trigger_id": "T1", "trigger_type": "CONTRACT", "strengthening_evidence_ids": ["E2"]}
        data["prior_add_trigger_receipt"] = {"receipt_type": "PriorAddTriggerReceiptV2", "subject_id": "SEC1", "trigger_id": "T1", "trigger_type": "CONTRACT"}
        data["fresh_evidence_delta_receipt"] = {"receipt_type": "FreshnessDeltaReceiptV2", "subject_id": "SEC1", "delta_state": "STRENGTHENED", "strengthening_evidence_ids": ["E2"]}
        data["strengthening_evidence_receipt"] = {"receipt_type": "StrengtheningEvidenceReceiptV2", "subject_id": "SEC1", "strengthening_evidence_ids": ["E2"]}
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, data)
        self.assertIsNone(outcome.authoritative_action)
        self.assertEqual(outcome.outcome, "BLOCKED_BY_CRITICAL_ISSUE")

    def test_full_mismatched_position_is_rejected(self) -> None:
        agent = self.make_agent(); data = execution_input("FULL"); data["position_snapshot"] = {"receipt_type": "PositionSnapshotReceiptV2", "subject_id": "OTHER", "position_exists": True}
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, data)
        self.assertEqual(outcome.outcome, "BLOCKED_BY_CRITICAL_ISSUE")

    def test_critical_issue_blocks_execution(self) -> None:
        agent = self.make_agent(); data = execution_input(); data["unresolved_critical"] = True
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, data)
        self.assertEqual(outcome.outcome, "BLOCKED_BY_CRITICAL_ISSUE")

    def test_stage3_is_not_deep_research_eligible(self) -> None:
        agent = self.make_agent(); data = execution_input(); data["candidates"][0]["proposed_stage"] = "STAGE_3"
        outcome = agent.run(RunMode.HUNT_ONLY, data)
        self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")

    def test_discovery_execution_action_is_rejected(self) -> None:
        agent = self.make_agent(); data = execution_input(); data["candidates"][0]["recommended_discovery_action"] = "STARTER"
        outcome = agent.run(RunMode.HUNT_ONLY, data)
        self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")

    def test_market_execution_incomplete_cannot_pass(self) -> None:
        agent = self.make_agent(); data = execution_input(); data["market_execution"] = {"core_input_complete": False}
        data["candidates"][0]["reverse_valuation"] = {"receipt_type": "ReverseValuationReceiptV2", "status": "COMPLETE", "benchmark_implied_upside_pct": 0.5}
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, data)
        self.assertEqual(outcome.outcome, "BLOCKED_BY_EVIDENCE_GAP")

    def test_legacy_action_is_not_an_execution_action(self) -> None:
        with self.assertRaises(ValueError): ExecutionAction("BUY")


class ContractAndQueueTests(unittest.TestCase):
    def test_economic_receipt_requires_authoritative_lineage(self) -> None:
        store = SQLiteStore(":memory:"); rules = EffectiveRuleSet(); run = store.create_run(RunMode.HUNT_AND_EXECUTION_REVIEW, rules, "ctx", 1)
        store.upsert_evidence(Evidence("E1", "SEC1", "RESEARCH", "2026-08-18T00:00:00Z", 1, canonical_hash({"e": 1}), "RAW"))
        result_id = store.record_stage_result(run.run_id, None, "DEEP_RESEARCH", "SEC1", {"research_status": "COMPLETE"}, ["E1"], store.dependency_hash(["E1"], rules.rule_set_hash, "ctx"), store.current_evidence_epoch_for(["E1"]))
        receipt = make_economic_assessment_receipt(security_id="SEC1", current_price=10, bull_value=20, base_value=12, bear_value=5, bull_probability=.3, base_probability=.5, bear_probability=.2, opportunity_cost_score=1, evidence_ids=["E1"], source_result_ids=[result_id])
        self.assertEqual(store.validate_economic_receipt(run.run_id, "SEC1", receipt, ["E1"])["receipt_type"], "EconomicAssessmentReceiptV2")
        receipt["probability_weighted_ev"] = 999
        with self.assertRaises(ContractViolation):
            store.validate_economic_receipt(run.run_id, "SEC1", receipt, ["E1"])
        store.close()

    def test_failure_paths_and_structural_bear(self) -> None:
        paths = candidate()["failure_paths"]
        validate_failure_paths(paths)
        duplicate = copy.deepcopy(paths); duplicate[1]["scenario"] = duplicate[0]["scenario"]; duplicate[1]["causal_path"] = duplicate[0]["causal_path"]
        with self.assertRaises(ContractViolation): validate_failure_paths(duplicate)

    def test_stale_worker_result_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "agent.db")
            rules = EffectiveRuleSet(); run = store.create_run(RunMode.HUNT_ONLY, rules, "ctx", 1)
            item = store.enqueue(run, "DISCOVERY", {"x": 1}, "dependency-a")
            leased = store.lease_next("worker", 60)
            self.assertIsNotNone(leased)
            status = store.complete(leased, {"ok": True}, "dependency-b", 1, rules.rule_set_hash, "ctx")
            self.assertEqual(status, WorkStatus.STALE_ON_ARRIVAL)
            store.close()

    def test_lease_heartbeat_token(self) -> None:
        store = SQLiteStore(":memory:"); rules = EffectiveRuleSet(); run = store.create_run(RunMode.HUNT_ONLY, rules, "ctx", 1); store.enqueue(run, "DISCOVERY", {}, "d")
        item = store.lease_next("worker")
        self.assertTrue(store.heartbeat(item.work_item_id, item.lease_token))
        self.assertFalse(store.heartbeat(item.work_item_id, "wrong-token"))
        store.close()

    def test_evidence_refresh_invalidates_dependency_state(self) -> None:
        store = SQLiteStore(":memory:"); rules = EffectiveRuleSet(); fence = DependencyFence(store)
        evidence = Evidence("E1", "SEC1", "COMPANY", "2026-08-17T00:00:00Z", 1, canonical_hash({"version": 1}))
        store.upsert_evidence(evidence)
        before = fence.state(["E1"], rules, "ctx")
        refreshed = Evidence("E1", "SEC1", "COMPANY", "2026-08-18T00:00:00Z", 2, canonical_hash({"version": 2}))
        store.upsert_evidence(refreshed)
        after = fence.state(["E1"], rules, "ctx")
        decision = fence.compare(before, after)
        self.assertFalse(decision.fresh)
        self.assertEqual(decision.state, "STALE_ON_ARRIVAL")
        store.close()

    def test_fresh_money_two_positive_commitments_are_rejected(self) -> None:
        store = SQLiteStore(":memory:"); rules = EffectiveRuleSet(); run = store.create_run(RunMode.HUNT_AND_EXECUTION_REVIEW, rules, "ctx", 1)
        with self.assertRaises(ValueError): store.commit_final_allocation(run, "STARTER", {"shares": 1}, 2)
        store.close()


if __name__ == "__main__":
    unittest.main()


