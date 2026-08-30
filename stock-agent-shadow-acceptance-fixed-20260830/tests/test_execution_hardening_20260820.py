from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root
from unittest.mock import patch

from stock_agent.models import EffectiveRuleSet, RunMode, canonical_hash, utc_now
from stock_agent.prompt_runtime import PromptContractError, PromptRuntime
from stock_agent.providers import CodexExecError, CodexExecProvider, ModelProfile, ModelRouter
from stock_agent.runtime import StockAgent, StockAgentConfig
from stock_agent.store import SQLiteStore
from tests.test_stock_agent import starter_plan
from stock_agent.adapters import RecordedPortfolioProvider


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = canonical_prompt_library_root()


class _TransientProvider:
    provider = "transient"
    model = "transient-v1"

    def __init__(self):
        self.calls = 0

    def call(self, request):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary provider failure")
        return {"ok": True}, {"provider": self.provider, "model": self.model, "retry_count": 0}


class ExecutionHardeningTests(unittest.TestCase):
    @staticmethod
    def _recorded_agent():
        # Import inside the helper so unittest discovery does not collect the
        # imported ProductionAdapterTests class a second time.
        from tests.test_production_adapters import ProductionAdapterTests
        return ProductionAdapterTests().make()

    @staticmethod
    def _valid_starter_plan():
        plan = starter_plan()
        summary = {"summary": "validated", "evidence_ids": ["E1"], "unknowns": []}
        for key in ("starter_zone", "execution_stop", "thesis_stop", "structural_bear", "worst_plausible_gap", "maximum_account_loss"):
            plan[key] = summary
        return plan

    def test_router_retries_transient_provider_failure(self):
        provider = _TransientProvider()
        router = ModelRouter({"transient": provider}, {"BALANCED": ModelProfile("BALANCED", "transient", "transient-v1", max_retries=1)})
        payload, telemetry = router.call("BALANCED", {"prompt_id": "retry"})
        self.assertEqual(payload["ok"], True)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(telemetry["retry_count"], 1)

    def test_router_retry_exhaustion_is_fail_closed(self):
        class AlwaysFail(_TransientProvider):
            def call(self, request):
                self.calls += 1
                raise RuntimeError("still unavailable")
        provider = AlwaysFail()
        router = ModelRouter({"transient": provider}, {"BALANCED": ModelProfile("BALANCED", "transient", "transient-v1", max_retries=1)})
        with self.assertRaises(RuntimeError):
            router.call("BALANCED", {"prompt_id": "retry"})
        self.assertEqual(provider.calls, 2)

    def test_codex_exec_uses_read_only_and_schema(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            captured["schema"] = json.loads(Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}\n', stderr="")

        schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}
        with patch("stock_agent.providers.subprocess.run", side_effect=fake_run):
            payload, telemetry = CodexExecProvider(timeout=3).call({"prompt_id": "smoke", "prompt_body": "Return JSON.", "output_schema_definition": schema, "reasoning_effort": "high"})
        self.assertEqual(payload, {"ok": True})
        self.assertIn("--sandbox", captured["command"])
        self.assertEqual(captured["command"][captured["command"].index("--sandbox") + 1], "read-only")
        self.assertIn("--ask-for-approval", captured["command"])
        self.assertIn('model_reasoning_effort="high"', captured["command"])
        self.assertEqual(captured["schema"], schema)
        self.assertEqual(telemetry["provider"], "codex_exec")

    def test_codex_exec_xhigh_routing_and_no_api_key_forwarding(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}', stderr="")

        schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "should-not-forward", "DEEPSEEK_API_KEY": "should-not-forward"}), patch("stock_agent.providers.subprocess.run", side_effect=fake_run):
            _, telemetry = CodexExecProvider().call({"prompt_body": "Return JSON.", "output_schema_definition": schema, "reasoning_effort": "xhigh"})
        self.assertEqual(telemetry["reasoning_effort"], "xhigh")
        self.assertNotIn("OPENAI_API_KEY", captured["env"])
        self.assertNotIn("DEEPSEEK_API_KEY", captured["env"])

    def test_codex_exec_timeout_nonzero_and_malformed_fail_closed(self):
        schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
        with patch("stock_agent.providers.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 1)):
            with self.assertRaises(CodexExecError):
                CodexExecProvider(timeout=1).call({"prompt_body": "Return JSON.", "output_schema_definition": schema})
        with patch("stock_agent.providers.subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="secret")):
            with self.assertRaises(CodexExecError):
                CodexExecProvider().call({"prompt_body": "Return JSON.", "output_schema_definition": schema})
        with patch("stock_agent.providers.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="not-json", stderr="")):
            with self.assertRaises(CodexExecError):
                CodexExecProvider().call({"prompt_body": "Return JSON.", "output_schema_definition": schema})

    def test_prompt_declared_dependencies_are_materialized_in_parent_context(self):
        fixture = self._recorded_agent()
        outcome = fixture.run(RunMode.HUNT_ONLY, {})
        self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
        calls = getattr(fixture.provider, "calls", [])
        research_call = next(call for call in calls if call["request"]["prompt_id"] == "workflow.stock_researcher")
        included = set(research_call["request"]["context_manifest"]["included_context_ids"])
        self.assertIn("PROMPT:capability.fundamental_change_quality", included)
        self.assertIn("PROMPT:capability.catalyst_expectation_gap", included)
        self.assertIn("PROMPT:capability.directional_probability_hypothesis", included)

    def test_prompt_declared_dependency_missing_is_rejected(self):
        runtime = PromptRuntime(LIBRARY)
        context = runtime.context_manifest({"run_mode": "HUNT_ONLY", "effective_rule_pack": "hash", "semantic_context": True, "enforce_declared_dependencies": True, "upstream_receipt_ids": []}, ["run_mode", "effective_rule_pack"])
        with self.assertRaises(PromptContractError):
            runtime.strict_call("workflow.stock_researcher", lambda request: {}, context=context, run_mode="HUNT_ONLY")

    def test_canonical_input_receipt_id_is_required(self):
        runtime = PromptRuntime(LIBRARY)
        value = {"source_stage": "MARKET_DATA", "content_type": "MarketContext", "value": {"complete": True}}
        typed_hash = canonical_hash(value)
        typed = {**value, "content_hash": typed_hash, "upstream_receipt": {"receipt_type": "ContextReceiptV2", "receipt_id": "input-receipt:MARKET_DATA:forged", "source_stage": "MARKET_DATA", "content_type": "MarketContext", "content_hash": typed_hash}}
        typed["upstream_receipt"]["receipt_hash"] = canonical_hash(typed["upstream_receipt"])
        with self.assertRaises(PromptContractError):
            runtime._validate_semantic_entry("workflow.market_analyst", "market_snapshot", typed, {"input-receipt:MARKET_DATA:forged"})

    def test_stage_result_exact_value_binding_is_repository_owned(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        rules = agent.store.resolve_rule_set()
        context = agent.prompts.context_manifest({"run_mode": "HUNT_ONLY", "effective_rule_pack": rules.rule_set_hash}, ["run_mode", "effective_rule_pack"])
        run = agent.store.create_run(RunMode.HUNT_ONLY, rules, context["manifest_hash"], 0)
        agent.store.record_stage_result(run.run_id, None, "MARKET_ANALYSIS", None, {"answer": "repository"}, [], agent.store.dependency_hash([], rules.rule_set_hash, run.context_manifest_hash), 0)
        supplied = {"market_snapshot": agent._typed_context("MARKET_ANALYSIS", "MarketAnalysisResult", {"answer": "forged"})}
        with self.assertRaises(PromptContractError):
            agent._bind_upstream_receipts(run, None, supplied)

    def _strict_execution_agent(self, synthesis_action: str | None = None):
        agent = self._recorded_agent()
        agent.config.portfolio_provider = RecordedPortfolioProvider({"as_of": utc_now(), "cash": 1000.0, "total_equity": 1000.0, "positions": []})
        original = agent.provider
        if synthesis_action:
            def responder(request):
                payload = dict(request["default_payload"])
                if request["prompt_id"] == "workflow.final_synthesis_agent":
                    payload.update({"recommendation_status": "READY", "recommended_action": synthesis_action, "blocking_reason_codes": []})
                    if synthesis_action == "STARTER":
                        payload["starter_plan"] = self._valid_starter_plan()
                return payload
            from stock_agent.providers import FakeProvider
            agent.provider = FakeProvider(responder)
            agent.router.providers["fake"] = agent.provider
        return agent

    def _scenario(self):
        scenario = {"bull_value": 14.0, "base_value": 10.5, "bear_value": 7.0, "bull_probability": .3, "base_probability": .5, "bear_probability": .2, "opportunity_cost_score": .1, "current_price": 10.0, "evidence_ids": ["E1"], "source_stage_lineage": ["DEEP_RESEARCH", "FULL_SEC_FORENSIC", "ADVERSARIAL_AUDIT", "PORTFOLIO_REVIEW"]}
        scenario["scenario_value_hash"] = canonical_hash({"security_id": "SEC1", "evidence_ids": ["E1"], "bull_value": 14.0, "base_value": 10.5, "bear_value": 7.0, "bull_probability": .3, "base_probability": .5, "bear_probability": .2, "opportunity_cost_score": .1, "source_stage_lineage": ["ADVERSARIAL_AUDIT", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "PORTFOLIO_REVIEW"]})
        return scenario

    def test_caller_shares_and_capital_are_not_authoritative(self):
        agent = self._strict_execution_agent("STARTER")
        data = {"requested_action": "STARTER", "shares": 999999, "capital_pct": 99, "starter_plan": {"maximum_position": {"shares": 999999}}}
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, data)
        self.assertEqual(outcome.authoritative_action.value, "STARTER")
        self.assertLess(outcome.allocation["shares"], 999999)

    def test_caller_starter_cannot_override_synthesis_watch(self):
        agent = self._strict_execution_agent(None)
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, {"requested_action": "STARTER", "starter_plan": self._valid_starter_plan()})
        self.assertEqual(outcome.authoritative_action.value, "WATCH")
        self.assertEqual(outcome.allocation["shares"], 0)

    def test_synthesis_zero_starter_is_rejected(self):
        agent = self._strict_execution_agent("STARTER")
        from stock_agent.providers import FakeProvider
        def zero(request):
            payload = dict(request["default_payload"])
            if request["prompt_id"] == "workflow.final_synthesis_agent":
                payload.update({"recommendation_status": "READY", "recommended_action": "STARTER", "starter_plan": self._valid_starter_plan(), "blocking_reason_codes": []})
                payload["starter_plan"]["starter_shares"] = 0
            return payload
        agent.provider = FakeProvider(zero); agent.router.providers["fake"] = agent.provider
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, {"requested_action": "STARTER"})
        self.assertEqual(outcome.outcome, "BLOCKED_BY_CRITICAL_ISSUE")

    def test_synthesis_oversized_starter_is_rejected(self):
        agent = self._strict_execution_agent("STARTER")
        from stock_agent.providers import FakeProvider
        def oversized(request):
            payload = dict(request["default_payload"])
            if request["prompt_id"] == "workflow.final_synthesis_agent":
                plan = self._valid_starter_plan(); plan["starter_shares"] = 1_000_000
                payload.update({"recommendation_status": "READY", "recommended_action": "STARTER", "starter_plan": plan, "blocking_reason_codes": []})
            return payload
        agent.provider = FakeProvider(oversized); agent.router.providers["fake"] = agent.provider
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, {"requested_action": "STARTER"})
        self.assertEqual(outcome.outcome, "BLOCKED_BY_CRITICAL_ISSUE")

    def test_price_only_add_lineage_is_rejected(self):
        from stock_agent.gates import ContractViolation, validate_add_lineage
        with self.assertRaises(ContractViolation):
            validate_add_lineage("SEC1", {"trigger_id": "T1", "shares": 1}, {"subject_id": "SEC1", "position_exists": True}, {}, {"delta_state": "UNCHANGED"}, {})

    def test_codex_profile_routing_uses_high_and_xhigh(self):
        provider = CodexExecProvider()
        profiles = {name: ModelProfile(name, "codex", "codex-cli", reasoning_effort=("xhigh" if name in {"CRITICAL_AUDIT", "LUNA_EXTRA_HIGH"} else "high")) for name in ("BALANCED", "LUNA_HIGH", "LUNA_EXTRA_HIGH")}
        router = ModelRouter({"codex": provider}, profiles)
        self.assertEqual(router.profiles["BALANCED"].reasoning_effort, "high")
        self.assertEqual(router.profiles["LUNA_HIGH"].reasoning_effort, "high")
        self.assertEqual(router.profiles["LUNA_EXTRA_HIGH"].reasoning_effort, "xhigh")

    def test_dynamic_dependency_receipt_uses_stage_result_id(self):
        agent = self._recorded_agent()
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        call = next(call for call in agent.provider.calls if call["request"]["prompt_id"] == "workflow.stock_researcher")
        entries = {entry["id"]: entry["content"] for entry in call["request"]["context_manifest"]["entries"]}
        receipt = entries["PROMPT:capability.fundamental_change_quality"]["upstream_receipt"]
        self.assertTrue(receipt["receipt_id"].startswith("stage-result:"))
        self.assertIn(receipt["receipt_id"], call["request"]["context_manifest"]["upstream_receipt_ids"])

