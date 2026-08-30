from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import json

from stock_agent.adapters import (
    CompositeLiveMarketContextProvider,
    CoinGeckoMarketDataProvider,
    FREDMarketDataProvider,
    YahooChartMarketDataProvider,
    _market_asset_observation,
    deterministic_market_context_from_payload,
)
from stock_agent.gates import GateDecision, MarketContextGate, _latest_completed_us_session_date
from stock_agent.models import EffectiveRuleSet
from stock_agent.runtime import ProductionStockAgent, StockAgentConfig
from stock_agent.store import SQLiteStore
from stock_agent.paths import canonical_prompt_library_root
from pathlib import Path


def _fresh(symbol: str, provider: str = "fixture", observed_at: str | None = None):
    observed = observed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    unit = {
        "SPY": "USD_PER_SHARE", "QQQ": "USD_PER_SHARE", "IWM": "USD_PER_SHARE",
        "SOXX": "USD_PER_SHARE", "SMH": "USD_PER_SHARE", "WTI": "USD_PER_BARREL",
        "BTC": "USD_PER_COIN", "ETH": "USD_PER_COIN", "US10Y": "PERCENT",
    }.get(symbol, "INDEX_POINTS")
    currency = "USD" if unit.startswith("USD_") else None
    return _market_asset_observation(
        symbol=symbol,
        provider=provider,
        source_identifier=f"https://example.test/{symbol}",
        payload={"observations": [{"timestamp": observed, "value": 1.0}, {"timestamp": observed, "value": 1.1}]},
        values=[1.0, 1.1],
        observed_at=observed,
        unit=unit,
        currency=currency,
    )


def _context(*, omit: str | None = None, stale: str | None = None, observed_at: str | None = None):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    symbols = ["SPY", "QQQ", "IWM", "SOXX", "VIX", "US10Y", "DXY", "WTI", "BTC", "ETH"]
    observations = []
    for symbol in symbols:
        if symbol == omit:
            continue
        timestamp = observed_at or now.isoformat().replace("+00:00", "Z")
        if symbol == stale:
            timestamp = (now - timedelta(days=3)).isoformat().replace("+00:00", "Z")
        observations.append(_fresh(symbol, observed_at=timestamp))
    normalized = deterministic_market_context_from_payload(observations)
    normalized["source"] = [{k: v for k, v in item.items() if k != "_raw_artifact"} for item in observations]
    for item in observations:
        normalized["assets"][item["symbol"]].update({k: v for k, v in item.items() if k not in {"payload", "_raw_artifact"}})
    return normalized


class _FakeToss:
    def fetch_market_context(self, query):
        items = [_fresh(symbol, provider="toss") for symbol in query["symbols"]]
        normalized = deterministic_market_context_from_payload(items)
        normalized["source"] = [{k: v for k, v in item.items() if k != "_raw_artifact"} for item in items]
        normalized["asset_raw_artifacts"] = [item["_raw_artifact"] for item in items]
        for item in items:
            normalized["assets"][item["symbol"]].update({k: v for k, v in item.items() if k not in {"payload", "_raw_artifact"}})
        return type("Artifact", (), {"payload": normalized})()


class _FakeMacro:
    def fetch_series(self, symbol):
        return _fresh(symbol, provider="macro")


class _FakeDxy:
    def fetch_series(self, symbol):
        return _fresh(symbol, provider="dxy")


class _FakeCrypto:
    def fetch_series(self, symbol):
        return _fresh(symbol, provider="crypto")


