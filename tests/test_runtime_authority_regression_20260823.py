from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from stock_agent.adapters import TossMarketDataProvider
from stock_agent.gates import ContractViolation, RiskEngine
from stock_agent.models import EffectiveRuleSet, RawArtifact, RunMode, canonical_hash, utc_now
from stock_agent.normalizers import PortfolioNormalizer
from stock_agent.paths import canonical_prompt_library_root
from stock_agent.runtime import StockAgent, StockAgentConfig
from stock_agent.store import SQLiteStore


class RuntimeAuthorityRegression20260823(unittest.TestCase):
    def test_risk_engine_exposes_target_position_not_transaction_delta(self):
        result = RiskEngine().assess(
            current_price=10.0,
            execution_stop=9.0,
            structural_asymmetry=5.0,
            probability_weighted_ev=2.0,
            account_equity=1000.0,
            risk_budget_pct=1.0,
        )
        self.assertEqual(result["arithmetic_source"], "PYTHON_RISK_ENGINE")
        self.assertEqual(result["risk_target_position_shares"], result["shares"])

    def test_qualified_pool_requires_complete_dag_from_database(self):
        store = SQLiteStore(":memory:")
        run = store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "ctx", 0)
        qualified, missing = store.qualified_candidate_status(run.run_id, "SEC1")
        self.assertFalse(qualified)
        for stage in (
            "STAGE_GATE",
            "CAPITAL_PRESCREEN_GATE",
            "CATALYST_GATE",
            "CAP_FUNDAMENTAL_CHANGE",
            "CAP_CATALYST_EXPECTATION_RESEARCH",
            "CAP_DIRECTIONAL_PROBABILITY",
            "DEEP_RESEARCH",
            "FULL_SEC_FORENSIC",
            "STANDARD_AUDIT",
            "ADVERSARIAL_AUDIT",
            "EXPECTATION_GAP_GATE",
        ):
            self.assertIn(stage, missing)
        store.close()

    def test_toss_market_snapshot_does_not_copy_caller_risk_fields(self):
        provider = TossMarketDataProvider("id", "secret")
        stamp = utc_now()
        price_payload = {"result": [{"lastPrice": 12.5}], "observed_at": stamp}
        candle_payload = {"result": [{"close": 12.0}, {"close": 12.5}], "observed_at": stamp}
        with patch.object(provider, "fetch_prices", return_value=type("A", (), {"payload": price_payload})()), \
             patch.object(provider, "fetch_candles", return_value=type("A", (), {"payload": candle_payload})()):
            artifact = provider.fetch_execution_snapshot(
                "SEC1",
                {"current_price": 999999, "execution_stop": 1, "account_equity": 1, "gap_risk": 0, "event_risk_pct": 0},
            )
        self.assertEqual(artifact.payload["current_price"], 12.5)
        self.assertNotIn("execution_stop", artifact.payload)
        self.assertNotIn("account_equity", artifact.payload)
        self.assertFalse(artifact.payload["core_input_complete"])

    def test_execution_context_merges_provider_price_and_portfolio_equity(self):
        stamp = utc_now()
        market_payload = {
            "security_id": "SEC1", "current_price": 12.5,
            "core_input_complete": False, "account_equity": 1.0,
        }
        portfolio_payload = {"as_of": stamp, "cash": 2500.0, "total_equity": 2500.0, "positions": [], "currency": "USD"}
        market_artifact = RawArtifact("market", "toss", "MARKET_EXECUTION", "SEC1", stamp, market_payload, canonical_hash(market_payload), stamp, stamp)
        portfolio_artifact = RawArtifact("portfolio", "toss-portfolio", "PORTFOLIO_SNAPSHOT", None, stamp, portfolio_payload, canonical_hash(portfolio_payload), stamp, stamp)
        agent = StockAgent(StockAgentConfig(canonical_prompt_library_root(), Path(":memory:")))
        snapshot = agent._build_execution_context(
            market_artifact,
            portfolio_artifact,
            PortfolioNormalizer().normalize(portfolio_artifact),
            {"authoritative_execution_inputs": {"execution_stop": 10.0, "gap_risk": 0.5, "event_risk_pct": 2.0, "account_equity": 1.0}},
            "SEC1",
        )
        self.assertEqual(snapshot.current_price, 12.5)
        self.assertEqual(snapshot.execution_stop, 10.0)
        self.assertEqual(snapshot.account_equity, 2500.0)
        self.assertTrue(snapshot.core_input_complete)
        self.assertEqual(snapshot.source_artifact_ids, ("market", "portfolio"))
        agent.close()

    def test_incomplete_toss_context_without_python_stop_fails_closed(self):
        stamp = utc_now()
        market_payload = {"security_id": "SEC1", "current_price": 12.5, "core_input_complete": False}
        portfolio_payload = {"as_of": stamp, "cash": 2500.0, "total_equity": 2500.0, "positions": []}
        market_artifact = RawArtifact("market", "toss", "MARKET_EXECUTION", "SEC1", stamp, market_payload, canonical_hash(market_payload), stamp, stamp)
        portfolio_artifact = RawArtifact("portfolio", "toss-portfolio", "PORTFOLIO_SNAPSHOT", None, stamp, portfolio_payload, canonical_hash(portfolio_payload), stamp, stamp)
        agent = StockAgent(StockAgentConfig(canonical_prompt_library_root(), Path(":memory:")))
        with self.assertRaises(ContractViolation):
            agent._build_execution_context(market_artifact, portfolio_artifact, PortfolioNormalizer().normalize(portfolio_artifact), {"execution_stop": 10.0, "account_equity": 1.0}, "SEC1")
        agent.close()


if __name__ == "__main__":
    unittest.main()
