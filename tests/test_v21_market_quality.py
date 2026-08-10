from __future__ import annotations

import unittest

from stock_agent.certification import RequiredDataContract
from stock_agent.market_quality import assess_market_quality
from stock_agent.lineage import build_material_numeric_lineage, validate_material_numeric_lineage
from stock_agent.schemas import MarketSnapshot, TradePlan


class MarketQualityTests(unittest.TestCase):
    def test_inod_sunday_bar_cannot_certify_relative_volume_or_stage(self):
        quality = assess_market_quality(
            api_received_at="2026-08-10T03:01:00+00:00",
            provider_observed_at="2026-08-10T03:01:00+00:00",
            bar_end_at="2026-08-09T23:01:00-04:00",
            volume=10_349, average_volume=1_297_102)
        self.assertEqual(quality.market_session, "CLOSED")
        self.assertEqual(quality.bar_completeness, "INCOMPLETE")
        self.assertEqual(quality.volume_validity, "INVALID")
        self.assertEqual(quality.indicator_readiness, "UNCERTIFIED")
        self.assertEqual(quality.data_quality, "LOW")

    def test_completed_friday_bar_is_valid_on_sunday(self):
        quality = assess_market_quality(
            api_received_at="2026-08-10T03:01:00+00:00",
            provider_observed_at="2026-08-10T03:01:00+00:00",
            bar_end_at="2026-08-07T16:00:00-04:00",
            volume=1_100_000, average_volume=1_000_000)
        self.assertTrue(quality.certifiable)
        self.assertEqual(quality.bar_completeness, "COMPLETE")

    def test_uncertified_indicator_forces_stage_and_market_blocker(self):
        market = MarketSnapshot("INOD", "2026-08-10T03:01:00+00:00", 63, 0, 0, -10,
            10_349, 1_297_102, 1_000_000_000, 62, 79, 5, stage="STAGE_1",
            source="TOSS_OPEN_API", data_quality="LOW", is_mock=False,
            transport_status="OK", quote_freshness="FRESH", candle_freshness="FRESH",
            market_session="CLOSED", bar_completeness="INCOMPLETE",
            volume_validity="INVALID", indicator_readiness="UNCERTIFIED")
        self.assertEqual(market.stage, "UNCERTIFIED")
        self.assertFalse(market.relative_volume_certified)
        result = RequiredDataContract.assess(
            market, [], {}, live_mode=False, sizing_requested=False)
        self.assertIn("MARKET_DATA_NOT_CERTIFIABLE", result.failures)

    def test_material_numeric_claims_require_source_asof_and_method(self):
        with self.assertRaises(ValueError):
            validate_material_numeric_lineage([
                {"claim": "cash", "value": 100, "source": "", "as_of": "2026-08-10",
                 "method": "XBRL"}
            ])
        market = MarketSnapshot("INOD", "2026-08-07T20:00:00+00:00", 10, 1, 2, 3,
            1000, 900, 0, 9, 8, 1, source="TOSS", is_mock=False)
        plan = TradePlan(10, 9, 10, 8, 14, 18, 4, 2, 2)
        rows = build_material_numeric_lineage(market, plan, {})
        self.assertGreaterEqual(len(rows), 6)
        self.assertTrue(all(row["source"] and row["as_of"] and row["method"] for row in rows))


if __name__ == "__main__":
    unittest.main()
