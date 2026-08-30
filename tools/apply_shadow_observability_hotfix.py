from pathlib import Path

runtime = Path("stock_agent/runtime.py")
text = runtime.read_text(encoding="utf-8")

old = '''                stage = self.stage_gate.evaluate(feature_stage, feature_eligible, rules)\n                self.store.record_stage_result(run.run_id, None, "STAGE_GATE", sid, stage.as_dict(), evidence_ids, self.store.dependency_hash(evidence_ids, rules.rule_set_hash, run.context_manifest_hash), self.store.current_evidence_epoch_for(evidence_ids))\n'''
new = '''                stage = self.stage_gate.evaluate(feature_stage, feature_eligible, rules)\n                stage_result = {**stage.as_dict(), "proposed_stage": feature_stage}\n                self.store.record_stage_result(run.run_id, None, "STAGE_GATE", sid, stage_result, evidence_ids, self.store.dependency_hash(evidence_ids, rules.rule_set_hash, run.context_manifest_hash), self.store.current_evidence_epoch_for(evidence_ids))\n'''
if old not in text:
    raise SystemExit("STAGE_GATE persistence target not found")
text = text.replace(old, new, 1)

old = '''                    failure = {"security_id": sid, "error": reason}\n                    stage_name = "SEC_STALE_DATA" if stale_sec else "SEC_PROVIDER_FAILURE"\n'''
new = '''                    failure_provider = str(getattr(sec_provider, "provider_name", sec_provider.__class__.__name__))\n                    failure = {"security_id": sid, "error": reason, "provider": failure_provider}\n                    stage_name = "SEC_STALE_DATA" if stale_sec else "SEC_PROVIDER_FAILURE"\n'''
if old not in text:
    raise SystemExit("SEC failure attribution target not found")
text = text.replace(old, new, 1)

old = '''                        {"status": "NOT_EVALUATED" if stale_sec else "FAILED", "decision": "INSUFFICIENT_EVIDENCE" if stale_sec else "FAILED", "reason": reason, "security_id": sid},\n'''
new = '''                        {"status": "NOT_EVALUATED" if stale_sec else "FAILED", "decision": "INSUFFICIENT_EVIDENCE" if stale_sec else "FAILED", "reason": reason, "security_id": sid, "provider": failure_provider},\n'''
if old not in text:
    raise SystemExit("SEC stage-result attribution target not found")
text = text.replace(old, new, 1)

old = '''                    reason = str(exc)[:240]\n                    research_provider_failures.append({"security_id": sid, "error": reason})\n                    self.store.record_stage_result(\n                        run.run_id, None, "RESEARCH_PROVIDER_FAILURE", sid,\n                        {"status": "FAILED", "reason": reason, "security_id": sid},\n'''
new = '''                    reason = str(exc)[:240]\n                    failure_provider = str(getattr(research_provider, "provider_name", research_provider.__class__.__name__))\n                    research_provider_failures.append({"security_id": sid, "error": reason, "provider": failure_provider})\n                    self.store.record_stage_result(\n                        run.run_id, None, "RESEARCH_PROVIDER_FAILURE", sid,\n                        {"status": "FAILED", "reason": reason, "security_id": sid, "provider": failure_provider},\n'''
if old not in text:
    raise SystemExit("research exception attribution target not found")
text = text.replace(old, new, 1)

old = '''                    research_provider_failures.append({"security_id": sid, "error": "normalized research source contract incomplete"})\n                    self.store.record_stage_result(\n                        run.run_id, None, "RESEARCH_PROVIDER_FAILURE", sid,\n                        {"status": "FAILED", "reason": "normalized research source contract incomplete", "security_id": sid},\n'''
new = '''                    research_provider_failures.append({"security_id": sid, "error": "normalized research source contract incomplete", "provider": research_artifact.provider})\n                    self.store.record_stage_result(\n                        run.run_id, None, "RESEARCH_PROVIDER_FAILURE", sid,\n                        {"status": "FAILED", "reason": "normalized research source contract incomplete", "security_id": sid, "provider": research_artifact.provider},\n'''
if old not in text:
    raise SystemExit("research contract attribution target not found")
