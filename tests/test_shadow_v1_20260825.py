from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from stock_agent.adapters import RecordedMarketDataProvider, RecordedPortfolioProvider, RecordedResearchEvidenceProvider, RecordedSECProvider
from stock_agent.models import EffectiveRuleSet, Evidence, RunMode, canonical_hash
from stock_agent.paths import canonical_prompt_library_root
from stock_agent.providers import FakeProvider, ModelProfile, ModelRouter, OpenAIResponsesProvider, ProviderRequestError
from stock_agent.runtime import ProductionStockAgent, StockAgent, StockAgentConfig, _compact_model_universe_rows
from stock_agent.shadow import DailyShadowRunner, LunaHealthChecker, OutcomeTracker, persist_outcomes, reproducibility_metadata, validate_report_provenance
from stock_agent.daily_orchestrator import PrimaryV8DailyOrchestrator
from stock_agent.store import SQLiteStore


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "strict_provider_recorded_input.json"
LIBRARY = canonical_prompt_library_root()


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.url = "https://api.openai.com/v1/responses"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return json.dumps(self.payload).encode("utf-8")


class LunaResponsesProviderTests(unittest.TestCase):
    schema = {
        "type": "object", "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False,
    }

    def _response(self, text='{"ok":true}'):
        return {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {
                "input_tokens": 10, "output_tokens": 3,
                "input_tokens_details": {"cached_tokens": 4},
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        }

    def test_responses_api_structured_output_and_usage(self):
        captured = {}

        def fake_open(request, timeout=0):
            captured["body"] = json.loads(request.data)
            captured["authorization_present"] = bool(request.headers.get("Authorization"))
            return _Response(self._response())

        provider = OpenAIResponsesProvider("secret-value", reasoning_effort="medium")
        with patch("urllib.request.urlopen", side_effect=fake_open):
            payload, telemetry = provider.call({"prompt_id": "workflow.market_analyst", "prompt_body": "policy", "output_schema_definition": self.schema})
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(captured["body"]["model"], "gpt-5.6-luna")
        self.assertEqual(captured["body"]["reasoning"], {"effort": "medium"})
        self.assertEqual(captured["body"]["text"]["format"]["type"], "json_schema")
        self.assertFalse(captured["body"]["store"])
        self.assertTrue(captured["authorization_present"])
        self.assertEqual(telemetry["cached_tokens"], 4)
        self.assertEqual(telemetry["reasoning_output_tokens"], 2)
        self.assertNotIn("secret-value", json.dumps(provider.calls))

    def test_responses_wire_schema_fills_required_properties_without_mutating_canonical(self):
        schema = {"type": "object", "properties": {"required_value": {"type": "string"}, "optional_value": {"type": ["string", "null"]}}, "required": ["required_value"], "additionalProperties": False}
        provider = OpenAIResponsesProvider("secret-value")
        wire = provider._strict_responses_schema(schema)
        self.assertEqual(wire["required"], ["optional_value", "required_value"])
        self.assertEqual(schema["required"], ["required_value"])

    def test_malformed_and_schema_invalid_response_fail_closed(self):
        provider = OpenAIResponsesProvider("secret-value")
        with patch("urllib.request.urlopen", return_value=_Response(self._response("not-json"))):
            with self.assertRaises(ProviderRequestError):
                provider.call({"prompt_body": "policy", "output_schema_definition": self.schema})
        with patch("urllib.request.urlopen", return_value=_Response(self._response('{"ok":"yes"}'))):
            with self.assertRaises(ProviderRequestError):
                provider.call({"prompt_body": "policy", "output_schema_definition": self.schema})

    def test_timeout_and_invalid_key_are_sanitized(self):
        provider = OpenAIResponsesProvider("do-not-leak")
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout with do-not-leak")):
            with self.assertRaises(ProviderRequestError) as raised:
                provider.call({"prompt_body": "policy", "output_schema_definition": self.schema})
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("do-not-leak", str(raised.exception))
        error = urllib.error.HTTPError(provider.endpoint, 401, "bad key do-not-leak", {}, io.BytesIO(b'{"error":{"message":"do-not-leak"}}'))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ProviderRequestError) as raised:
                provider.call({"prompt_body": "policy", "output_schema_definition": self.schema})
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn("do-not-leak", str(raised.exception))

    def test_429_uses_bounded_exponential_router_retry(self):
        class Flaky:
            calls = 0
            def call(self, request):
                self.calls += 1
                if self.calls < 3:
                    raise ProviderRequestError("HTTP 429", retryable=True, status_code=429)
                return {"ok": True}, {"input_tokens": 1, "output_tokens": 1}
        provider = Flaky()
        profile = ModelProfile("BALANCED", "luna", "gpt-5.6-luna", 2, "medium", "responses", 0.1)
        with patch("stock_agent.providers.time.sleep") as sleeper:
            payload, telemetry = ModelRouter({"luna": provider}, {"BALANCED": profile}).call("BALANCED", {})
        self.assertTrue(payload["ok"])
        self.assertEqual(provider.calls, 3)
        self.assertEqual([call.args[0] for call in sleeper.call_args_list], [0.1, 0.2])
        self.assertEqual(telemetry["retry_count"], 2)

    def test_nonretryable_auth_error_does_not_retry(self):
        class Invalid:
            calls = 0
            def call(self, request):
                self.calls += 1
                raise ProviderRequestError("HTTP 401", retryable=False, status_code=401)
        provider = Invalid()
        with self.assertRaises(ProviderRequestError):
            ModelRouter({"luna": provider}, {"BALANCED": ModelProfile("BALANCED", "luna", "gpt-5.6-luna", 3)}).call("BALANCED", {})
        self.assertEqual(provider.calls, 1)


