from __future__ import annotations

import unittest
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root

from stock_agent.models import RunMode
from stock_agent.providers import FakeProvider
from stock_agent.runtime import StockAgent, StockAgentConfig
from stock_agent.store import SQLiteStore
from tests.test_stock_agent import execution_input


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = canonical_prompt_library_root()


class ProductionPathTests(unittest.TestCase):
    def make(self, provider=None):
        return StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")), provider=provider)

    def test_recorded_style_full_dag_calls_every_leaf(self):
        provider = FakeProvider(); agent = self.make(provider)
        outcome = agent.run(RunMode.HUNT_ONLY, execution_input())
        self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
        self.assertEqual([c["request"]["prompt_id"] for c in provider.calls], [
            "workflow.market_analyst", "workflow.sector_analyst", "workflow.stock_scout",
            "utility.capital_structure_prescreen", "workflow.stock_researcher",
            "utility.sec_extraction", "workflow.adversarial_reviewer",
        ])
        self.assertGreater(agent.store.connection.execute("SELECT COUNT(*) FROM stage_results").fetchone()[0], 0)

    def test_toxic_tri_state_fails_closed(self):
        agent = self.make(); data = execution_input(); data["candidates"][0]["capital_prescreen"] = {"toxic_convertible": {"state": "TRUE"}, "complete": True}
        self.assertEqual(agent.run(RunMode.HUNT_ONLY, data).outcome, "NO_QUALIFIED_CANDIDATE")

    def test_missing_full_sec_blocks_pool(self):
        def responder(request):
            payload = request["default_payload"]
            if request["prompt_id"] == "utility.sec_extraction": payload["status"] = "INCOMPLETE"
            return payload
        agent = self.make(FakeProvider(responder)); self.assertEqual(agent.run(RunMode.HUNT_ONLY, execution_input()).outcome, "NO_QUALIFIED_CANDIDATE")

    def test_no_trade_cannot_carry_positive_risk_size(self):
        agent = self.make(); outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, execution_input("NO_TRADE"))
        self.assertEqual(outcome.outcome, "BLOCKED_BY_CRITICAL_ISSUE")

    def test_unregistered_override_is_not_authoritative(self):
        agent = self.make(); data = execution_input(); data["rule_override"] = {"strategy_max_days": 999}
        self.assertEqual(agent.run(RunMode.HUNT_ONLY, data).outcome, "BLOCKED_BY_CRITICAL_ISSUE")

    def test_registered_override_resolves_immutable_rules(self):
        store = SQLiteStore(":memory:"); store.register_rule_override("ov-56", "2.0", {"strategy_max_days": 56}, {"scope": "test"}, "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z", "test-authority")
        agent = StockAgent(StockAgentConfig(LIBRARY, Path(":memory:")), store=store); data = execution_input(); data["rule_override_id"] = "ov-56"
        self.assertEqual(agent.run(RunMode.HUNT_ONLY, data).outcome, "QUALIFIED_CANDIDATE_POOL")


if __name__ == "__main__":
    unittest.main()


