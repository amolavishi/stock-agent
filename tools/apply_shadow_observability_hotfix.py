from pathlib import Path

runtime = Path("stock_agent/runtime.py")
text = runtime.read_text(encoding="utf-8")

# Operational provider attribution only. Do not mutate authoritative gate receipts.
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

old = '''                try:\n                    research_artifact = research_provider.fetch(sid, data.get("research_query") or {})\n'''
new = '''                research_artifact = None\n                try:\n                    research_artifact = research_provider.fetch(sid, data.get("research_query") or {})\n'''
if old not in text:
    raise SystemExit("research artifact initialization target not found")
text = text.replace(old, new, 1)

old = '''                    reason = str(exc)[:240]\n                    research_provider_failures.append({"security_id": sid, "error": reason})\n                    self.store.record_stage_result(\n                        run.run_id, None, "RESEARCH_PROVIDER_FAILURE", sid,\n                        {"status": "FAILED", "reason": reason, "security_id": sid},\n'''
new = '''                    reason = str(exc)[:240]\n                    failure_provider = str(\n                        getattr(research_artifact, "provider", "")\n                        or getattr(research_provider, "provider_name", research_provider.__class__.__name__)\n                    )\n                    research_provider_failures.append({"security_id": sid, "error": reason, "provider": failure_provider})\n                    self.store.record_stage_result(\n                        run.run_id, None, "RESEARCH_PROVIDER_FAILURE", sid,\n                        {"status": "FAILED", "reason": reason, "security_id": sid, "provider": failure_provider},\n'''
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

# Project an already-existing discovery stage into Shadow reporting. The
# authoritative STAGE_GATE receipt remains byte-for-byte unchanged.
old = '''        rows = self._stage_rows(hunt_run_id)\n        subjects = sorted({str(row["subject_id"]) for row in rows if row.get("subject_id")})\n        execution_action: dict[str, dict[str, Any]] = {}\n'''
new = '''        rows = self._stage_rows(hunt_run_id)\n        subjects = sorted({str(row["subject_id"]) for row in rows if row.get("subject_id")})\n        proposed_stage_by_subject: dict[str, str] = {}\n        for row in rows:\n            if str(row.get("stage") or "") != "STOCK_DISCOVERY" or row.get("status") != "SUCCEEDED":\n                continue\n            discovery_value = self._decode(row.get("result_json") or "{}")\n            if not isinstance(discovery_value, dict):\n                continue\n            for candidate in discovery_value.get("candidates") or []:\n                if not isinstance(candidate, dict) or not candidate.get("security_id") or not candidate.get("proposed_stage"):\n                    continue\n                proposed_stage_by_subject[str(candidate["security_id"])] = str(candidate["proposed_stage"])\n        execution_action: dict[str, dict[str, Any]] = {}\n'''
if old not in text:
    raise SystemExit("Shadow discovery projection insertion target not found")
text = text.replace(old, new, 1)

old = '''                "stage": (stage_values.get("STAGE_GATE") or {}).get("decision") if isinstance(stage_values.get("STAGE_GATE"), dict) else None,\n'''
new = '''                "stage": proposed_stage_by_subject.get(subject),\n'''
if old not in text:
    raise SystemExit("Shadow stage export target not found")
text = text.replace(old, new, 1)
shadow.write_text(text, encoding="utf-8")

regression = Path("tests/test_shadow_observability.py")
regression.write_text('''from __future__ import annotations

import json
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

    def test_decision_projects_stage_from_stock_discovery_without_mutating_gate_receipt(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        run = agent.store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "ctx", 0)
        gate_receipt = {"decision": "PASS", "receipt_hash": "gate-receipt"}
        agent.store.record_stage_result(run.run_id, None, "STAGE_GATE", "ABC", gate_receipt, [], canonical_hash([]), 0)
        agent.store.record_stage_result(
            run.run_id, None, "STOCK_DISCOVERY", None,
            {"candidates": [{"security_id": "ABC", "proposed_stage": "STAGE_1", "recommended_discovery_action": "DEEP_DIVE_NOW"}]},
            [], canonical_hash([]), 0,
        )
        persisted = agent.store.get_stage_result(run.run_id, "STAGE_GATE", "ABC")
        self.assertEqual(json.loads(persisted["result_json"]), gate_receipt)
        agent.store.finish_run(run.run_id, "NO_QUALIFIED_CANDIDATE")
        shadow = agent.store.reserve_shadow_run("2026-08-30", "SHADOW_TEST", {})
        with tempfile.TemporaryDirectory() as temp:
            rows = DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: {"status": "PASS"})._decisions(shadow["shadow_run_id"], run.run_id, None)
        self.assertEqual(rows[0]["stage"], "STAGE_1")
        persisted_after = agent.store.get_stage_result(run.run_id, "STAGE_GATE", "ABC")
        self.assertEqual(json.loads(persisted_after["result_json"]), gate_receipt)
        agent.close()

    def test_not_evaluated_incident_preserves_provider_in_incident_json(self):
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")))
        run = agent.store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "ctx", 0)
        agent.store.record_stage_result(run.run_id, None, "STAGE_GATE", "ABC", {"decision": "PASS"}, [], canonical_hash([]), 0)
        agent.store.record_stage_result(
            run.run_id, None, "RESEARCH_PROVIDER_FAILURE", "ABC",
            {"status": "FAILED", "reason": "stale research input exceeds max-age", "security_id": "ABC", "provider": "yahoo-finance-news"},
            [], canonical_hash([]), 0,
        )
        agent.store.finish_run(run.run_id, "NO_QUALIFIED_CANDIDATE")
        shadow = agent.store.reserve_shadow_run("2026-08-30", "SHADOW_TEST", {})
        with tempfile.TemporaryDirectory() as temp:
            DailyShadowRunner(agent, temp, self.metadata(), provider_health=lambda: {"status": "PASS"})._decisions(shadow["shadow_run_id"], run.run_id, None)
        incident_rows = agent.store.connection.execute("SELECT incident_json FROM shadow_incidents").fetchall()
        incidents = [json.loads(row["incident_json"]) for row in incident_rows]
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["provider"], "yahoo-finance-news")
        agent.close()


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")
