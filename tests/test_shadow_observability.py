from __future__ import annotations

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
