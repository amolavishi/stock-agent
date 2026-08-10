import tempfile
import unittest
from pathlib import Path

from stock_agent.agents import MockCriticAgent, MockResearchAgent
from stock_agent.evidence import MockEvidenceCollector
from stock_agent.guard import FinalGuard
from stock_agent.market import MockMarketDataProvider
from stock_agent.schemas import RiskResult
from stock_agent.tool_service import StockAgentToolService, TOOL_SERVER_VERSION
from stock_agent.trade_plan import build_heuristic_trade_plan


def config(root):
    return {
        "mode": "PAPER", "database_path": str(Path(root) / "db.sqlite"),
        "vault_path": str(Path(root) / "vault"), "credentials": {
            "toss_app_key": "test", "toss_app_secret": "test", "sec_user_agent": "test x@example.com",
            "discord_research_token": "test", "discord_critic_token": "test",
            "discord_chairman_token": "test", "discord_channel_id": "1",
            "discord_debate_channel_id": "2", "discord_report_channel_id": "3",
        },
        "risk_rules": {"minimum_price_usd": 3, "minimum_market_cap_usd": 300000000,
            "minimum_avg_volume_usd": 10000000, "minimum_reward_risk": 2,
            "stage_3_action": "WAIT", "max_data_age_days": 3, "high_volatility_atr_pct": 12,
            "max_position_pct": 10, "max_loss_pct": 0.75},
    }


class ToolLayerTests(unittest.TestCase):
    def test_idempotent_start_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = StockAgentToolService(config(tmp))
            first = service.audit_start_run("IONQ", "same-request")
            second = service.audit_start_run("IONQ", "same-request")
            self.assertTrue(first["ok"] and second["ok"])
            self.assertEqual(first["data"]["run_id"], second["data"]["run_id"])
            with service.db.connect() as c:
                count = c.execute("SELECT count(*) FROM analysis_runs").fetchone()[0]
            self.assertEqual(count, 1)

    def test_structured_error_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = StockAgentToolService(config(tmp)).market_get_snapshot("../../bad")
            self.assertFalse(result["ok"])
            self.assertIsNotNone(result["error"]["code"])

    def test_final_guard_blocks_risk_override(self):
        provider = MockMarketDataProvider()
        market = provider.snapshot("IONQ")
        plan = build_heuristic_trade_plan(market)
        risk = RiskResult("IONQ", False, [], ["hard failure"], plan, "EXCLUDE")
        result = FinalGuard.validate_final({"decision": "BUY"}, risk, True, True)
        self.assertEqual(result["final_decision"], "EXCLUDE")
        self.assertTrue(result["risk_override_applied"])

    def test_trade_plan_guard(self):
        market = MockMarketDataProvider().snapshot("IONQ")
        plan = build_heuristic_trade_plan(market)
        self.assertTrue(FinalGuard.validate_trade_plan(plan)["valid"])
        plan.stop_price = plan.entry_price
        self.assertFalse(FinalGuard.validate_trade_plan(plan)["valid"])

    def test_tool_server_version_is_explicit(self):
        self.assertEqual(TOOL_SERVER_VERSION, "stock-agent-mcp-v001")

    def test_no_order_or_shell_methods_exposed_by_service(self):
        names = {name for name in dir(StockAgentToolService) if not name.startswith("_")}
        forbidden = {"shell", "execute", "order", "buy_order", "sell_order", "sql", "delete_file"}
        self.assertFalse(names & forbidden)

    def test_claim_guard_accepts_known_ids(self):
        evidence = MockEvidenceCollector().collect("IONQ")
        claims = [{"claim": "x", "evidence_ids": [evidence[0].evidence_id],
                   "materiality": "MATERIAL", "domain": "SEC_FILING",
                   "claim_type": "FACT", "minimum_evidence_grade": "UNCLASSIFIED"}]
        self.assertTrue(FinalGuard.validate_claims(claims, evidence)["valid"])

    def test_paper_performance_update_requires_known_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = StockAgentToolService(config(tmp))
            missing = service.paper_update_performance(
                "missing", 10, [11], [12], [9], 8, 12, 14)
            self.assertFalse(missing["ok"])
            service.audit_start_run("IONQ", "known")
            saved = service.paper_update_performance(
                "known", 10, [11], [12], [9], 8, 12, 14)
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["data"]["return_pct"], 10.0)


if __name__ == "__main__":
    unittest.main()
