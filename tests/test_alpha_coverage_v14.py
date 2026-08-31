from __future__ import annotations

import unittest

from stock_agent.alpha_coverage_v14 import (
    ALPHA_COVERAGE_VERSION,
    AlphaCoverageLiveMarketContextProvider,
    _selection_v14,
)
from stock_agent.adapters import ProviderError
from stock_agent.models import RawArtifact, canonical_hash


STAMP = "2026-08-31T13:17:00Z"  # 09:17 EDT: premarket-like live timing


def _artifact(name: str, payload: dict, subject: str | None = None) -> RawArtifact:
    return RawArtifact(name, "test", "TEST", subject, STAMP, payload, canonical_hash(payload), STAMP, STAMP)


class _Screener:
    def __init__(self, rows):
        self.rows = rows

    def fetch_universe(self, query):
        return _artifact("screen", {
            "securities": [dict(row) for row in self.rows],
            "source": [{"provider": "test-screener", "asof": STAMP}],
        })


class _Toss:
    base_url = "https://toss.test"

    def __init__(self, quote_volumes, *, fail_after: int | None = None):
        self.quote_volumes = quote_volumes
        self.fail_after = fail_after
        self.candle_calls: list[str] = []

    def fetch_prices(self, symbols):
        return _artifact("prices-" + str(len(symbols)), {"result": [
            {
                "symbol": symbol,
                "lastPrice": 10.0,
                "volume": self.quote_volumes.get(symbol, 0),
                "timestamp": STAMP,
                "currency": "USD",
            }
            for symbol in symbols
        ]})

    def fetch_candles(self, ticker, interval, count):
        self.candle_calls.append(ticker)
        if self.fail_after is not None and len(self.candle_calls) > self.fail_after:
            raise ProviderError("synthetic candle outage")
        candles = [
            {
                "timestamp": f"2026-08-{day:02d}T20:00:00Z",
                "closePrice": 10.0 + day / 100.0,
                "volume": 2_000_000 + day * 1000,
            }
            for day in range(1, 31)
        ]
        return _artifact("candle-" + ticker, {"result": candles}, ticker)


def _rows(count: int = 80):
    return [
        {
            "security_id": f"T{index:03d}",
            "ticker": f"T{index:03d}",
            "issuer_name": f"T{index:03d} Inc.",
            "venue": "NASDAQ",
            "market": "NASDAQ",
            "security_type": "COMMON_STOCK",
            "currency": "USD",
            "price": 10.0,
            "market_cap": 500_000_000 + index * 10_000_000,
            "source_observed_at": STAMP,
        }
        for index in range(count)
    ]


class AlphaCoverageV14Tests(unittest.TestCase):
    def test_premarket_low_same_day_volume_cannot_collapse_30_probe_budget(self):
        rows = _rows()
        # Every same-day quote is far below the $10M ADV hard threshold.
        # V1.3 incorrectly used this as a probe eligibility gate and collapsed
        # to the base provider's single historical probe.
        quote_volumes = {row["security_id"]: 1_000 for row in rows}
        toss = _Toss(quote_volumes)
        provider = AlphaCoverageLiveMarketContextProvider(toss, screener=_Screener(rows))

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
        self.assertEqual(payload["alpha_discovery_version"], ALPHA_COVERAGE_VERSION)
        self.assertEqual(payload["probe_strategy"], "ALPHA_V14_SESSION_ROBUST_BUDGET_FILL")
        self.assertEqual(payload["probe_target"], 30)
        self.assertEqual(payload["probe_count"], 30)
        self.assertEqual(payload["probe_success_count"], 30)
        self.assertEqual(payload["coverage_status"], "PASS")
        self.assertEqual(payload["quote_signal_regime"], "THIN_OR_PREMARKET")
        self.assertEqual(len(toss.candle_calls), 30)

    def test_selection_fills_from_rows_with_no_quote_volume_hint(self):
        rows = _rows(50)
        selected, meta = _selection_v14(rows, 30, "2026-08-31", 10_000_000)
        self.assertEqual(len(selected), 30)
        self.assertEqual(len({row["security_id"] for row in selected}), 30)
        self.assertEqual(meta["quote_signal_regime"], "THIN_OR_PREMARKET")
        self.assertEqual(meta["turnover"], 0)
        self.assertEqual(meta["liquidity"], 0)
        self.assertEqual(meta["exploration"], 30)

    def test_same_day_quote_proxy_never_becomes_authoritative_adv(self):
        rows = _rows()
        quote_volumes = {row["security_id"]: 1_000 for row in rows}
        provider = AlphaCoverageLiveMarketContextProvider(_Toss(quote_volumes), screener=_Screener(rows))
        payload = provider.fetch_universe({
            "broad": True,
            "min_price": 3,
            "min_market_cap": 300_000_000,
            "min_average_dollar_volume": 10_000_000,
            "alpha_probe_limit": 30,
            "technical_count": 30,
            "liquidity_rotation_key": "2026-08-31",
        }).payload
        unprobed = next(
            row for row in payload["securities"]
            if row["security_id"] not in {r["security_id"] for r in payload["securities"] if r.get("average_dollar_volume") is not None}
        )
        self.assertLess(unprobed.get("approximate_dollar_volume", 0), 10_000_000)
        self.assertNotIn("average_dollar_volume", unprobed)

    def test_major_candle_failure_is_not_reported_as_clean_no_opportunity(self):
        rows = _rows()
        quote_volumes = {row["security_id"]: 1_000 for row in rows}
        provider = AlphaCoverageLiveMarketContextProvider(
            _Toss(quote_volumes, fail_after=5),
            screener=_Screener(rows),
        )
        with self.assertRaisesRegex(ProviderError, "historical ADV coverage degraded"):
            provider.fetch_universe({
                "broad": True,
                "min_price": 3,
                "min_market_cap": 300_000_000,
                "min_average_dollar_volume": 10_000_000,
                "alpha_probe_limit": 30,
                "technical_count": 30,
                "liquidity_rotation_key": "2026-08-31",
            })

    def test_budget_is_bounded_by_available_price_cap_universe(self):
        rows = _rows(12)
        quote_volumes = {row["security_id"]: 0 for row in rows}
        provider = AlphaCoverageLiveMarketContextProvider(_Toss(quote_volumes), screener=_Screener(rows))
        payload = provider.fetch_universe({
            "broad": True,
            "min_price": 3,
            "min_market_cap": 300_000_000,
            "min_average_dollar_volume": 10_000_000,
            "alpha_probe_limit": 30,
            "technical_count": 30,
            "liquidity_rotation_key": "2026-08-31",
        }).payload
        self.assertEqual(payload["probe_target"], 12)
        self.assertEqual(payload["probe_count"], 12)
        self.assertEqual(payload["probe_success_count"], 12)


if __name__ == "__main__":
    unittest.main()
