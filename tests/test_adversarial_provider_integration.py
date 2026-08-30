from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root

from stock_agent.adapters import RecordedMarketDataProvider, RecordedPortfolioProvider, RecordedResearchEvidenceProvider, RecordedSECProvider
from stock_agent.models import RunMode, WorkStatus
from stock_agent.providers import FakeProvider
from stock_agent.providers import DeepSeekProvider
from unittest.mock import patch
from stock_agent.prompt_runtime import PromptRuntime
from stock_agent.runtime import ProductionStockAgent, StockAgent, StockAgentConfig
from stock_agent.store import SQLiteStore
from tests.test_stock_agent import execution_input, market_context_fixture


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = canonical_prompt_library_root()
FIXTURE = ROOT / "tests" / "fixtures" / "strict_provider_recorded_input.json"


def fixture() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # A recorded acceptance has an explicit capture clock and must remain
    # deterministic as wall time advances. Legacy ad-hoc fixtures without a
    # capture clock retain the dynamic helper for compatibility.
    if not data["provider_recordings"].get("_recorded_at"):
        data["provider_recordings"]["market_context"] = market_context_fixture()
    return data


def strict_agent(data: dict, provider=None, sec=None, research=None, store=None) -> ProductionStockAgent:
    recordings = data["provider_recordings"]
    market = RecordedMarketDataProvider(recordings)
    portfolio = RecordedPortfolioProvider(recordings["portfolio_snapshot"])
    config = StockAgentConfig(LIBRARY, Path(":memory:"), strict_inputs=True, market_data_provider=market, sec_provider=sec if sec is not None else RecordedSECProvider(recordings["sec"]), portfolio_provider=portfolio, research_provider=research if research is not None else RecordedResearchEvidenceProvider(recordings["research"]))
    return ProductionStockAgent(config, store=store, provider=provider or FakeProvider())


class AdversarialProviderIntegrationTests(unittest.TestCase):
    def test_neg01_prompt_body_is_in_provider_request(self):
        runtime = PromptRuntime(LIBRARY); agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        captured = {}
        def call(request):
            captured.update(request); return agent._valid_payload(runtime.compose("workflow.stock_researcher")["output_schema"])
        required = runtime.prompts["workflow.stock_researcher"].get("required_inputs", [])
        context_values = {"run_id": "r", "stage": "s", "run_mode": "HUNT_ONLY", "effective_rule_pack": "h", **{key: {} for key in required}}
        runtime.strict_call("workflow.stock_researcher", call, context=runtime.context_manifest(context_values, ["run_id", "stage", "run_mode", "effective_rule_pack"] + required), run_mode="HUNT_ONLY")
        marker = runtime.prompts["workflow.stock_researcher"]["_body"].split()[0]
        self.assertIn(marker, captured["prompt_body"])
        self.assertEqual(captured["prompt_body_hash"], runtime.compose("workflow.stock_researcher")["compiled_prompt_hash"])

    def test_neg01b_deepseek_transport_contains_compiled_body(self):
        captured = {}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],"usage":{}}'
        def fake_open(request, timeout=0):
            captured["body"] = request.data.decode("utf-8"); return Response()
        with patch("urllib.request.urlopen", side_effect=fake_open):
            DeepSeekProvider("dummy").call({"prompt_body": "UNIQUE_PROMPT_BODY_MARKER", "default_payload": {"status": "COMPLETE"}, "output_schema_definition": {"type": "object"}})
        self.assertIn("UNIQUE_PROMPT_BODY_MARKER", captured["body"])
        self.assertNotIn("default_payload", captured["body"])
        self.assertNotIn("COMPLETE", captured["body"])

    def test_neg02_empty_sec_cannot_qualify(self):
        data = fixture(); agent = strict_agent(data, sec=RecordedSECProvider({}))
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertNotEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")

    def test_neg03_unresolved_critical_is_persisted_and_blocks_pool(self):
        def responder(request):
            payload = copy.deepcopy(request["default_payload"])
            if request.get("prompt_id") == "workflow.adversarial_reviewer":
                payload["unresolved_critical_issues"] = [{"issue_id": "I-CRIT-1", "severity": "CRITICAL", "category": "SEC", "finding": "unresolved", "evidence_ids": ["E1"]}]
            return payload
        data = fixture(); store = SQLiteStore(":memory:"); agent = strict_agent(data, provider=FakeProvider(responder), store=store)
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertNotEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
        self.assertGreater(store.connection.execute("SELECT COUNT(*) FROM debate_issues WHERE severity='CRITICAL'").fetchone()[0], 0)

    def test_neg04_final_synthesis_blocked_cannot_commit_starter(self):
        data = fixture()
        def responder(request):
            return copy.deepcopy(request["default_payload"])
        agent = strict_agent(data, provider=FakeProvider(responder))
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, {"requested_action": "STARTER", "starter_plan": {}})
        self.assertNotEqual(outcome.outcome, "FINAL_ACTION_COMMITTED")

    def test_neg05_blocked_synthesis_ignores_caller_action(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, execution_input("STARTER"))
        self.assertNotEqual(outcome.outcome, "FINAL_ACTION_COMMITTED")
        self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) FROM final_actions").fetchone()[0], 0)

    def test_neg06_stale_market_source_is_rejected(self):
        data = fixture(); data["provider_recordings"]["market_context"]["observed_at"] = "2000-01-01T00:00:00Z"
        agent = strict_agent(data)
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertNotEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")

    def test_non_sec_research_provider_is_mandatory_in_strict_path(self):
        data = fixture(); agent = strict_agent(data, research=None)
        agent.config.research_provider = None
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertNotEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")

    def test_neg06b_stale_sec_source_is_rejected(self):
        data = fixture()
        for value in data["provider_recordings"]["sec"]["SEC1"].values():
            if isinstance(value, dict): value["observed_at"] = "2000-01-01T00:00:00Z"
        agent = strict_agent(data)
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertNotEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")

    def test_neg07_terminal_run_work_item_cannot_be_released(self):
        store = SQLiteStore(":memory:"); run = store.create_run(RunMode.HUNT_ONLY, __import__("stock_agent.models", fromlist=["EffectiveRuleSet"]).EffectiveRuleSet(), "ctx", 0)
        store.enqueue(run, "MARKET_ANALYSIS", {"prerequisites": {}}, "dep"); store.finish_run(run.run_id, "BLOCKED_BY_CRITICAL_ISSUE")
        self.assertIsNone(store.lease_next("zombie-worker"))

    def test_neg08_hunt_only_has_no_final_action(self):
        data = fixture(); agent = strict_agent(data)
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertIsNone(outcome.authoritative_action)


if __name__ == "__main__": unittest.main()