if __name__ == "__main__":
    unittest.main()

class CodexExecIsolationTests(unittest.TestCase):
    def test_codex_exec_forces_chatgpt_auth_and_isolated_context(self):
        captured = {}
        schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}

        def fake_run(command, **kwargs):
            captured["command"] = list(command)
            captured["cwd"] = kwargs["cwd"]
            captured["env"] = dict(kwargs["env"])
            captured["input"] = kwargs.get("input", "")
            final_path = command[command.index("--output-last-message") + 1]
            Path(final_path).write_text('{"ok":true}', encoding="utf-8")
            stdout = "\n".join([
                json.dumps({"type": "thread.started", "thread_id": "t"}),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1234, "cached_input_tokens": 1000, "output_tokens": 56, "reasoning_output_tokens": 34}}),
            ])
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        polluted = {
            "OPENAI_API_KEY": "must-not-forward",
            "CODEX_API_KEY": "must-not-forward",
            "OPENAI_ACCESS_TOKEN": "must-not-forward",
            "CODEX_ACCESS_TOKEN": "must-not-forward",
        }
        with patch.dict(os.environ, polluted), patch("stock_agent.providers.subprocess.run", side_effect=fake_run):
            payload, telemetry = CodexExecProvider(cwd=str(ROOT)).call({"prompt_body": "Return JSON.", "output_schema_definition": schema, "reasoning_effort": "xhigh"})

        self.assertEqual(payload, {"ok": True})
        command = captured["command"]
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn('web_search="disabled"', command)
        self.assertIn("features.shell_tool=false", command)
        self.assertIn("features.unified_exec=false", command)
        self.assertIn("agents.enabled=false", command)
        self.assertIn('model_reasoning_effort="xhigh"', command)
        self.assertEqual(command[-1], "-")
        self.assertNotIn("Return JSON.", command)
        self.assertIn("Return JSON.", captured.get("input", ""))
        self.assertNotEqual(Path(captured["cwd"]).resolve(), ROOT.resolve())
        for key in polluted:
            self.assertNotIn(key, captured["env"])
        self.assertEqual(telemetry["input_tokens"], 1234)
        self.assertEqual(telemetry["cached_tokens"], 1000)
        self.assertEqual(telemetry["output_tokens"], 56)
        self.assertEqual(telemetry["reasoning_output_tokens"], 34)
        self.assertEqual(telemetry["usage_source"], "codex_jsonl")
        self.assertEqual(telemetry["billing_source"], "chatgpt_codex_credits")