class LiveMarketContextAdapterTests(unittest.TestCase):
    def setUp(self):
        self.rules = EffectiveRuleSet()
        self.gate = MarketContextGate()

    def test_composite_contains_all_required_assets_and_provenance(self):
        provider = CompositeLiveMarketContextProvider(_FakeToss(), _FakeMacro(), _FakeDxy(), _FakeCrypto())
        artifact = provider.fetch_market_context({})
        self.assertEqual(set(artifact.payload["assets"]), {"SPY", "QQQ", "IWM", "SOXX", "VIX", "US10Y", "DXY", "WTI", "BTC", "ETH"})
        self.assertEqual(self.gate.evaluate(artifact.payload, self.rules).decision, GateDecision.PASS)
        self.assertEqual(len(artifact.payload["asset_raw_artifacts"]), 10)
        for details in artifact.payload["assets"].values():
            self.assertTrue(details["raw_artifact_id"])
            self.assertTrue(details["evidence_id"])
            self.assertTrue(details["observed_at"])
            self.assertGreaterEqual(details["observation_count"], 2)

    def test_missing_vix_fails_closed(self):
        receipt = self.gate.evaluate(_context(omit="VIX"), self.rules)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_stale_us10y_fails_closed(self):
        receipt = self.gate.evaluate(_context(stale="US10Y"), self.rules)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_missing_observed_at_fails_closed(self):
        context = _context()
        context["assets"]["DXY"]["observed_at"] = None
        self.assertEqual(self.gate.evaluate(context, self.rules).decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_cross_asset_spread_beyond_freshness_fails(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        receipt = self.gate.evaluate(_context(observed_at=old), self.rules)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_synchronization_policy_is_separate_from_asset_freshness(self):
        context = _context()
        context["assets"]["WTI"]["observed_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        rules = EffectiveRuleSet(max_age_market_context_hours=72, max_market_context_sync_spread_hours=1)
        receipt = self.gate.evaluate(context, rules)
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_provider_complete_claim_cannot_override_python_completeness(self):
        context = {"complete": True, "regime": "RISK_ON", "breadth": "BROAD", "volatility": "NORMAL", "normalization_status": "COMPLETE"}
        self.assertEqual(self.gate.evaluate(context, self.rules).decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_live_nan_value_fails_closed(self):
        context = _context()
        context["assets"]["DXY"]["value"] = float("nan")
        self.assertEqual(self.gate.evaluate(context, self.rules).decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_live_missing_provenance_fails_closed(self):
        context = _context()
        context["assets"]["DXY"].pop("raw_artifact_id")
        self.assertEqual(self.gate.evaluate(context, self.rules).decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_live_canonical_asset_identity_mismatch_fails_closed(self):
        context = _context()
        context["assets"]["DXY"]["symbol"] = "BTC"
        self.assertEqual(self.gate.evaluate(context, self.rules).decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_non_finite_observation_is_rejected_before_gate(self):
        with self.assertRaises(Exception):
            _market_asset_observation(symbol="DXY", provider="fixture", source_identifier="https://example.test/DXY", payload={}, values=[1.0, float("nan")], observed_at=datetime.now(timezone.utc).isoformat(), unit="INDEX_POINTS", currency=None)

    def test_fred_csv_adapter_normalizes_official_series(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size=-1): return b"observation_date,VIXCLS\n2026-08-20,16.0\n2026-08-21,16.5\n"
        with patch("stock_agent.adapters.urllib.request.urlopen", return_value=Response()):
            result = FREDMarketDataProvider().fetch_series("VIX")
        self.assertEqual(result["provider"], "fred")
        self.assertEqual(result["value"], 16.5)
        self.assertEqual(result["observed_at"], "2026-08-21T00:00:00Z")

    def test_yahoo_chart_adapter_normalizes_dxy(self):
        payload = {"chart": {"result": [{"timestamp": [1761000000, 1761086400], "indicators": {"quote": [{"close": [98.1, 98.4]}]}}]}}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size=-1): return json.dumps(payload).encode()
        with patch("stock_agent.adapters.urllib.request.urlopen", return_value=Response()):
            result = YahooChartMarketDataProvider().fetch_series("DXY")
        self.assertEqual(result["provider"], "yahoo-chart")
        self.assertEqual(result["value"], 98.4)

    def test_coingecko_adapter_normalizes_crypto(self):
        payload = {"prices": [[1761000000000, 100.0], [1761003600000, 101.0]]}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size=-1): return json.dumps(payload).encode()
        with patch("stock_agent.adapters.urllib.request.urlopen", return_value=Response()):
            result = CoinGeckoMarketDataProvider().fetch_series("BTC")
        self.assertEqual(result["provider"], "coingecko")
        self.assertEqual(result["value"], 101.0)
        self.assertEqual(result["currency"], "USD")

    def test_strict_market_asset_evidence_preserves_raw_artifact_lineage(self):
        """Every persisted market Evidence row must point to its exact RawArtifact."""
        store = SQLiteStore(":memory:")
        config = StockAgentConfig(canonical_prompt_library_root(), Path(":memory:"), strict_inputs=True)
        agent = ProductionStockAgent(config, provider=None, store=store)
        try:
            raw = _fresh("SPY")
            agent._persist_market_asset_artifacts({"asset_raw_artifacts": [raw["_raw_artifact"]]})
            row = store.connection.execute(
                "SELECT evidence_id, raw_artifact_id, payload_hash FROM evidence"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["raw_artifact_id"], raw["_raw_artifact"]["artifact_id"])
            self.assertEqual(row["payload_hash"], raw["_raw_artifact"]["payload_hash"])
        finally:
            agent.close()
            store.close()

    def test_weekend_uses_latest_completed_friday_session(self):
        saturday = datetime(2026, 8, 22, 15, tzinfo=timezone.utc)
        self.assertEqual(_latest_completed_us_session_date(saturday).isoformat(), "2026-08-21")

    def test_us_market_holiday_uses_prior_completed_session(self):
        labor_day = datetime(2026, 9, 7, 15, tzinfo=timezone.utc)
        self.assertEqual(_latest_completed_us_session_date(labor_day).isoformat(), "2026-09-04")


if __name__ == "__main__":
    unittest.main()
