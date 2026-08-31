from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from stock_agent.alpha_bootstrap import (
    ALPHA_DISCOVERY_VERSION,
    AlphaCompositeLiveMarketContextProvider,
    AlphaProductionStockAgent,
    AlphaTechnicalFeatureCalculator,
    _alpha_selection,
    _grounded_catalyst,
    install_alpha_discovery_policy,
)
from stock_agent.models import EffectiveRuleSet, RawArtifact, RunMode, canonical_hash


STAMP = "2026-08-31T12:00:00Z"


def _artifact(name: str, payload: dict, subject: str | None = None) -> RawArtifact:
    return RawArtifact(name, "test", "TEST", subject, STAMP, payload, canonical_hash(payload), STAMP, STAMP)


class _Screener:
    def __init__(self, rows):
        self.rows = rows

    def fetch_universe(self, query):
        payload = {"securities": [dict(row) for row in self.rows], "source": [{"provider": "test-screener", "asof": STAMP}]}
        return _artifact("screen", payload)


class _Toss:
    base_url = "https://toss.test"

    def __init__(self, quote_volumes):
        self.quote_volumes = quote_volumes
        self.candle_calls = []

    def fetch_prices(self, symbols):
        result = []
        for symbol in symbols:
            result.append({
                "symbol": symbol,
                "lastPrice": 10.0,
                "volume": self.quote_volumes[symbol],
                "timestamp": STAMP,
                "currency": "USD",
            })
        return _artifact("prices-" + str(len(symbols)), {"result": result})

    def fetch_candles(self, ticker, interval, count):
        self.candle_calls.append(ticker)
        # True historical ADV is deliberately different from the one-day quote
        # hint, proving that the hint itself never becomes authoritative ADV.
        candles = [
            {"timestamp": f"2026-08-{day:02d}T20:00:00Z", "closePrice": 10.0 + day / 100.0, "volume": 2_000_000 + day * 1000}
            for day in range(1, 31)
        ]
        return _artifact("candle-" + ticker, {"result": candles}, ticker)


