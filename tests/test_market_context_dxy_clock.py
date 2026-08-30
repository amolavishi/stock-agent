from __future__ import annotations

import unittest
from datetime import datetime, timezone

from stock_agent.adapters import CompositeLiveMarketContextProvider
from stock_agent.gates import MarketContextGate
from stock_agent.models import EffectiveRuleSet, RawArtifact, canonical_hash


class CompositeMarketContextClockRegressionTests(unittest.TestCase):
    def test_composite_dxy_uses_fx_clock_and_weekend_context_passes(self):
        def observation(symbol, observed_at, unit, currency):
            payload = {"data": [{"close": 100.0}, {"close": 101.0}]}
            digest = canonical_hash(payload)
            artifact_id = f"artifact-{symbol.lower()}"
            return {
                "symbol": symbol,
                "payload": payload,
                "observed_at": observed_at,
                "source_observed_at": observed_at,
                "fetched_at": "2026-08-30T12:07:23Z",
                "source": f"https://example.test/{symbol}",
                "source_identifier": f"https://example.test/{symbol}",
                "provider": "fixture",
                "value": 101.0,
                "unit": unit,
                "currency": currency,
                "observation_count": 2,
                "raw_artifact_id": artifact_id,
                "evidence_id": f"E-{artifact_id}",
                "payload_hash": digest,
            }

        exchange = "2026-08-28T13:00:00+09:00"
        daily = "2026-08-27T00:00:00Z"
        wti = "2026-08-25T00:00:00Z"
        dxy = "2026-08-27T04:00:00Z"
        crypto = "2026-08-30T12:05:00Z"

        class Toss:
            def fetch_market_context(self, query):
                source = [
                    observation("SPY", exchange, "USD_PER_SHARE", "USD"),
                    observation("QQQ", exchange, "USD_PER_SHARE", "USD"),
                    observation("IWM", exchange, "USD_PER_SHARE", "USD"),
                    observation("SOXX", exchange, "USD_PER_SHARE", "USD"),
                ]
                payload = {"source": source, "asset_raw_artifacts": []}
                return RawArtifact("toss-market", "fixture", "MARKET_CONTEXT", None, exchange, payload, canonical_hash(payload), exchange, "2026-08-30T12:07:23Z")

        class Fred:
            def fetch_series(self, symbol):
                if symbol == "VIX":
                    return observation(symbol, daily, "INDEX_POINTS", None)
                if symbol == "US10Y":
                    return observation(symbol, daily, "PERCENT", None)
                return observation(symbol, wti, "USD_PER_BARREL", "USD")

        class Yahoo:
            def fetch_series(self, symbol):
                return observation(symbol, dxy, "INDEX_POINTS", None)

        class CoinGecko:
            def fetch_series(self, symbol):
                return observation(symbol, crypto, "USD_PER_COIN", "USD")

        provider = CompositeLiveMarketContextProvider(Toss(), fred=Fred(), yahoo=Yahoo(), coingecko=CoinGecko())
        artifact = provider.fetch_market_context({"symbols": ["SPY", "QQQ", "IWM", "SOXX", "VIX", "US10Y", "DXY", "WTI", "BTC", "ETH"]})

        self.assertEqual(artifact.payload["assets"]["DXY"]["sync_group"], "fx")
        self.assertEqual(artifact.payload["assets"]["SPY"]["sync_group"], "exchange")
        self.assertEqual(artifact.payload["assets"]["WTI"]["sync_group"], "daily")

        gate = MarketContextGate().evaluate(
            artifact.payload,
            EffectiveRuleSet(),
            evaluation_time=datetime(2026, 8, 30, 12, 7, 23, tzinfo=timezone.utc),
        )
        self.assertEqual(gate.decision.value, "PASS")
        self.assertTrue(gate.core_input_complete)


if __name__ == "__main__":
    unittest.main()