class CodexLedgerTelemetryTests(unittest.TestCase):
    def test_codex_usage_metadata_persists_in_sqlite_ledger(self):
        store = SQLiteStore(":memory:")
        rules = EffectiveRuleSet()
        run = store.create_run(RunMode.HUNT_ONLY, rules, canonical_hash({"ctx": 1}), 0)
        item = store.enqueue(run, "MARKET_ANALYSIS", {}, canonical_hash({"dep": 1}))
        telemetry = {
            "provider": "codex_exec", "model": "codex-cli", "reasoning_effort": "high",
            "wire_api": "codex_exec", "endpoint": "local-codex-cli", "router_profile": "LUNA_HIGH",
            "input_tokens": 1234, "cached_tokens": 1000, "output_tokens": 56,
            "reasoning_output_tokens": 34, "latency_ms": 12.0, "finish_reason": "stop",
            "actual_cost": 0.0, "billing_source": "chatgpt_codex_credits", "usage_source": "codex_jsonl",
        }
        rid = store.reserve_cost(run.run_id, item.work_item_id, "workflow.market_analyst", "codex", "codex-cli", 0.0, "high", "codex_exec")
        store.settle_cost(rid, telemetry, 0)
        store.record_model_call(run.run_id, item.work_item_id, "workflow.market_analyst", telemetry, 0)
        call = store.connection.execute("SELECT * FROM model_calls").fetchone()
        reservation = store.connection.execute("SELECT * FROM cost_reservations").fetchone()
        self.assertEqual(call["billing_source"], "chatgpt_codex_credits")
        self.assertEqual(call["usage_source"], "codex_jsonl")
        self.assertEqual(call["reasoning_output_tokens"], 34)
        self.assertEqual(reservation["billing_source"], "chatgpt_codex_credits")
        self.assertEqual(reservation["usage_source"], "codex_jsonl")
        self.assertEqual(reservation["reasoning_output_tokens"], 34)
        store.close()