class AlphaDiscoveryPolicyTests(unittest.TestCase):
    def test_alpha_selection_prioritizes_turnover_and_is_deterministic(self):
        rows = []
        for index in range(80):
            rows.append({
                "security_id": f"T{index:03d}",
                "market_cap": 500_000_000 + index * 50_000_000,
                "approximate_dollar_volume": 12_000_000 + index * 100_000,
            })
        # Give one mid-cap an extreme turnover anomaly without making it the
        # largest market-cap or alphabetically special name.
        rows[47]["market_cap"] = 500_000_000
        rows[47]["approximate_dollar_volume"] = 250_000_000
        first, counts = _alpha_selection([dict(row) for row in rows], 30, "2026-08-31")
        second, _ = _alpha_selection([dict(row) for row in rows], 30, "2026-08-31")
        first_ids = [row["security_id"] for row in first]
        self.assertIn("T047", first_ids[:5])
        self.assertEqual(first_ids, [row["security_id"] for row in second])
        self.assertEqual(sum(counts.values()), 30)

    def test_live_provider_spends_bounded_alpha_probe_budget_not_alpha_rotation(self):
        rows = []
        quote_volumes = {}
        for index in range(80):
            sid = f"T{index:03d}"
            cap = 500_000_000 + index * 10_000_000
            volume = 1_200_000 + index * 10_000
            rows.append({
                "security_id": sid,
                "ticker": sid,
                "issuer_name": sid + " Inc.",
                "venue": "NASDAQ",
                "market": "NASDAQ",
                "security_type": "COMMON_STOCK",
                "currency": "USD",
                "price": 10.0,
                "market_cap": cap,
                "source_observed_at": STAMP,
            })
            quote_volumes[sid] = volume
        quote_volumes["T047"] = 30_000_000
        rows[47]["market_cap"] = 500_000_000

        toss = _Toss(quote_volumes)
        provider = AlphaCompositeLiveMarketContextProvider(toss, screener=_Screener(rows))
        artifact = provider.fetch_universe({
            "broad": True,
            "markets": ["NASDAQ"],
            "min_price": 3,
            "min_market_cap": 300_000_000,
            "min_average_dollar_volume": 10_000_000,
            "alpha_probe_limit": 30,
            "technical_count": 30,
            "liquidity_rotation_key": "2026-08-31",
        })
        payload = artifact.payload
        self.assertEqual(payload["alpha_discovery_version"], ALPHA_DISCOVERY_VERSION)
        self.assertEqual(payload["probe_strategy"], "ALPHA_TURNOVER_LIQUIDITY_HASH_EXPLORATION_V1")
        self.assertLessEqual(payload["probe_count"], 30)
        self.assertGreaterEqual(payload["probe_count"], 29)
        anomalous = next(row for row in payload["securities"] if row["security_id"] == "T047")
        self.assertEqual(anomalous["liquidity_status"], "FULL_CANDLE")
        self.assertGreater(anomalous["average_dollar_volume"], 10_000_000)
        self.assertIsNotNone(anomalous["quote_turnover_proxy"])
        unprobed = next(row for row in payload["securities"] if row["liquidity_status"] == "QUOTE_SINGLE_DAY_ESTIMATE")
        self.assertIn("approximate_dollar_volume", unprobed)
        self.assertNotIn("average_dollar_volume", unprobed)

    def test_alpha_technical_features_surface_crisis_relative_strength(self):
        class Market:
            alpha_benchmark_return_window = -0.12
        calc = AlphaTechnicalFeatureCalculator(Market())
        prices = [10.0 + index * 0.08 for index in range(31)]
        volumes = [1_000_000.0] * 30 + [2_500_000.0]
        result = calc.calculate("WIN", prices, volumes, STAMP, ("A",))
        self.assertGreater(result.features["benchmark_relative_return"], 0.20)
        self.assertEqual(result.features["alpha_signal_class"], "CRISIS_RELATIVE_STRENGTH")
        self.assertGreater(result.features["volume_ratio_20"], 2.0)

    def test_grounded_catalyst_requires_real_quantification(self):
        good = {
            "source_class": "COMPANY_IR",
            "source_url": "https://example.com/news/contract",
            "source_observed_at": "2026-08-31T10:00:00Z",
            "title": "Company awarded a contract valued at $250 million",
            "content": "The contract award is expected to contribute to backlog. Delivery begins September 15, 2026.",
        }
        catalyst = _grounded_catalyst(good)
        self.assertIsNotNone(catalyst)
        self.assertEqual(catalyst["event_type"], "CONTRACT_AWARD")
        self.assertEqual(catalyst["verification_status"], "OFFICIAL")
        self.assertEqual(catalyst["economic_transmission"]["amount"], 250_000_000)
        self.assertEqual(catalyst["event_at"], "2026-09-15T00:00:00Z")

        no_number = dict(good)
        no_number["title"] = "Company awarded a new contract"
        no_number["content"] = "Management described the award as strategically important."
        self.assertIsNone(_grounded_catalyst(no_number))

    def test_mou_is_structured_but_remains_non_binding(self):
        payload = {
            "source_class": "MAJOR_MEDIA",
            "source_url": "https://example.com/mou",
            "source_observed_at": "2026-08-31T10:00:00Z",
            "title": "Company signs memorandum of understanding for $100 million project",
            "content": "The MOU describes a potential project.",
        }
        catalyst = _grounded_catalyst(payload)
        self.assertIsNotNone(catalyst)
        self.assertEqual(catalyst["event_type"], "MOU")
        self.assertEqual(catalyst["binding_status"], "NOT_BINDING")

    def test_hunt_freshness_expands_but_execution_research_does_not(self):
        class Store:
            def resolve_rule_set(self, override_id):
                return EffectiveRuleSet()
        agent = AlphaProductionStockAgent.__new__(AlphaProductionStockAgent)
        agent.store = Store()
        agent._alpha_active_mode = RunMode.HUNT_ONLY
        hunt = agent._rules({})
        self.assertEqual(hunt.max_age_sec_hours, 24 * 120)
        self.assertEqual(hunt.max_age_research_hours, 24 * 45)

        agent._alpha_active_mode = RunMode.HUNT_AND_EXECUTION_REVIEW
        execution = agent._rules({})
        self.assertEqual(execution.max_age_sec_hours, 24 * 120)
        self.assertEqual(execution.max_age_research_hours, EffectiveRuleSet().max_age_research_hours)

    def test_install_is_idempotent(self):
        install_alpha_discovery_policy()
        install_alpha_discovery_policy()
        from stock_agent import adapters, runtime
        self.assertIs(adapters.CompositeLiveMarketContextProvider, AlphaCompositeLiveMarketContextProvider)
        self.assertIs(runtime.ProductionStockAgent, AlphaProductionStockAgent)


if __name__ == "__main__":
    unittest.main()