class ShadowV1Tests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def make_agent(self):
        recordings = copy.deepcopy(self.data["provider_recordings"])
        recordings["portfolio_snapshot"]["as_of"] = recordings["_recorded_at"]
        scenario = {
            "security_id": "SEC1", "bull_value": 14.0, "base_value": 10.5, "bear_value": 7.0,
            "bull_probability": 0.3, "base_probability": 0.5, "bear_probability": 0.2,
            "opportunity_cost_score": 0.1, "evidence_ids": ["E1"],
            "source_stage_lineage": ["ADVERSARIAL_AUDIT", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "PORTFOLIO_REVIEW"],
        }
        scenario["scenario_value_hash"] = canonical_hash(scenario)
        recordings["research"]["SEC1"]["economic_scenario"] = scenario
        provider = FakeProvider()
        config = StockAgentConfig(
            LIBRARY, Path(":memory:"), strict_inputs=True,
            market_data_provider=RecordedMarketDataProvider(recordings),
            sec_provider=RecordedSECProvider(recordings["sec"]),
            portfolio_provider=RecordedPortfolioProvider(recordings["portfolio_snapshot"]),
            research_provider=RecordedResearchEvidenceProvider(recordings["research"]),
        )
        return ProductionStockAgent(config, store=SQLiteStore(":memory:"), provider=provider)

    def test_live_model_universe_context_is_compact_and_keeps_python_timeseries_private(self):
        rows = [{
            "security_id": "MID1", "ticker": "MID", "price": 12.5,
            "market_cap": 1_000_000_000, "average_dollar_volume": 25_000_000,
            "prices": [12.0] * 100, "volumes": [1_000_000] * 100,
            "candles": [{"close": 12.0, "volume": 1_000_000}] * 100,
        }]
        compact = _compact_model_universe_rows(rows)
        self.assertEqual(compact[0]["security_id"], "MID1")
        self.assertEqual(compact[0]["average_dollar_volume"], 25_000_000)
        self.assertNotIn("prices", compact[0])
        self.assertNotIn("volumes", compact[0])
        self.assertNotIn("candles", compact[0])

    @staticmethod
    def metadata():
        return {
            "code_git_sha": "a" * 40, "branch": "test", "ruleset_hash": "rules",
            "prompt_library_hash": "prompt", "config_hash": "config", "model": "recorded",
            "provider": "recorded", "reasoning_effort": {"BALANCED": "medium"},
            "schema_version": "shadow-log-v1", "database_schema_version": "shadow-v1",
            "timezone": "Asia/Seoul", "broker_write_count": 0,
        }

    def test_complete_daily_run_creates_immutable_logs_and_zero_broker_writes(self):
        agent = self.make_agent()
        with tempfile.TemporaryDirectory() as temp:
            runner = DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: {"status": "PASS"})
            result = runner.run({}, run_date="2026-08-25")
            self.assertEqual(result.status, "SUCCEEDED")
            self.assertEqual(result.broker_write_count, 0)
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) n FROM final_actions WHERE run_id=?", (result.hunt_run_id,)).fetchone()["n"], 0)
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) n FROM work_items WHERE run_id=?", (result.hunt_run_id,)).fetchone()["n"], 11)
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) n FROM work_items WHERE run_id=?", (result.execution_run_id,)).fetchone()["n"], 18)
            self.assertEqual(set(result.artifact_paths), {"DAILY_REPORT", "RUN_LOG", "DECISIONS", "INCIDENTS", "EVIDENCE_MANIFEST"})
            run_log = json.loads(Path(result.artifact_paths["RUN_LOG"]).read_text(encoding="utf-8"))
            self.assertEqual(run_log["broker_write_count"], 0)
            decisions = agent.store.list_shadow_decisions(result.shadow_run_id)
            self.assertTrue(decisions)
            self.assertIn("evidence_ids", decisions[0])
            self.assertIsNone(decisions[0]["grade"])
            manifest = [json.loads(line) for line in Path(result.artifact_paths["EVIDENCE_MANIFEST"]).read_text(encoding="utf-8").splitlines()]
            self.assertTrue(manifest)
            self.assertTrue(all(row["lineage_valid"] for row in manifest))
        agent.close()

    def test_duplicate_same_date_run_ids_are_monotonic(self):
        agent = self.make_agent()
        with tempfile.TemporaryDirectory() as temp:
            runner = DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: {"status": "PASS"})
            first = runner.run({}, run_date="2026-08-25")
            second = runner.run({}, run_date="2026-08-25")
            self.assertEqual(first.shadow_run_id, "RUN-20260825-001")
            self.assertEqual(second.shadow_run_id, "RUN-20260825-002")
        agent.close()

    def test_completed_run_resume_is_idempotent(self):
        agent = self.make_agent()
        health_calls = []
        with tempfile.TemporaryDirectory() as temp:
            runner = DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: health_calls.append(True) or {"status": "PASS"})
            first = runner.run({}, run_date="2026-08-25")
            second = runner.run({}, run_date="2026-08-25", resume_run_id=first.shadow_run_id)
            self.assertEqual(second, first)
            self.assertEqual(len(health_calls), 1)
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) n FROM shadow_decisions").fetchone()["n"], 1)
        agent.close()

    def test_crash_after_hunt_can_resume_without_reusing_other_run_receipts(self):
        agent = self.make_agent()
        with tempfile.TemporaryDirectory() as temp:
            runner = DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: {"status": "PASS"})
            with self.assertRaises(InterruptedError):
                runner.run({}, run_date="2026-08-25", stop_after_hunt=True)
            saved = agent.store.get_shadow_run("RUN-20260825-001")
            hunt_run_id = saved["hunt_run_id"]
            resumed = runner.run({}, run_date="2026-08-25", resume_run_id="RUN-20260825-001")
            self.assertEqual(resumed.hunt_run_id, hunt_run_id)
            self.assertNotEqual(resumed.execution_run_id, hunt_run_id)
            self.assertEqual(resumed.status, "SUCCEEDED")
        agent.close()

    def test_provider_failure_is_explicit_and_retryable_by_run_id(self):
        agent = self.make_agent()
        with tempfile.TemporaryDirectory() as temp:
            runner = DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: (_ for _ in ()).throw(RuntimeError("unavailable")))
            with self.assertRaises(RuntimeError):
                runner.run({}, run_date="2026-08-25")
            saved = agent.store.get_shadow_run("RUN-20260825-001")
            self.assertEqual(saved["status"], "FAILED")
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) n FROM shadow_incidents").fetchone()["n"], 1)
        agent.close()

    def test_decision_and_replay_are_immutable(self):
        store = SQLiteStore(":memory:")
        first = store.reserve_shadow_run("2026-08-25", "SHADOW_V1.0", {})
        decision = {"decision_id": "D1", "ticker": "ABC", "decision": "WATCH"}
        store.append_shadow_decision(first["shadow_run_id"], decision)
        with self.assertRaises(ValueError):
            store.append_shadow_decision(first["shadow_run_id"], {**decision, "decision": "QUALIFIED"})
        replay = store.reserve_shadow_run("2026-08-25", "SHADOW_V1.1", {}, original_shadow_run_id=first["shadow_run_id"])
        self.assertEqual(replay["original_shadow_run_id"], first["shadow_run_id"])
        self.assertEqual(store.list_shadow_decisions(first["shadow_run_id"])[0]["decision"], "WATCH")
        store.close()

    def test_reproducibility_metadata_redacts_secret_values(self):
        metadata = reproducibility_metadata(ROOT, LIBRARY, model="gpt-5.6-luna", provider="luna", reasoning_effort={"BALANCED": "medium"}, config_values={"OPENAI_API_KEY": "secret", "market_provider": "live"})
        self.assertNotIn("secret", json.dumps(metadata))
        self.assertIn("git_dirty", metadata)
        self.assertIn("git_status_hash", metadata)
        self.assertIn("git_diff_hash", metadata)
        self.assertIn("source_tree_hash", metadata)
        self.assertIn(metadata["source_provenance_status"], {"CLEAN_COMMITTED", "DIRTY_WORKTREE", "UNKNOWN"})

    def test_daily_report_provenance_must_match_run_log(self):
        log = {"code_git_sha": "a" * 40, "git_diff_hash": "diff-hash", "source_tree_hash": "tree-hash"}
        report = "Git SHA: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nGit Diff Hash: `diff-hash`\nSource Tree Hash: `tree-hash`\n"
        validate_report_provenance(log, report)
        with self.assertRaisesRegex(RuntimeError, "REPORT_PROVENANCE_MISMATCH"):
            validate_report_provenance(log, report.replace("tree-hash", "other-tree"))

    def test_luna_health_uses_real_stock_agent_stage_schema(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        class HealthProvider:
            reasoning_effort = "medium"
            def call(inner_self, request):
                self.assertEqual(request["prompt_id"], "workflow.market_analyst")
                self.assertIn("$defs", request["output_schema_definition"])
                return agent._valid_payload("MarketContextExecutionAssessmentV2"), {"model": "gpt-5.6-luna", "latency_ms": 1, "usage_source": "test"}
        result = LunaHealthChecker(HealthProvider(), agent.prompts).check()
        self.assertEqual(result["status"], "PASS")
        agent.close()

    def test_watch_and_rejected_decisions_are_retained(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        run = agent.store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "context", 0)
        for subject, decision in (("WATCH1", "PASS"), ("REJECT1", "REJECT")):
            agent.store.record_stage_result(run.run_id, None, "STAGE_GATE", subject, {"decision": decision}, [], canonical_hash([]), 0)
        agent.store.finish_run(run.run_id, "NO_QUALIFIED_CANDIDATE")
        shadow = agent.store.reserve_shadow_run("2026-08-25", "SHADOW_V1.0", {})
        with tempfile.TemporaryDirectory() as temp:
            runner = DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: {"status": "PASS"})
            decisions = runner._decisions(shadow["shadow_run_id"], run.run_id, None)
        by_ticker = {row["ticker"]: row for row in decisions}
        self.assertEqual(by_ticker["WATCH1"]["decision"], "WATCH")
        self.assertEqual(by_ticker["REJECT1"]["decision"], "REJECTED_DISCOVERY")
        agent.close()

    def test_future_evidence_cannot_enter_point_in_time_manifest(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        run = agent.store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "context", 0)
        shadow = agent.store.reserve_shadow_run("2026-08-25", "SHADOW_V1.0", {})
        agent.store.update_shadow_run(shadow["shadow_run_id"], hunt_run_id=run.run_id)
        decision_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        agent.store.append_shadow_decision(shadow["shadow_run_id"], {"decision_id": "D-FUTURE", "ticker": "ABC", "decision_time": decision_time})
        future = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        agent.store.upsert_evidence(Evidence("E-FUTURE", "ABC", "DERIVED", future, 1, canonical_hash({"future": True}), "A"))
        agent.store.record_stage_result(run.run_id, None, "STAGE_GATE", "ABC", {"decision": "PASS"}, ["E-FUTURE"], agent.store.dependency_hash(["E-FUTURE"], run.rule_set.rule_set_hash, run.context_manifest_hash), 1)
        with tempfile.TemporaryDirectory() as temp:
            runner = DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: {"status": "PASS"})
            with self.assertRaisesRegex(RuntimeError, "future Evidence"):
                runner._evidence_manifest([run.run_id])
        agent.close()

    def test_evidence_after_run_start_before_decision_is_valid(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        run = agent.store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "context", 0)
        started = datetime.fromisoformat(str(agent.store.get_run(run.run_id).created_at).replace("Z", "+00:00"))
        shadow = agent.store.reserve_shadow_run("2026-08-25", "SHADOW_V1.0", {})
        agent.store.update_shadow_run(shadow["shadow_run_id"], hunt_run_id=run.run_id)
        evidence_at = (started + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        decision_at = (started + timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
        agent.store.append_shadow_decision(shadow["shadow_run_id"], {"decision_id": "D-BEFORE", "ticker": "ABC", "decision_time": decision_at})
        payload_hash = canonical_hash({"source": "between-run-and-decision"})
        agent.store.upsert_evidence(Evidence("E-BEFORE", "ABC", "DERIVED", evidence_at, 1, payload_hash, "A"))
        agent.store.record_stage_result(run.run_id, None, "STAGE_GATE", "ABC", {"decision": "PASS"}, ["E-BEFORE"], agent.store.dependency_hash(["E-BEFORE"], run.rule_set.rule_set_hash, run.context_manifest_hash), 1)
        runner = DailyShadowRunner(agent, tempfile.mkdtemp(), self.metadata(), provider_health=lambda: {"status": "PASS"})
        manifest = runner._evidence_manifest([run.run_id])
        self.assertEqual([row["evidence_id"] for row in manifest], ["E-BEFORE"])
        agent.close()


class PrimaryV8DailyOrchestratorTests(unittest.TestCase):
    """End-to-end orchestration tests use recorded providers only as fixtures."""

    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def make_agent(self, *, no_catalyst: bool = False):
        recordings = copy.deepcopy(self.data["provider_recordings"])
        recordings["portfolio_snapshot"]["as_of"] = recordings["_recorded_at"]
        scenario = {
            "security_id": "SEC1", "bull_value": 14.0, "base_value": 10.5, "bear_value": 7.0,
            "bull_probability": 0.3, "base_probability": 0.5, "bear_probability": 0.2,
            "opportunity_cost_score": 0.1, "evidence_ids": ["E1"],
            "source_stage_lineage": ["ADVERSARIAL_AUDIT", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "PORTFOLIO_REVIEW"],
        }
        scenario["scenario_value_hash"] = canonical_hash(scenario)
        recordings["research"]["SEC1"]["economic_scenario"] = scenario
        if no_catalyst:
            recordings["research"]["SEC1"]["catalysts"] = []
        config = StockAgentConfig(
            LIBRARY, Path(":memory:"), strict_inputs=True,
            market_data_provider=RecordedMarketDataProvider(recordings),
            sec_provider=RecordedSECProvider(recordings["sec"]),
            portfolio_provider=RecordedPortfolioProvider(recordings["portfolio_snapshot"]),
            research_provider=RecordedResearchEvidenceProvider(recordings["research"]),
        )
        return ProductionStockAgent(config, store=SQLiteStore(":memory:"), provider=FakeProvider())

    @staticmethod
    def metadata():
        return {
            "code_git_sha": "a" * 40, "branch": "test", "ruleset_hash": "rules",
            "prompt_library_hash": "prompt", "config_hash": "config", "model": "recorded",
            "provider": "recorded", "reasoning_effort": {"BALANCED": "medium"},
            "schema_version": "shadow-log-v1", "database_schema_version": "shadow-v1",
            "timezone": "Asia/Seoul", "broker_write_count": 0,
        }

    @staticmethod
    def bundle(root: Path) -> Path:
        stages = ["00A", "01", *[f"{n:02d}" for n in range(2, 19)]]
        root.mkdir(parents=True, exist_ok=True)
        for stage in stages:
            (root / f"{stage}_prompt.md").write_text(f"V8 stage {stage}", encoding="utf-8")
        return root

    @staticmethod
    def executor(stage, prompt, context):
        if stage == "18":
            return {"ticker": "SEC1", "score": 85, "score_start": 0, "hard_gate_pass": True, "category": "A_CERTIFIED"}
        return {"status": "OK", "stage_observed": stage}

    def test_one_command_generates_primary_export_v8_comparison_and_combined_report(self):
        agent = self.make_agent()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = self.bundle(root / "bundle")
            orchestrator = PrimaryV8DailyOrchestrator(
                agent, root / "runs", self.metadata(), prompt_bundle=bundle,
                preflight=lambda: {"status": "PASS"}, v8_executor=self.executor,
            )
            result = orchestrator.run(self.data, run_date="2026-08-25", run_id="DAILY-TEST-001")
            self.assertEqual(result.status, "SUCCEEDED")
            self.assertEqual(result.primary_status, "SUCCEEDED")
            self.assertEqual(result.export_status, "PASS")
            self.assertEqual(result.v8_status, "SUCCEEDED")
            self.assertEqual(result.broker_write_count, 0)
            run_root = root / "runs" / "2026-08-25" / "DAILY-TEST-001"
            for relative in (
                "PRIMARY/DAILY_REPORT.md", "PRIMARY/RUN_LOG.json", "EXPORT/CHALLENGER_INPUT_MANIFEST.json",
                "EXPORT/PRIMARY_CANDIDATES.json", "EXPORT/PRIMARY_EVIDENCE.json", "V8/V8_REPORT.md",
                "PRIMARY_VS_V8_COMPARISON.json", "DAILY_COMBINED_REPORT.md", "RUN_LOG.json",
            ):
                self.assertTrue((run_root / relative).exists(), relative)
            top_log = json.loads((run_root / "RUN_LOG.json").read_text(encoding="utf-8"))
            self.assertEqual(top_log["broker_write_count"], 0)
            comparison = json.loads((run_root / "PRIMARY_VS_V8_COMPARISON.json").read_text(encoding="utf-8"))
            self.assertEqual(comparison["comparison_as_of"], json.loads((run_root / "EXPORT/CHALLENGER_INPUT_MANIFEST.json").read_text(encoding="utf-8"))["comparison_as_of"])
        agent.close()

    def test_preflight_failure_still_generates_failure_report_and_never_runs_primary_or_v8(self):
        agent = self.make_agent()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            orchestrator = PrimaryV8DailyOrchestrator(agent, root / "runs", self.metadata(), prompt_bundle=self.bundle(root / "bundle"), preflight=lambda: {"status": "FAILED"}, v8_executor=self.executor)
            result = orchestrator.run(self.data, run_date="2026-08-25", run_id="DAILY-FAIL-001")
            self.assertEqual(result.status, "FAILED")
            self.assertEqual(result.primary_status, "NOT_RUN")
            self.assertEqual(result.v8_status, "NOT_RUN")
            report = root / "runs" / "2026-08-25" / "DAILY-FAIL-001" / "DAILY_COMBINED_REPORT.md"
            self.assertTrue(report.exists())
            self.assertIn("PREFLIGHT_FAILED", report.read_text(encoding="utf-8"))
        agent.close()

    def test_v8_failure_preserves_primary_report_and_combined_report(self):
        agent = self.make_agent()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def failing(stage, prompt, context):
                raise RuntimeError("challenger timeout")
            orchestrator = PrimaryV8DailyOrchestrator(agent, root / "runs", self.metadata(), prompt_bundle=self.bundle(root / "bundle"), preflight=lambda: {"status": "PASS"}, v8_executor=failing)
            result = orchestrator.run(self.data, run_date="2026-08-25", run_id="DAILY-V8-FAIL")
            self.assertEqual(result.primary_status, "SUCCEEDED")
            self.assertEqual(result.v8_status, "FAILED")
            run_root = root / "runs" / "2026-08-25" / "DAILY-V8-FAIL"
            self.assertTrue((run_root / "PRIMARY/DAILY_REPORT.md").exists())
            self.assertTrue((run_root / "V8/V8_REPORT.md").exists())
            self.assertTrue((run_root / "DAILY_COMBINED_REPORT.md").exists())
        agent.close()

    def test_normal_no_candidate_run_does_not_fail_hunt_contract_or_start_v8(self):
        agent = self.make_agent(no_catalyst=True)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            orchestrator = PrimaryV8DailyOrchestrator(
                agent, root / "runs", self.metadata(), prompt_bundle=self.bundle(root / "bundle"),
                preflight=lambda: {"status": "PASS"}, v8_executor=self.executor,
            )
            result = orchestrator.run(self.data, run_date="2026-08-25", run_id="DAILY-NO-CANDIDATE")
            self.assertEqual(result.status, "SUCCEEDED")
            self.assertEqual(result.primary_status, "SUCCEEDED")
            self.assertEqual(result.export_status, "NOT_APPLICABLE_NO_CANDIDATE")
            self.assertEqual(result.v8_status, "NOT_RUN")
            self.assertEqual(result.comparison_status, "NOT_RUN")
            run_root = root / "runs" / "2026-08-25" / "DAILY-NO-CANDIDATE"
            self.assertTrue((run_root / "PRIMARY/DAILY_REPORT.md").exists())
            self.assertTrue((run_root / "DAILY_COMBINED_REPORT.md").exists())
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) n FROM final_actions").fetchone()["n"], 0)
            decisions = agent.store.list_shadow_decisions(result.primary.shadow_run_id)
            self.assertTrue(any(row["decision"] == "NOT_EVALUATED_CATALYST" for row in decisions))
        agent.close()


    def test_provider_failure_is_not_evaluated_and_emits_incident(self):
        agent = self.make_agent(no_catalyst=True)
        with tempfile.TemporaryDirectory() as temp:
            runner = DailyShadowRunner(agent, Path(temp), self.metadata(), provider_health=lambda: {"status": "PASS"})
            shadow = agent.store.reserve_shadow_run("2026-08-25", "SHADOW_V1.1", self.metadata())
            hunt = agent.store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "ctx", agent.store.current_evidence_epoch())
            agent.store.record_stage_result(
                hunt.run_id, None, "RESEARCH_PROVIDER_FAILURE", "SEC1",
                {"status": "FAILED", "reason": "provider timeout", "security_id": "SEC1"},
                [], agent.store.dependency_hash([], hunt.rule_set.rule_set_hash, hunt.context_manifest_hash),
                agent.store.current_evidence_epoch_for([]),
            )
            agent.store.update_shadow_run(shadow["shadow_run_id"], hunt_run_id=hunt.run_id)
            decisions = runner._decisions(shadow["shadow_run_id"], hunt.run_id, None)
            self.assertEqual(decisions[0]["decision"], "NOT_EVALUATED_RESEARCH_PROVIDER")
            self.assertFalse(decisions[0]["rejected"])
            self.assertFalse(decisions[0]["watch"])
            self.assertTrue(decisions[0]["not_evaluated"])
            incidents = agent.store.connection.execute("SELECT COUNT(*) n FROM shadow_incidents WHERE shadow_run_id=?", (shadow["shadow_run_id"],)).fetchone()["n"]
            self.assertEqual(incidents, 1)
        agent.close()

    def test_resume_uses_existing_orchestrator_log_without_duplicate_primary(self):
        agent = self.make_agent()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            orchestrator = PrimaryV8DailyOrchestrator(agent, root / "runs", self.metadata(), prompt_bundle=self.bundle(root / "bundle"), preflight=lambda: {"status": "PASS"}, v8_executor=self.executor)
            first = orchestrator.run(self.data, run_date="2026-08-25", run_id="DAILY-RESUME")
            second = orchestrator.run(self.data, run_date="2026-08-25", resume_run_id="DAILY-RESUME")
            self.assertEqual(first.run_id, second.run_id)
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) n FROM shadow_runs").fetchone()["n"], 1)
        agent.close()


class ShadowOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.decision = {
            "decision_id": "D1", "ticker": "ABC", "decision_time": "2026-08-21T20:00:00Z",
            "decision_price": 100.0, "recommended_entry": 99.0, "stop": 90.0, "target": 120.0,
        }

    def bars(self, count=20):
        start = datetime(2026, 8, 24, tzinfo=timezone.utc)
        return [{"date": (start + timedelta(days=index)).date().isoformat(), "open": 100 + index, "high": 101 + index, "low": 98 + index, "close": 100 + index} for index in range(count)]

    def test_horizons_use_observed_trading_rows_and_calculate_mfe_mae(self):
        values = OutcomeTracker.calculate(self.decision, self.bars())
        self.assertEqual(set(values), {"1D", "3D", "5D", "10D", "20D"})
        self.assertAlmostEqual(values["5D"]["forward_return"], 0.04)
        self.assertAlmostEqual(values["5D"]["mfe"], 0.05)
        self.assertAlmostEqual(values["5D"]["mae"], -0.02)

    def test_horizon_counts_market_sessions_not_calendar_days(self):
        bars = [
            {"date": "2026-08-24", "high": 101, "low": 99, "close": 100},
            {"date": "2026-08-25", "high": 102, "low": 99, "close": 101},
            {"date": "2026-08-28", "high": 103, "low": 100, "close": 102},
            {"date": "2026-08-31", "high": 104, "low": 101, "close": 103},
            {"date": "2026-09-01", "high": 105, "low": 102, "close": 104},
        ]
        values = OutcomeTracker.calculate(self.decision, bars)
        self.assertEqual(values["5D"]["terminal_date"], "2026-09-01")

    def test_unfilled_stop_before_target_and_target_before_stop(self):
        no_fill = [{"date": "2026-08-24", "open": 110, "high": 115, "low": 105, "close": 112}]
        self.assertEqual(OutcomeTracker.shadow_lifecycle(self.decision, no_fill)["status"], "NOT_FILLED")
        stopped = [{"date": "2026-08-24", "open": 100, "high": 101, "low": 98, "close": 99}, {"date": "2026-08-25", "open": 95, "high": 96, "low": 89, "close": 90}]
        self.assertEqual(OutcomeTracker.shadow_lifecycle(self.decision, stopped)["status"], "STOPPED")
        target = [{"date": "2026-08-24", "open": 100, "high": 101, "low": 98, "close": 99}, {"date": "2026-08-25", "open": 110, "high": 121, "low": 105, "close": 120}]
        self.assertEqual(OutcomeTracker.shadow_lifecycle(self.decision, target)["status"], "TARGET_HIT")

    def test_persisted_outcomes_do_not_mutate_original_decision(self):
        store = SQLiteStore(":memory:")
        run = store.reserve_shadow_run("2026-08-25", "SHADOW_V1.0", {})
        store.append_shadow_decision(run["shadow_run_id"], self.decision)
        ids = persist_outcomes(store, self.decision, self.bars(), as_of="2026-09-25")
        self.assertGreaterEqual(len(ids), 6)
        original = store.list_shadow_decisions(run["shadow_run_id"])[0]
        self.assertEqual(original["decision_price"], 100.0)
        with self.assertRaises(ValueError):
            store.append_shadow_outcome("D1", "1D", "2026-09-25", {"forward_return": 999})
        store.close()

    def test_outcome_rejects_bar_after_explicit_as_of(self):
        bars = self.bars(2) + [{"date": "2026-09-30", "high": 130, "low": 95, "close": 125}]
        with self.assertRaises(OutcomeTracker.PITViolation):
            OutcomeTracker.calculate(self.decision, bars, as_of="2026-08-25")

    def test_mfe_mae_cannot_use_bar_after_cutoff(self):
        bars = [{"date": "2026-08-24", "high": 101, "low": 99, "close": 100}]
        future = {"date": "2026-08-26", "high": 200, "low": 1, "close": 150}
        with self.assertRaises(OutcomeTracker.PITViolation):
            OutcomeTracker.calculate(self.decision, bars + [future], as_of="2026-08-25")

    def test_lifecycle_cannot_use_future_target_or_stop_hit(self):
        pre_cutoff = {"date": "2026-08-24", "high": 101, "low": 98, "close": 99}
        future_target = {"date": "2026-08-26", "high": 121, "low": 105, "close": 120}
        with self.assertRaises(OutcomeTracker.PITViolation):
            OutcomeTracker.shadow_lifecycle(self.decision, [pre_cutoff, future_target], as_of="2026-08-25")

    def test_lifecycle_cannot_use_future_stop_hit(self):
        pre_cutoff = {"date": "2026-08-24", "high": 101, "low": 98, "close": 99}
        future_stop = {"date": "2026-08-26", "high": 95, "low": 80, "close": 85}
        with self.assertRaises(OutcomeTracker.PITViolation):
            OutcomeTracker.shadow_lifecycle(self.decision, [pre_cutoff, future_stop], as_of="2026-08-25")

    def test_valid_historical_bars_with_cutoff_still_calculate(self):
        values = OutcomeTracker.calculate(self.decision, self.bars(), as_of="2026-09-25")
        self.assertEqual(set(values), {"1D", "3D", "5D", "10D", "20D"})

    def test_persist_rejects_future_bar_before_any_outcome_append(self):
        store = SQLiteStore(":memory:")
        shadow = store.reserve_shadow_run("2026-08-25", "SHADOW_V1.0", {})
        store.append_shadow_decision(shadow["shadow_run_id"], self.decision)
        with self.assertRaises(OutcomeTracker.PITViolation):
            persist_outcomes(store, self.decision, self.bars(2) + [{"date": "2026-09-30", "high": 200, "low": 1, "close": 150}], as_of="2026-08-25")
        count = store.connection.execute("SELECT COUNT(*) AS n FROM shadow_outcomes").fetchone()["n"]
        self.assertEqual(count, 0)
        store.close()


if __name__ == "__main__":
    unittest.main()
