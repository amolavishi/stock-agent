from __future__ import annotations

import unittest
from datetime import datetime, timezone

from stock_agent.gates import MarketContextGate, MarketExecutionGate, SectorGate
from stock_agent.models import EffectiveRuleSet, GateDecision
from stock_agent.v8_market_discovery_admission import (
    _MarketContextDiscoveryAdmissionGate,
    _SectorDiscoveryAdmissionGate,
)


class MarketDiscoveryAdmissionTests(unittest.TestCase):
    @staticmethod
    def context():
        stamp = "2026-09-01T20:00:00Z"
        assets = {
            symbol: {"observed_at": stamp, "source": "fixture", "observation_count": 2}
            for symbol in ("SPY", "QQQ", "IWM", "VIX")
        }
        return {
            "assets": assets,
            "regime": "ROTATION",
            "breadth": "MIXED",
            "volatility": "NORMAL",
            "normalization_status": "PARTIAL",
        }

    def test_partial_noncore_context_can_admit_discovery_without_forging_canonical_pass(self):
        state = {}
        strict = MarketContextGate()
        canonical = strict.evaluate(self.context(), EffectiveRuleSet(), evaluation_time=datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc))
        proxy = _MarketContextDiscoveryAdmissionGate(strict, lambda admitted, reason: state.update(admitted=admitted, reason=reason))
        receipt = proxy.evaluate(self.context(), EffectiveRuleSet(), evaluation_time=datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(receipt.decision, GateDecision.PASS)
        self.assertEqual(receipt.as_dict()["decision"], GateDecision.INSUFFICIENT_EVIDENCE.value)
        self.assertEqual(receipt.receipt_hash, canonical.receipt_hash)
        self.assertEqual(receipt.core_input_complete, canonical.core_input_complete)
        self.assertFalse(receipt.core_input_complete)
        self.assertTrue(state["admitted"])
        self.assertEqual(state["reason"], "PARTIAL_CONTEXT_CORE_DISCOVERY_VALID")

    def test_missing_core_iwm_does_not_admit_discovery(self):
        context = self.context()
        del context["assets"]["IWM"]
        state = {}
        proxy = _MarketContextDiscoveryAdmissionGate(MarketContextGate(), lambda admitted, reason: state.update(admitted=admitted, reason=reason))
        receipt = proxy.evaluate(context, EffectiveRuleSet(), evaluation_time=datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)
        self.assertFalse(state["admitted"])

    def test_partial_sector_context_does_not_kill_bottom_up_after_market_core_admission(self):
        state = {}
        strict = SectorGate()
        canonical = strict.evaluate({"eligible": False}, EffectiveRuleSet())
        proxy = _SectorDiscoveryAdmissionGate(strict, lambda: True, lambda admitted, reason: state.update(admitted=admitted, reason=reason))
        receipt = proxy.evaluate({"eligible": False}, EffectiveRuleSet())
        self.assertEqual(receipt.decision, GateDecision.PASS)
        self.assertEqual(receipt.as_dict()["decision"], GateDecision.INSUFFICIENT_EVIDENCE.value)
        self.assertEqual(receipt.receipt_hash, canonical.receipt_hash)
        self.assertTrue(state["admitted"])

    def test_market_execution_gate_remains_strict(self):
        receipt = MarketExecutionGate().evaluate({"core_input_complete": False}, EffectiveRuleSet())
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
