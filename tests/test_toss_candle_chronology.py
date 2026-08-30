from __future__ import annotations

import unittest

from stock_agent.adapters import CompositeLiveMarketContextProvider, _close_series
from stock_agent.models import RawArtifact, canonical_hash

STAMP = "2026-08-30T12:00:00Z"
NEWEST = "2026-08-28T09:00:00-04:00"
OLDER = "2026-08-27T09:00:00-04:00"


def artifact(artifact_id, artifact_type, payload, observed_at=NEWEST):
    return RawArtifact(artifact_id, "fixture", artifact_type, None, observed_at, payload, canonical_hash(payload), observed_at, STAMP)


class TossCandleChronologyTests(unittest.TestCase):
    def test_close_series_sorts_timestamped_newest_first_payload(self):
        payload = {"result": {"candles": [
            {"timestamp": NEWEST, "closePrice": "120", "volume": "2000"},
            {"timestamp": OLDER, "closePrice": "100", "volume": "1000"},
        ]}}
        self.assertEqual(_close_series(payload), [100.0, 120.0])
        self.assertEqual(payload["result"]["candles"][0]["timestamp"], NEWEST)

    def test_broad_universe_stores_chronological_prices_and_volumes(self):
        rows = [{"security_id": "AAA", "ticker": "AAA", "venue": "NASDAQ", "price": 120.0, "market_cap": 1_000_000_000}]
        class Screener:
            def fetch_universe(self, query):
                payload = {"securities": [dict(row) for row in rows], "source": [{"provider": "fixture"}]}
                return artifact("screen", "UNIVERSE", payload)
        class Toss:
            base_url = "https://toss.test"
            def fetch_prices(self, symbols):
                return artifact("prices", "PRICES", {"result": [{"symbol": "AAA", "lastPrice": "120", "timestamp": NEWEST, "currency": "USD"}]})
            def fetch_candles(self, ticker, interval, count):
                return artifact("candles", "CANDLES", {"result": {"candles": [
                    {"timestamp": NEWEST, "closePrice": "120", "volume": "2000000"},
                    {"timestamp": OLDER, "closePrice": "100", "volume": "1000000"},
                ]}})
        provider = CompositeLiveMarketContextProvider(Toss(), screener=Screener())
        result = provider.fetch_universe({"broad": True, "min_price": 3, "min_market_cap": 300_000_000, "min_average_dollar_volume": 10_000_000, "liquidity_full_probe_limit": 1, "liquidity_rotation_key": "2026-08-30"})
        row = result.payload["securities"][0]
        self.assertEqual(row["prices"], [100.0, 120.0])
        self.assertEqual(row["volumes"], [1_000_000.0, 2_000_000.0])
        self.assertEqual(row["average_volume"], 1_500_000.0)

    def test_explicit_universe_stores_chronological_prices_and_volumes(self):
        class Toss:
            base_url = "https://toss.test"
            def fetch_universe(self, query):
                return artifact("universe", "UNIVERSE", {"securities": [{"security_id": "AAA", "ticker": "AAA", "venue": "NASDAQ", "currency": "USD"}]})
            def fetch_prices(self, symbols):
                return artifact("prices", "PRICES", {"result": [{"symbol": "AAA", "lastPrice": "120", "timestamp": NEWEST, "currency": "USD"}]})
            def fetch_candles(self, ticker, interval, count):
                return artifact("candles", "CANDLES", {"result": {"candles": [
                    {"timestamp": NEWEST, "closePrice": "120", "volume": "2000000"},
                    {"timestamp": OLDER, "closePrice": "100", "volume": "1000000"},
                ]}})
        class Yahoo:
            def fetch_market_cap(self, symbol):
                return {"market_cap": 1_000_000_000.0, "market_cap_observed_at": "2026-08-28T00:00:00Z", "market_cap_source": "https://example.test/cap", "market_cap_provider": "fixture"}
        provider = CompositeLiveMarketContextProvider(Toss(), yahoo=Yahoo())
        result = provider.fetch_universe({"symbols": ["AAA"], "technical_count": 2})
        row = result.payload["securities"][0]
        self.assertEqual(row["prices"], [100.0, 120.0])
        self.assertEqual(row["volumes"], [1_000_000.0, 2_000_000.0])
        self.assertEqual(row["average_volume"], 1_500_000.0)


if __name__ == "__main__":
    unittest.main()
