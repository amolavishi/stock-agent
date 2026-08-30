from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from stock_agent.adapters import RecordedMarketDataProvider, RecordedPortfolioProvider, RecordedResearchEvidenceProvider, RecordedSECProvider
from stock_agent.models import RunMode
from stock_agent.providers import ModelProfile, ModelRouter, OpenAICompatibleProvider
from stock_agent.prompt_runtime import PromptContractError
from stock_agent.gates import ContractViolation, require_fresh, make_economic_assessment_receipt
from stock_agent.models import EffectiveRuleSet, Evidence, RawArtifact, canonical_hash, utc_now
from unittest.mock import patch
from stock_agent.runtime import ProductionStockAgent, StockAgent, StockAgentConfig
from stock_agent.store import SQLiteStore
from tests.test_adversarial_provider_integration import FIXTURE, LIBRARY, fixture
from tests.test_stock_agent import market_context_fixture


class CapturingProvider:
    provider = "capture"
    model = "capture-test"

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.agent = None

    def call(self, request):
        self.requests.append(copy.deepcopy(request))
        payload = self.agent._valid_payload(self.agent.prompts.compose(request["prompt_id"])["output_schema"])
        if request["prompt_id"] == "workflow.stock_scout":
            payload["candidates"] = [{"security_id": "SEC1", "recommended_discovery_action": "DEEP_DIVE_NOW", "proposed_stage": "STAGE_1", "rationale": "capture", "evidence_ids": ["E1"]}]
        if request["prompt_id"] == "utility.capital_structure_prescreen":
            for key in ("active_atm", "large_shelf_and_financing_need", "toxic_convertible", "material_warrant", "imminent_financing", "cash_runway_critical"):
                payload[key] = {"state": "FALSE", "details": {"summary": "capture", "evidence_ids": ["E1"], "unknowns": []}, "evidence_ids": ["E1"]}
            payload["extraction_status"] = "COMPLETE"
        if request["prompt_id"] == "workflow.stock_researcher":
            payload["research_status"] = "COMPLETE"
        if request["prompt_id"] == "utility.sec_extraction":
            payload["status"] = "COMPLETE"
        if request["prompt_id"] == "workflow.adversarial_reviewer":
            payload["audit_recommendation"] = "SUPPORTS_CONTINUATION"
        return payload, {"provider": self.provider, "model": self.model, "input_tokens": 1, "output_tokens": 1, "cached_tokens": 0, "latency_ms": 0, "finish_reason": "stop", "retry_count": 0, "actual_cost": 0.0}