text = text.replace(old, new, 1)
runtime.write_text(text, encoding="utf-8")

shadow = Path("stock_agent/shadow.py")
text = shadow.read_text(encoding="utf-8")
old = '''                "stage": (stage_values.get("STAGE_GATE") or {}).get("decision") if isinstance(stage_values.get("STAGE_GATE"), dict) else None,\n'''
new = '''                "stage": (stage_values.get("STAGE_GATE") or {}).get("proposed_stage") if isinstance(stage_values.get("STAGE_GATE"), dict) else None,\n'''
if old not in text:
    raise SystemExit("Shadow stage export target not found")
text = text.replace(old, new, 1)
shadow.write_text(text, encoding="utf-8")

regression = Path("tests/test_shadow_observability.py")
regression.write_text('''from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stock_agent.models import EffectiveRuleSet, RunMode, canonical_hash
from stock_agent.paths import canonical_prompt_library_root
from stock_agent.runtime import StockAgent, StockAgentConfig
from stock_agent.shadow import DailyShadowRunner


LIBRARY = canonical_prompt_library_root()


class ShadowObservabilityTests(unittest.TestCase):
    @staticmethod
    def metadata():
        return {
            "code_git_sha": "a" * 40,
            "branch": "test",
            "ruleset_hash": "rules",
            "prompt_library_hash": "prompt",
            "config_hash": "config",
            "model": "fixture",
            "provider": "fixture",
            "reasoning_effort": {"BALANCED": "medium"},
            "schema_version": "shadow-log-v1",
            "database_schema_version": "shadow-v1",
            "timezone": "Asia/Seoul",
            "broker_write_count": 0,
        }

    def test_decision_exports_actual_proposed_stage(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        run = agent.store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "ctx", 0)
        agent.store.record_stage_result(
            run.run_id, None, "STAGE_GATE", "ABC",
            {"decision": "PASS", "proposed_stage": "STAGE_1"},
            [], canonical_hash([]), 0,
        )
        agent.store.finish_run(run.run_id, "NO_QUALIFIED_CANDIDATE")
        shadow = agent.store.reserve_shadow_run("2026-08-30", "SHADOW_TEST", {})
        with tempfile.TemporaryDirectory() as temp:
            rows = DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: {"status": "PASS"})._decisions(
                shadow["shadow_run_id"], run.run_id, None
            )
        self.assertEqual(rows[0]["stage"], "STAGE_1")
        agent.close()

    def test_not_evaluated_incident_preserves_provider(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        run = agent.store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "ctx", 0)
        agent.store.record_stage_result(
            run.run_id, None, "STAGE_GATE", "ABC",
            {"decision": "PASS", "proposed_stage": "STAGE_0"},
            [], canonical_hash([]), 0,
        )
        agent.store.record_stage_result(
            run.run_id, None, "RESEARCH_PROVIDER_FAILURE", "ABC",
            {"status": "FAILED", "reason": "stale research input exceeds max-age", "security_id": "ABC", "provider": "yahoo-finance-news"},
            [], canonical_hash([]), 0,
        )
        agent.store.finish_run(run.run_id, "NO_QUALIFIED_CANDIDATE")
        shadow = agent.store.reserve_shadow_run("2026-08-30", "SHADOW_TEST", {})
        with tempfile.TemporaryDirectory() as temp:
            DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: {"status": "PASS"})._decisions(
                shadow["shadow_run_id"], run.run_id, None
            )
        incidents = [dict(row) for row in agent.store.connection.execute("SELECT * FROM shadow_incidents").fetchall()]
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["provider"], "yahoo-finance-news")
        agent.close()


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")
