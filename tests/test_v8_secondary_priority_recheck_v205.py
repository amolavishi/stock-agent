from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest
from types import SimpleNamespace

from stock_agent.models import RawArtifact, canonical_hash
from stock_agent.v8_secondary_priority_recheck_v205 import (
    V8_SECONDARY_PRIORITY_RECHECK_VERSION,
    _priority_enrich,
    active_high_secondary_ids,
    inject_secondary_priority_query,
)


class SecondaryPriorityRecheckTests(unittest.TestCase):
    def test_broad_query_gets_additional_priority_ids_without_becoming_bounded(self):
        data = {"universe_query": {"broad": True, "alpha_probe_limit": 1000}}
        patched, bounded = inject_secondary_priority_query(data, ["BBB", "AAA", "AAA"])
        self.assertFalse(bounded)
        query = patched["universe_query"]
        self.assertEqual(query["secondary_priority_tickers"], ["AAA", "BBB"])
        self.assertEqual(query["alpha_probe_limit"], 1000)
        self.assertNotIn("symbols", query)
        self.assertNotIn("tickers", query)

    def test_bounded_query_is_not_rewritten(self):
        data = {"universe_query": {"symbols": ["XYZ"]}}
        patched, bounded = inject_secondary_priority_query(data, ["AAA"])
        self.assertTrue(bounded)
        self.assertNotIn("secondary_priority_tickers", patched["universe_query"])

    def test_expired_high_secondary_is_not_reinjected(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE discovery_secondary_queue (security_id TEXT PRIMARY KEY, research_value TEXT, expiry TEXT, status TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO discovery_secondary_queue VALUES ('OLD','HIGH','2020-01-01T00:00:00Z','OPEN','2020-01-01T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO discovery_secondary_queue VALUES ('LIVE','HIGH','2099-01-01T00:00:00Z','OPEN','2026-09-02T00:00:00Z')"
        )
        store = SimpleNamespace(connection=connection)
        self.assertEqual(active_high_secondary_ids(store), ["LIVE"])
        status = connection.execute("SELECT status FROM discovery_secondary_queue WHERE security_id='OLD'").fetchone()["status"]
        self.assertEqual(status, "EXPIRED_WATCH")
        connection.close()

    def test_priority_probe_is_additive_and_materializes_verified_adv(self):
        class Toss:
            base_url = "https://example.test"
            def fetch_candles(self, sid, interval, count):
                payload = {"result": {"candles": [
                    {"closePrice": "10", "volume": "2000000", "timestamp": "2026-09-01T00:00:00Z"},
                    {"closePrice": "11", "volume": "2200000", "timestamp": "2026-09-02T00:00:00Z"},
                ]}}
                return RawArtifact(
                    "CANDLE-1", "toss", "CANDLES", sid, "2026-09-02T00:00:00Z",
                    payload, canonical_hash(payload), "2026-09-02T00:00:00Z", "2026-09-02T00:00:01Z",
                )

        provider = SimpleNamespace(toss=Toss(), last_secondary_priority_recheck=None)
        payload = {
            "securities": [{
                "security_id": "AAA", "ticker": "AAA", "price": 11.0,
                "market_cap": 500_000_000.0, "approximate_dollar_volume": 25_000_000.0,
                "liquidity_status": "QUOTE_SINGLE_DAY_ESTIMATE",
            }],
            "probe_limit": 1000,
            "probe_count": 1000,
            "probe_errors": [],
        }
        artifact = RawArtifact(
            "U1", "composite-live-market-alpha-v13", "UNIVERSE", None,
            "2026-09-02T00:00:00Z", payload, canonical_hash(payload),
            "2026-09-02T00:00:00Z", "2026-09-02T00:00:01Z",
        )
        query = {
            "broad": True, "alpha_probe_limit": 1000,
            "min_price": 3.0, "min_market_cap": 300_000_000.0,
            "min_average_dollar_volume": 10_000_000.0, "technical_count": 100,
        }
        enriched = _priority_enrich(provider, artifact, query, ["AAA"])
        row = enriched.payload["securities"][0]
        receipt = enriched.payload["secondary_priority_recheck"][0]
        self.assertEqual(enriched.payload["probe_limit"], 1000)
        self.assertEqual(receipt["status"], "ADV_PASS")
        self.assertTrue(receipt["extra_priority_probe_executed"])
        self.assertGreater(row["average_dollar_volume"], 10_000_000)
        self.assertEqual(row["liquidity_status"], "FULL_CANDLE")
        self.assertTrue(row["secondary_priority_recheck"])
        self.assertFalse(provider.last_secondary_priority_recheck["broad_budget_reduced"])
        self.assertEqual(provider.last_secondary_priority_recheck["base_probe_limit"], 1000)

    def test_production_composition_installs_recheck_layer(self):
        code = (
            "import json; from stock_agent.production import production_composition; "
            "c=production_composition(); print(json.dumps({'v':c.get('v8_secondary_priority_recheck_version'),"
            "'weak':c.get('discovery_recall_lite_runtime_installed'),'cls':c.get('runtime_class')}))"
        )
        completed = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, check=True)
        data = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(data["v"], V8_SECONDARY_PRIORITY_RECHECK_VERSION)
        self.assertFalse(data["weak"])
        self.assertEqual(data["cls"], "V8SecondaryPriorityRecheckProductionStockAgent")


if __name__ == "__main__":
    unittest.main()
