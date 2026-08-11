from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stock_agent.database import Database
from stock_agent.discovery.orchestrator import DiscoveryOrchestrator
from stock_agent.discovery.schemas import (CandidateFeatureSnapshot, CoverageMetrics,
                                            DiscoveryContext, DiscoveryResult, FieldValue,
                                            SecurityMasterRecord)
from stock_agent.schemas import ResearchAnalysis, UserRequest


AS_OF = "2026-08-11T00:00:00+00:00"


def source_candidate() -> CandidateFeatureSnapshot:
    security = SecurityMasterRecord(
        "US-PROMOTE", "PROMOTE", "Promotion Fixture", exchange="NASDAQ",
        sector_canonical="Technology", industry_canonical="Software",
        source="FIXTURE", themes=("Technology",))
    item = CandidateFeatureSnapshot(
        security=security, as_of=AS_OF, discovery_run_id="RUN",
        feature_version="discovery_features_v1", fields={
            "primary_financial_evidence": FieldValue(True, "KNOWN", "FIXTURE", AS_OF),
            "capital_overhang_status": FieldValue("CLEAR", "KNOWN", "FIXTURE", AS_OF),
        }, stage="DISCOVERY_STAGE_1", eligibility="ELIGIBLE",
        discovery_bucket="P1_DEEP_ANALYSIS",
        gate_results={"fuel_gate": "PASS", "fundamental_hydration_status": "READY",
                      "capital_preflight_status": "READY", "final_candidate_gate": "PASS"},
        created_at=AS_OF, first_seen_at=AS_OF, last_seen_at=AS_OF,
        last_validated_at=AS_OF, expires_at="2099-01-01T00:00:00+00:00")
    return item


def stored_result(status: str) -> DiscoveryResult:
    candidate = source_candidate()
    context = DiscoveryContext(
        "RUN", "MARKET", "", "MINIMUM", AS_OF, AS_OF, AS_OF, AS_OF, AS_OF,
        "rules", "features", "sha", "SNAPSHOT", shadow=True)
    return DiscoveryResult(
        "RUN", status, "SHADOW_ENRICHED", context,
        CoverageMetrics(1, 1, 1, 1, 1, 100, 100, 100, 100, 100, 100, 100, 100),
        {}, [], [candidate], all_candidates=[candidate])


def request() -> UserRequest:
    return UserRequest(
        "REQ", "MSG", "USER", AS_OF, "DISCOVERY DEEP RUN",
        "DISCOVERY_DEEP_HANDOFF", [], paper_action_enabled=False,
        discovery_run_id="RUN", promotion_limit=1)


def certified_child() -> dict:
    plan = SimpleNamespace(reward_risk=2.0)
    return {
        "run_id": "CHILD_PROMOTED",
        "certification": SimpleNamespace(
            certified=True, certification_status="CERTIFIED", decision_confidence=80,
            required_data_failures=[], important_data_warnings=[]),
        "decision": SimpleNamespace(decision="BUY", trade_plan=plan),
        "risk": SimpleNamespace(hard_filter_pass=True, trade_plan=plan),
        "research": ResearchAnalysis(
            ticker="PROMOTE", market_regime="RISK_ON", sector="Technology",
            signal_strength=80, catalyst_quality=70, expectation_gap=60,
            surge_elasticity=65, entry_readiness=75, capital_structure_risk=20,
            strategy_fit=80, bull_case=[], bear_case=[], suggested_decision="BUY",
            confidence=80, evidence_ids=[]),
    }


def promote_stored(status: str, directory: str, calls: list, child=None):
    database = Database(str(Path(directory) / f"{status.lower()}.sqlite"))
    database.init()
    orchestrator = DiscoveryOrchestrator(
        database, {"discovery": {"enabled": True, "cost": {
            "max_actual_llm_calls": 5, "max_llm_input_tokens": 1000,
            "max_llm_output_tokens": 1000, "max_estimated_cost_usd": 1,
            "max_child_analysis_runs": 3}}},
        handoff=lambda child_request: calls.append(child_request) or (child or {}))
    orchestrator.store.save_run(stored_result(status), AS_OF, AS_OF)
    with patch.object(orchestrator, "_portfolio_context", return_value={
            "portfolio_context_status": "READY", "remaining_risk_budget_usd": 1000}):
        return orchestrator.promote("RUN", request(), 1)


class DiscoveryMvpV5AuditTests(unittest.TestCase):
    def test_blocked_market_data_source_run_cannot_promote_p1_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            result = promote_stored("BLOCKED_MARKET_DATA", directory, calls,
                                    certified_child())
            self.assertEqual(calls, [])
            self.assertEqual(result.deep_analysis_results, [])
            self.assertEqual(result.final_selection, "NONE")
            self.assertEqual(result.final_selection_status, "NONE")
            self.assertIn("SOURCE_RUN_NOT_PROMOTABLE", result.final_selection_reason_codes)
            self.assertIn("SOURCE_STATUS_BLOCKED_MARKET_DATA", result.final_selection_reason_codes)
            self.assertIn("BENCHMARK_DATA_UNAVAILABLE", result.final_selection_reason_codes)
            self.assertIn("MARKET_REGIME_NOT_READY", result.final_selection_reason_codes)

    def test_existing_blocked_data_and_coverage_states_still_cannot_promote(self):
        for status in ("BLOCKED_DATA", "BLOCKED_COVERAGE"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                calls = []
                result = promote_stored(status, directory, calls, certified_child())
                self.assertEqual(calls, [])
                self.assertEqual(result.final_selection, "NONE")
                self.assertIn("SOURCE_RUN_NOT_PROMOTABLE", result.final_selection_reason_codes)
                self.assertIn(f"SOURCE_STATUS_{status}", result.final_selection_reason_codes)

    def test_unknown_and_future_source_statuses_fail_closed(self):
        for status in ("SOME_NEW_BLOCKED_STATUS", "BLOCKED_COST", "FAILED", "CANCELLED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                calls = []
                result = promote_stored(status, directory, calls, certified_child())
                self.assertEqual(calls, [])
                self.assertEqual(result.deep_analysis_results, [])
                self.assertIn("SOURCE_RUN_NOT_PROMOTABLE", result.final_selection_reason_codes)
                self.assertIn(f"SOURCE_STATUS_{status}", result.final_selection_reason_codes)

    def test_completed_shadow_enriched_source_run_still_promotes_normally(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            result = promote_stored("COMPLETED_SHADOW_ENRICHED", directory, calls,
                                    certified_child())
            self.assertEqual([item.intent for item in calls], ["ANALYZE"])
            self.assertEqual(len(result.deep_analysis_results), 1)
            self.assertEqual(result.deep_analysis_results[0]["certified"], True)
            self.assertEqual(result.final_selection, "PROMOTE")

    def test_market_only_source_run_is_not_promotable(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            result = promote_stored("COMPLETED_SHADOW_MARKET_ONLY", directory, calls,
                                    certified_child())
            self.assertEqual(calls, [])
            self.assertEqual(result.final_selection, "NONE")
            self.assertEqual(result.deep_analysis_results, [])
            self.assertIn("SOURCE_RUN_NOT_PROMOTABLE", result.final_selection_reason_codes)
            self.assertIn("SOURCE_STATUS_COMPLETED_SHADOW_MARKET_ONLY",
                          result.final_selection_reason_codes)


if __name__ == "__main__":
    unittest.main()
