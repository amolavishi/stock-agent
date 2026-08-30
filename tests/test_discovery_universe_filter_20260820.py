from __future__ import annotations

import json
import unittest

from stock_agent.discovery import deterministic_universe_prefilter
from stock_agent.models import RunMode
from tests.test_adversarial_provider_integration import fixture, strict_agent


class DeterministicUniversePrefilterTests(unittest.TestCase):
    def test_explicit_thresholds_are_cumulative(self):
        rows = [
            {"security_id": "PASS", "ticker": "PASS", "issuer_name": "Pass", "venue": "NASDAQ", "price": 10, "market_cap": 500_000_000, "average_dollar_volume": 20_000_000},
            {"security_id": "LOWP", "ticker": "LOWP", "issuer_name": "Low Price", "venue": "NASDAQ", "price": 2, "market_cap": 500_000_000, "average_dollar_volume": 20_000_000},
            {"security_id": "LOWC", "ticker": "LOWC", "issuer_name": "Low Cap", "venue": "NYSE", "price": 10, "market_cap": 200_000_000, "average_dollar_volume": 20_000_000},
            {"security_id": "LOWA", "ticker": "LOWA", "issuer_name": "Low ADV", "venue": "AMEX", "price": 10, "market_cap": 500_000_000, "average_dollar_volume": 5_000_000},
        ]
        result = deterministic_universe_prefilter(rows, min_price=3, min_market_cap=300_000_000, min_average_dollar_volume=10_000_000)
        self.assertEqual(result["counts"], {"RAW_UNIVERSE": 4, "SUPPORTED_SECURITY": 4, "PRICE_FILTER": 3, "MARKET_CAP_FILTER": 2, "ADV_FILTER": 1})
        self.assertEqual(result["eligible_security_ids"], ["PASS"])
        by_sid = {row["security_id"]: row for row in result["evaluations"]}
        self.assertIn("UNIVERSE_LOW_PRICE", by_sid["LOWP"]["reason_codes"])
        self.assertIn("UNIVERSE_LOW_MARKET_CAP", by_sid["LOWC"]["reason_codes"])
        self.assertIn("UNIVERSE_LOW_LIQUIDITY", by_sid["LOWA"]["reason_codes"])

    def test_missing_market_data_fails_closed(self):
        rows = [{"security_id": "MISS", "ticker": "MISS", "issuer_name": "Missing", "venue": "NASDAQ", "price": 10}]
        result = deterministic_universe_prefilter(rows, min_price=3, min_market_cap=300_000_000, min_average_dollar_volume=10_000_000)
        entry = result["evaluations"][0]
        self.assertFalse(entry["eligible"])
        self.assertEqual(entry["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("market_cap", entry["unknown_fields"])
        self.assertEqual(result["counts"]["MARKET_CAP_FILTER"], 0)

    def test_market_cap_and_adv_can_use_provider_economic_inputs(self):
        rows = [{
            "security_id": "DERIVED", "ticker": "DERIVED", "issuer_name": "Derived", "venue": "NASDAQ",
            "price": 10, "shares_outstanding": 50_000_000, "average_volume": 2_000_000,
        }]
        result = deterministic_universe_prefilter(rows, min_price=3, min_market_cap=300_000_000, min_average_dollar_volume=10_000_000)
        entry = result["evaluations"][0]
        self.assertTrue(entry["eligible"])
        self.assertEqual(entry["market_cap"], 500_000_000)
        self.assertEqual(entry["average_dollar_volume"], 20_000_000)
        self.assertEqual(entry["market_cap_source"], "shares_outstanding*price")
        self.assertEqual(entry["average_dollar_volume_source"], "average_volume*price")

    def test_unsupported_venue_is_excluded_before_price(self):
        rows = [{"security_id": "OTC", "ticker": "OTC", "issuer_name": "OTC", "venue": "OTC", "price": 10, "market_cap": 1_000_000_000, "average_dollar_volume": 50_000_000}]
        result = deterministic_universe_prefilter(rows, min_price=3, min_market_cap=300_000_000, min_average_dollar_volume=10_000_000)
        entry = result["evaluations"][0]
        self.assertFalse(entry["eligible"])
        self.assertIn("UNIVERSE_UNSUPPORTED_SECURITY", entry["reason_codes"])
        self.assertEqual(result["counts"]["SUPPORTED_SECURITY"], 0)
        self.assertEqual(result["counts"]["PRICE_FILTER"], 0)


class StrictDiscoveryFunnelIntegrationTests(unittest.TestCase):
    @staticmethod
    def _funnel(agent, run_id):
        rows = agent.store.connection.execute(
            "SELECT funnel_stage,count,details_json FROM discovery_funnel WHERE run_id=? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return {row["funnel_stage"]: {"count": row["count"], "details": json.loads(row["details_json"])} for row in rows}

    def test_strict_hunt_filters_before_technical_and_discovery(self):
        data = fixture()
        row = data["provider_recordings"]["candidates"][0]
        row["market_cap"] = 500_000_000
        row["average_dollar_volume"] = 20_000_000
        agent = strict_agent(data)
        try:
            outcome = agent.run(RunMode.HUNT_ONLY, {})
            self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
            funnel = self._funnel(agent, outcome.run_id)
            for stage in ("RAW_UNIVERSE", "SUPPORTED_SECURITY", "PRICE_FILTER", "MARKET_CAP_FILTER", "ADV_FILTER"):
                self.assertEqual(funnel[stage]["count"], 1)
            artifact_types = {row[0] for row in agent.store.connection.execute("SELECT artifact_type FROM raw_artifacts")}
            self.assertIn("UNIVERSE_FILTER_RESULT", artifact_types)
            self.assertIn("FILTERED_UNIVERSE", artifact_types)
            self.assertIn("STAGE_DISCOVERY_READY", funnel)
            self.assertEqual(funnel["CATALYST_PASS"]["count"], 1)
            self.assertEqual(funnel["STAGE_DISCOVERY_READY"]["details"].get("catalyst_status"), "NOT_EVALUATED")
        finally:
            agent.close()

    def test_low_price_never_reaches_stock_discovery(self):
        data = fixture()
        row = data["provider_recordings"]["candidates"][0]
        row["prices"] = [2.0, 2.0, 2.0]
        row["market_cap"] = 500_000_000
        row["average_dollar_volume"] = 20_000_000
        agent = strict_agent(data)
        try:
            outcome = agent.run(RunMode.HUNT_ONLY, {})
            self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")
            self.assertEqual(outcome.blocked_reason, "UNIVERSE_FILTER")
            funnel = self._funnel(agent, outcome.run_id)
            self.assertEqual(funnel["PRICE_FILTER"]["count"], 0)
            self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) FROM work_items WHERE stage='STOCK_DISCOVERY'").fetchone()[0], 0)
            filter_payload = json.loads(agent.store.connection.execute("SELECT payload_json FROM raw_artifacts WHERE artifact_type='UNIVERSE_FILTER_RESULT'").fetchone()[0])
            self.assertIn("UNIVERSE_LOW_PRICE", filter_payload["evaluations"][0]["reason_codes"])
        finally:
            agent.close()

    def test_missing_market_cap_never_reaches_stock_discovery(self):
        data = fixture()
        row = data["provider_recordings"]["candidates"][0]
        row.pop("market_cap", None)
        row.pop("shares_outstanding", None)
        row["average_dollar_volume"] = 20_000_000
        agent = strict_agent(data)
        try:
            outcome = agent.run(RunMode.HUNT_ONLY, {})
            self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")
            funnel = self._funnel(agent, outcome.run_id)
            self.assertEqual(funnel["MARKET_CAP_FILTER"]["count"], 0)
            filter_payload = json.loads(agent.store.connection.execute("SELECT payload_json FROM raw_artifacts WHERE artifact_type='UNIVERSE_FILTER_RESULT'").fetchone()[0])
            self.assertEqual(filter_payload["evaluations"][0]["status"], "INSUFFICIENT_EVIDENCE")
            self.assertIn("market_cap", filter_payload["evaluations"][0]["unknown_fields"])
        finally:
            agent.close()


if __name__ == "__main__":
    unittest.main()