class IndependentAuditRegressionTests(unittest.TestCase):
    def test_p0_forged_semantic_context_is_rejected(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        runtime = agent.prompts
        required = runtime.prompts["workflow.stock_researcher"]["required_inputs"]
        context_values = {"run_id": "r", "stage": "DEEP_RESEARCH", "run_mode": "HUNT_ONLY", "effective_rule_pack": "h"}
        for key in required:
            if key in {"run_id", "stage", "run_mode", "effective_rule_pack"}:
                continue
            value = {"source_stage": "WRONG_STAGE", "content_type": "WrongType", "value": {"same": "payload"}, "content_hash": "forged-" + key, "upstream_receipt": {"receipt_id": "input-receipt:forged", "receipt_type": "ContextReceiptV2", "source_stage": "WRONG_STAGE", "content_type": "WrongType", "content_hash": "forged-" + key, "receipt_hash": "forged"}}
            context_values[key] = value
        context_values["semantic_context"] = True
        context_values["upstream_receipt_ids"] = []
        context = runtime.context_manifest(context_values, ["run_id", "stage", "run_mode", "effective_rule_pack"])
        with self.assertRaises(PromptContractError):
            runtime.strict_call("workflow.stock_researcher", lambda request: agent._valid_payload(runtime.compose("workflow.stock_researcher")["output_schema"]), context=context, run_mode="HUNT_ONLY")

    def test_p0_future_timestamp_is_not_fresh(self):
        with self.assertRaises(ContractViolation):
            require_fresh("2099-01-01T00:00:00Z", 3600, "future-test")

    def test_p0_luna_reasoning_effort_is_on_wire(self):
        captured = {}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],"usage":{}}'
        def fake_open(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8")); return Response()
        provider = OpenAICompatibleProvider("dummy", "gpt-5.6-luna", "https://example.invalid", reasoning_effort="xhigh")
        with patch("urllib.request.urlopen", side_effect=fake_open):
            provider.call({"prompt_body": "marker", "output_schema_definition": {"type": "object"}})
        self.assertEqual(captured["body"]["model"], "gpt-5.6-luna")
        self.assertEqual(captured["body"]["reasoning_effort"], "xhigh")
        high = OpenAICompatibleProvider("dummy", "gpt-5.6-luna", "https://example.invalid", reasoning_effort="high")
        with patch("urllib.request.urlopen", side_effect=fake_open):
            high.call({"prompt_body": "marker", "output_schema_definition": {"type": "object"}})
        self.assertEqual(captured["body"]["reasoning_effort"], "high")

    def test_p1_router_telemetry_uses_selected_provider(self):
        class Routed:
            provider = "implementation"
            model = "implementation-model"
            def call(self, request):
                return {}, {"provider": "implementation", "model": "implementation-model", "input_tokens": 999, "output_tokens": 3, "actual_cost": 9.99}
        profile = ModelProfile("LUNA_EXTRA_HIGH", "selected-extra", "EXTRA-MODEL", reasoning_effort="xhigh")
        _, telemetry = ModelRouter({"selected-extra": Routed()}, {"LUNA_EXTRA_HIGH": profile}).call("LUNA_EXTRA_HIGH", {})
        self.assertEqual(telemetry["provider"], "selected-extra")
        self.assertEqual(telemetry["model"], "EXTRA-MODEL")
        self.assertEqual(telemetry["input_tokens"], 999)
        self.assertEqual(telemetry["actual_cost"], 9.99)
    def test_p0_required_inputs_are_typed_and_not_aliased(self):
        provider = CapturingProvider()
        profiles = {name: ModelProfile(name, "capture", "capture-test") for name in ("FAST_CHEAP", "BALANCED", "DEEP_REASONING", "CRITICAL_AUDIT", "LUNA_HIGH", "LUNA_EXTRA_HIGH")}
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")), provider=provider, router=ModelRouter({"capture": provider}, profiles))
        provider.agent = agent
        data = {"market_context": {**market_context_fixture(), "sector_relative_strength": {"TECH": 1}}, "sector": {"industry_driver_snapshot": {"driver": "test"}}, "candidates": [{"security_id": "SEC1", "recommended_discovery_action": "DEEP_DIVE_NOW", "proposed_stage": "STAGE_1", "evidence_ids": ["E1"], "capital_prescreen": {"complete": True, "active_atm": {"state": "FALSE"}, "large_shelf_and_financing_need": {"state": "FALSE"}, "toxic_convertible": {"state": "FALSE"}, "material_warrant": {"state": "FALSE"}, "imminent_financing": {"state": "FALSE"}, "cash_runway_critical": {"state": "FALSE"}}, "failure_paths": [{"category": c, "scenario": c, "causal_path": c + "-cause", "probability_direction": "INCREASES_DOWNSIDE", "severity": "MAJOR", "source_evidence_ids": ["E1"]} for c in ("FUNDAMENTAL", "CAPITAL_STRUCTURE", "PRICING_EXPECTATION")] }]}
        outcome = agent.run(RunMode.HUNT_ONLY, data)
        self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
        final = [request for request in provider.requests if request["prompt_id"] == "workflow.stock_researcher"][0]
        entries = {entry["id"]: entry["content"] for entry in final["context_manifest"]["entries"]}
        required = agent.prompts.prompts["workflow.stock_researcher"]["required_inputs"]
        hashes = [entries[key]["content_hash"] for key in required if key in entries and key not in {"effective_rule_pack", "run_mode"}]
        self.assertGreater(len(set(hashes)), 3)
        self.assertNotIn("default_payload", final)

    def test_p0_raw_universe_without_future_statuses_uses_real_funnel(self):
        data = fixture()
        row = data["provider_recordings"]["candidates"][0]
        for key in ("research_status", "full_sec_status", "audit_status", "capital_prescreen", "stage_eligible", "recommended_discovery_action", "proposed_stage", "failure_paths"):
            row.pop(key, None)
        agent = self._strict(data)
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
        types = {r["artifact_type"] for r in agent.store.connection.execute("SELECT artifact_type FROM raw_artifacts")}
        self.assertTrue({"MARKET_CONTEXT", "UNIVERSE", "TECHNICAL_FEATURES", "SEC_CHEAP_FACTS"}.issubset(types))

    def test_p0_capital_prescreen_ignores_universe_prebaked_field(self):
        data = fixture()
        data["provider_recordings"]["candidates"][0]["capital_prescreen"] = {"toxic_convertible": {"state": "TRUE"}}
        agent = self._strict(data)
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")

    def test_p0_missing_sec_cheap_facts_fails_closed_before_research(self):
        data = fixture()
        data["provider_recordings"]["sec"]["SEC1"].pop("cheap_facts", None)
        agent = self._strict(data)
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertNotEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")

    def _strict(self, data):
        recordings = data["provider_recordings"]
        config = StockAgentConfig(LIBRARY, Path(":memory:"), strict_inputs=True, market_data_provider=RecordedMarketDataProvider(recordings), sec_provider=RecordedSECProvider(recordings["sec"]), portfolio_provider=RecordedPortfolioProvider(recordings["portfolio_snapshot"]), research_provider=RecordedResearchEvidenceProvider(recordings["research"]))
        return ProductionStockAgent(config, store=SQLiteStore(":memory:"))


if __name__ == "__main__":
    unittest.main()


