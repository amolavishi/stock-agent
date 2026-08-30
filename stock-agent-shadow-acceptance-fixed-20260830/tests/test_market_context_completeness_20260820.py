from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root

from stock_agent.adapters import RecordedMarketDataProvider
from stock_agent.gates import GateDecision, MarketContextGate
from stock_agent.models import EffectiveRuleSet, RunMode, utc_now
from stock_agent.providers import FakeProvider
from stock_agent.runtime import ProductionStockAgent, StockAgentConfig


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = canonical_prompt_library_root()


def context_assets(*, omit: str | None = None, semiconductor: str = "SOXX", stale: str | None = None):
    now = datetime.now(timezone.utc)
    symbols = ["SPY", "QQQ", "IWM", semiconductor, "VIX", "US10Y", "DXY", "WTI", "BTC", "ETH"]
    assets = {}
    for symbol in symbols:
        if symbol == omit:
            continue
        observed = now - timedelta(days=30) if symbol == stale else now
        assets[symbol] = {
            "symbol": symbol,
            "observed_at": observed.isoformat(),
            "source": "recorded-test",
            "observation_count": 3,
        }
    return {
        "complete": True,  # deliberately non-authoritative caller claim
        "regime": "RISK_ON",
        "breadth": "BROAD",
        "volatility": "NORMAL",
        "normalization_status": "COMPLETE",
        "assets": assets,
    }


class MarketContextCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.gate = MarketContextGate()
        self.rules = EffectiveRuleSet()

    def test_provider_complete_claim_without_assets_cannot_pass(self):
        context = {"complete": True, "regime": "RISK_ON", "breadth": "BROAD", "volatility": "NORMAL", "normalization_status": "COMPLETE"}
        receipt = self.gate.evaluate(context, self.rules)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)
        self.assertFalse(receipt.core_input_complete)

    def test_all_required_assets_pass(self):
        receipt = self.gate.evaluate(context_assets(), self.rules)
        self.assertEqual(receipt.decision, GateDecision.PASS)
        self.assertTrue(receipt.core_input_complete)

    def test_smh_can_satisfy_semiconductor_group(self):
        receipt = self.gate.evaluate(context_assets(semiconductor="SMH"), self.rules)
        self.assertEqual(receipt.decision, GateDecision.PASS)

    def test_missing_required_asset_fails_closed(self):
        receipt = self.gate.evaluate(context_assets(omit="ETH"), self.rules)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_stale_required_asset_fails_closed(self):
        receipt = self.gate.evaluate(context_assets(stale="DXY"), self.rules)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_missing_asset_timestamp_fails_closed(self):
        context = context_assets()
        context["assets"]["WTI"]["observed_at"] = None
        receipt = self.gate.evaluate(context, self.rules)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_unknown_label_fails_closed_even_with_assets(self):
        context = context_assets()
        context["volatility"] = "UNKNOWN"
        receipt = self.gate.evaluate(context, self.rules)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_strict_runtime_rejects_labels_only_context(self):
        market = RecordedMarketDataProvider({
            "market_context": {"complete": True, "regime": "RISK_ON", "breadth": "BROAD", "volatility": "NORMAL", "normalization_status": "COMPLETE"},
            "candidates": [],
        })
        config = StockAgentConfig(LIBRARY, Path(":memory:"), strict_inputs=True, market_data_provider=market)
        agent = ProductionStockAgent(config, provider=FakeProvider())
        try:
            outcome = agent.run(RunMode.HUNT_ONLY, {})
            self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")
            self.assertEqual(agent.store.connection.execute("SELECT funnel_stage FROM discovery_funnel WHERE run_id=?", (outcome.run_id,)).fetchone()[0], "MARKET_CONTEXT_GATE")
        finally:
            agent.close()


if __name__ == "__main__":
    unittest.main()

