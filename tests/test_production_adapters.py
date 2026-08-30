from __future__ import annotations

import unittest
import json
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root
from unittest.mock import patch

from stock_agent.adapters import RecordedMarketDataProvider, RecordedPortfolioProvider, RecordedResearchEvidenceProvider, RecordedSECProvider, HttpJsonSECProvider, NasdaqScreenerMarketDataProvider, CompositeLiveMarketContextProvider, ProviderError, YahooFinanceNewsEvidenceProvider, CompositeResearchEvidenceProvider
from stock_agent.gates import CapitalPrescreenGate, ContractViolation, PositionSizer
from stock_agent.models import EffectiveRuleSet, RunMode, RawArtifact, canonical_hash, utc_now, Evidence
from stock_agent.providers import FakeProvider
from stock_agent.runtime import ProductionStockAgent, StockAgentConfig


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = canonical_prompt_library_root()


def raw_rows(capital_complete: bool = True):
    keys = ["active_atm", "large_shelf_and_financing_need", "toxic_convertible", "material_warrant", "imminent_financing", "cash_runway_critical"]
    capital = {key: {"state": "FALSE", "details": {"summary": "clear", "evidence_ids": ["E1"], "unknowns": []}, "evidence_ids": ["E1"]} for key in keys}
    return [{"security_id": "SEC1", "ticker": "SEC1", "issuer_name": "Issuer", "venue": "NASDAQ", "cik": "0000000000", "sector": "TECH", "prices": [10.0, 10.5, 11.0], "market_cap": 550_000_000, "average_dollar_volume": 20_000_000, "evidence_ids": ["E1"], "capital_prescreen": {**capital, "complete": capital_complete}, "failure_paths": [{"category": category, "scenario": f"{category}-scenario", "causal_path": f"{category}-cause", "probability_direction": "INCREASES_DOWNSIDE", "severity": "MAJOR", "source_evidence_ids": ["E1"]} for category in ("FUNDAMENTAL", "CAPITAL_STRUCTURE", "PRICING_EXPECTATION")] }]


















































def _complete_market_context_fixture():
    stamp = utc_now()
    symbols = ["SPY", "QQQ", "IWM", "SOXX", "VIX", "US10Y", "DXY", "WTI", "BTC", "ETH"]
    source = []
    for index, symbol in enumerate(symbols):
        source.append({
            "symbol": symbol,
            "observed_at": stamp,
            "source": "recorded-test",
            "payload": {"data": [{"close": 100.0 + index}, {"close": 102.0 + index}, {"close": 103.0 + index}]},
        })
    return {"source": source}


class ProductionAdapterTests(unittest.TestCase):
    def test_nasdaq_screener_normalizes_broad_rows_and_asof(self):
        provider = NasdaqScreenerMarketDataProvider()
        response = {
            "data": {"asof": "Last price as of Aug 27, 2026", "table": {"rows": [
                {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "lastsale": "$225.00", "marketCap": "3,400,000,000,000", "url": "/market-activity/stocks/aapl"},
                {"symbol": "bad symbol", "name": "ignored", "lastsale": "$1", "marketCap": "2"},
            ]}},
        }
        class _Response:
            url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=20&exchange=nasdaq"
            def read(self, *args):
                import json
                return json.dumps(response).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch("stock_agent.adapters.urllib.request.urlopen", return_value=_Response()):
            artifact = provider.fetch_universe({"markets": ["NASDAQ"], "limit": 20})
        self.assertEqual(artifact.provider, "nasdaq-screener")
        self.assertEqual(artifact.source_observed_at, "2026-08-27T00:00:00Z")
        self.assertEqual(len(artifact.payload["securities"]), 1)
        self.assertEqual(artifact.payload["securities"][0]["security_id"], "AAPL")
        self.assertEqual(artifact.payload["securities"][0]["currency"], "USD")

    def test_nasdaq_screener_rejects_future_asof(self):
        provider = NasdaqScreenerMarketDataProvider()
        response = {"data": {"asof": "Last price as of Aug 29, 2099", "table": {"rows": [{"symbol": "AAPL", "lastsale": "$1", "marketCap": "1000000000"}]}}}
        class _Response:
            url = provider.BASE_URL
            def read(self, *args):
                import json
                return json.dumps(response).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch("stock_agent.adapters.urllib.request.urlopen", return_value=_Response()):
            with self.assertRaises(ProviderError):
                provider.fetch_universe({"markets": ["NASDAQ"], "limit": 20})

    def test_composite_broad_liquidity_scans_midcap_quotes_not_market_cap_top_200(self):
        stamp = utc_now()
        rows = [
            {"security_id": "LARGE", "ticker": "LARGE", "venue": "NASDAQ", "price": 100.0, "market_cap": 50_000_000_000},
            {"security_id": "MID", "ticker": "MID", "venue": "NASDAQ", "price": 20.0, "market_cap": 1_000_000_000},
            {"security_id": "SMALL", "ticker": "SMALL", "venue": "NASDAQ", "price": 10.0, "market_cap": 600_000_000},
        ]
        class Screener:
            def fetch_universe(self, query):
                payload = {"securities": copy_rows, "source": [{"provider": "test-screener"}]}
                return RawArtifact("screen", "test-screener", "UNIVERSE", None, stamp, payload, canonical_hash(payload), stamp, stamp)
        copy_rows = [dict(row) for row in rows]
        class Toss:
            base_url = "https://toss.test"
            def fetch_prices(self, symbols):
                result = [{"symbol": symbol, "lastPrice": next(row["price"] for row in rows if row["security_id"] == symbol), "volume": 1_000_000, "timestamp": stamp, "currency": "USD"} for symbol in symbols]
                payload = {"result": result}
                return RawArtifact("prices-" + str(len(symbols)), "toss-test", "PRICES", None, stamp, payload, canonical_hash(payload), stamp, stamp)
            def fetch_candles(self, ticker, interval, count):
                payload = {"result": [{"closePrice": next(row["price"] for row in rows if row["security_id"] == ticker), "volume": 1_000_000}, {"closePrice": next(row["price"] for row in rows if row["security_id"] == ticker) + 1, "volume": 1_100_000}]}
                return RawArtifact("candle-" + ticker, "toss-test", "CANDLES", ticker, stamp, payload, canonical_hash(payload), stamp, stamp)
        provider = CompositeLiveMarketContextProvider(Toss(), screener=Screener())
        artifact = provider.fetch_universe({"broad": True, "min_price": 3, "min_market_cap": 500_000_000, "min_average_dollar_volume": 10_000_000, "liquidity_full_probe_limit": 2})
        self.assertEqual(artifact.payload["probe_strategy"], "BROAD_QUOTE_PRIORITY_PLUS_DAILY_ROTATION")
        self.assertEqual(artifact.payload["quote_scan_count"], 3)
        mid = next(row for row in artifact.payload["securities"] if row["security_id"] == "MID")
        self.assertEqual(mid["liquidity_status"], "FULL_CANDLE")
        self.assertGreater(mid["average_dollar_volume"], 10_000_000)


    def test_liquidity_rotation_changes_exploration_set_across_days(self):
        stamp = utc_now()
        rows = [
            {"security_id": f"T{index:02d}", "ticker": f"T{index:02d}", "venue": "NASDAQ", "price": 10.0 + index, "market_cap": 500_000_000 + index * 100_000_000}
            for index in range(12)
        ]
        class Screener:
            def fetch_universe(self, query):
                payload = {"securities": [dict(row) for row in rows], "source": [{"provider": "test-screener"}]}
                return RawArtifact("screen-rotation", "test-screener", "UNIVERSE", None, stamp, payload, canonical_hash(payload), stamp, stamp)
        class Toss:
            base_url = "https://toss.test"
            def __init__(self): self.candle_calls = []
            def fetch_prices(self, symbols):
                result = [{"symbol": symbol, "lastPrice": next(row["price"] for row in rows if row["security_id"] == symbol), "timestamp": stamp, "currency": "USD"} for symbol in symbols]
                payload = {"result": result}
                return RawArtifact("prices-rotation", "toss-test", "PRICES", None, stamp, payload, canonical_hash(payload), stamp, stamp)
            def fetch_candles(self, ticker, interval, count):
                self.candle_calls.append(ticker)
                payload = {"result": [{"closePrice": 10.0, "volume": 2_000_000}, {"closePrice": 10.5, "volume": 2_100_000}]}
                return RawArtifact("candle-" + ticker, "toss-test", "CANDLES", ticker, stamp, payload, canonical_hash(payload), stamp, stamp)
        toss = Toss(); provider = CompositeLiveMarketContextProvider(toss, screener=Screener())
        first = provider.fetch_universe({"broad": True, "min_price": 3, "min_market_cap": 300_000_000, "liquidity_full_probe_limit": 3, "liquidity_rotation_key": "2026-08-28"})
        first_calls = tuple(toss.candle_calls); toss.candle_calls.clear()
        second = provider.fetch_universe({"broad": True, "min_price": 3, "min_market_cap": 300_000_000, "liquidity_full_probe_limit": 3, "liquidity_rotation_key": "2026-08-29"})
        second_calls = tuple(toss.candle_calls)
        self.assertNotEqual(first_calls, second_calls)
        self.assertEqual(first.payload["liquidity_rotation_key"], "2026-08-28")
        self.assertEqual(second.payload["liquidity_rotation_key"], "2026-08-29")
        self.assertEqual(first.payload["liquidity_rotation_probe_count"], 3)

    def test_yahoo_news_normalizes_real_secondary_media_source(self):
        provider = YahooFinanceNewsEvidenceProvider(timeout=10)
        rss = """<?xml version='1.0' encoding='UTF-8'?>
        <rss version='2.0'><channel><item>
        <title>NVDA reports quarterly results</title>
        <link>https://finance.yahoo.com/news/nvda-results.html</link>
        <description>NVDA announced its latest quarterly results.</description>
        <pubDate>Thu, 27 Aug 2026 12:00:00 GMT</pubDate>
        </item></channel></rss>""".encode("utf-8")
        class _Response:
            url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA"
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, *args): return rss
        with patch("stock_agent.adapters.urllib.request.urlopen", return_value=_Response()):
            artifact = provider.fetch("NVDA", {})
        self.assertEqual(artifact.provider, "yahoo-finance-news")
        self.assertEqual(artifact.payload["source_class"], "MAJOR_MEDIA")
        self.assertTrue(artifact.payload["source_url"].startswith("https://finance.yahoo.com/"))
        self.assertEqual(artifact.payload["source_observed_at"], artifact.source_observed_at)
        self.assertEqual(artifact.payload["raw_artifact_id"], artifact.artifact_id)

    def test_composite_research_falls_back_to_real_secondary_provider(self):
        class Primary:
            def fetch(self, subject_id, query):
                raise ProviderError("not configured")

        class Secondary:
            def fetch(self, subject_id, query):
                payload = {"source_url": "https://finance.yahoo.com/a", "source_observed_at": "2026-08-27T00:00:00Z", "provider": "secondary", "content": "x"}
                return RawArtifact("a", "secondary", "RESEARCH_EVIDENCE", subject_id, payload["source_observed_at"], payload, "h", payload["source_observed_at"], "2026-08-27T00:01:00Z")

        artifact = CompositeResearchEvidenceProvider(Primary(), Secondary()).fetch("NVDA", {})
        self.assertEqual(artifact.provider, "secondary")

    def test_sec_cheap_facts_extracts_filing_signals(self):
        provider = HttpJsonSECProvider(user_agent="StockAgent Audit <audit@stockagent.test>")
        stamp = utc_now()
        submissions_payload = {"name": "Issuer", "filings": {"recent": {"form": ["S-3"], "accessionNumber": ["0000000000-26-000001"], "primaryDocument": ["recorded.htm"]}}}
        facts_payload = {"facts": {"us-gaap": {"ConvertibleDebtCurrent": {"units": {"USD": [{"val": 100}]}}}}}
        submissions = RawArtifact("sub", "sec", "SEC_SUBMISSIONS", "SEC1", stamp, submissions_payload, canonical_hash(submissions_payload), stamp, stamp)
        facts = RawArtifact("facts", "sec", "SEC_FACTS", "SEC1", stamp, facts_payload, canonical_hash(facts_payload), stamp, stamp)
        filing_payload = {"accession_number": "0000000000-26-000001", "form": "S-3", "primary_document": "recorded.htm", "document": "As of this filing, the at-the-market sales agreement remains in effect with $50 million of remaining available capacity. A convertible note remains outstanding with a variable-price conversion feature and conversion price reset based on market price. Warrants remain outstanding to purchase common stock at an exercise price of $8.50 per share."}
        filing = RawArtifact("filing", "sec", "SEC_FILING_DOCUMENT", "SEC1", stamp, filing_payload, canonical_hash(filing_payload), stamp, stamp)
        with patch.object(provider, "fetch_filings", return_value=filing):
            artifact = provider.fetch_cheap_facts({"security_id": "SEC1", "cik": "0000000000"}, submissions, facts)
        self.assertEqual(artifact.payload["active_atm"]["state"], "TRUE")
        self.assertEqual(artifact.payload["toxic_convertible"]["state"], "TRUE")
        self.assertEqual(artifact.payload["material_warrant"]["state"], "TRUE")
        self.assertIn("SEC_XBRL_AND_FIELD_LOCAL_FILING_TEXT_V2", artifact.payload["extraction_method"])
        self.assertEqual(artifact.source_observed_at, stamp)

    def make(self, rows=None):
        rows = rows or raw_rows()
        cheap = dict(rows[0].get("capital_prescreen") or {})
        cheap.pop("complete", None)
        market = RecordedMarketDataProvider({"market_context": _complete_market_context_fixture(), "candidates": rows, "market_execution": {"core_input_complete": True, "current_price": 10.0, "execution_stop": 9.0, "account_equity": 1000.0}})
        portfolio = RecordedPortfolioProvider({"as_of": "2026-08-18T00:00:00Z", "cash": 1000.0, "total_equity": 1000.0, "positions": []})
        config = StockAgentConfig(LIBRARY, Path(":memory:"), strict_inputs=True, market_data_provider=market, sec_provider=RecordedSECProvider({"SEC1": {"submissions": {"name": "Issuer", "filings": {"recent": {"form": ["10-K"], "accessionNumber": ["0000000000-26-000001"], "primaryDocument": ["recorded.htm"]}}}, "facts": {"facts": {"us-gaap": {"Revenue": {}}}}, "cheap_facts": {"extraction_status": "COMPLETE", **cheap}, "filings": {"accession_number": "0000000000-26-000001", "form": "10-K", "document": "recorded"}}}), portfolio_provider=portfolio, research_provider=RecordedResearchEvidenceProvider({"SEC1": {"source": "recorded", "content": "raw", "source_url": "https://issuer.example/ir", "catalysts": [{"catalyst_id": "C-RECORDED-1", "event_type": "EARNINGS", "event_date": "2026-09-15T20:00:00Z", "verification_status": "CONFIRMED", "binding_status": "NOT_APPLICABLE", "economic_transmission": {"metric": "revenue_growth_pct", "direction": "UP", "magnitude": 20.0, "unit": "percent"}, "confirmation_metric": "reported revenue and forward guidance"}], "valuation_inputs": {"valuation_basis": "EV_REVENUE", "metric_name": "FORWARD_REVENUE", "diluted_shares": 50000000, "net_cash": 50000000, "forward_metric_value": 100000000, "benchmark_multiple": 8.0, "benchmark_description": "recorded peer/consensus forward revenue benchmark"}, "economic_scenario": {"security_id": "SEC1", "bull_value": 14.0, "base_value": 10.5, "bear_value": 7.0, "bull_probability": 0.3, "base_probability": 0.5, "bear_probability": 0.2, "opportunity_cost_score": 0.1, "evidence_ids": ["E1"], "source_stage_lineage": ["DEEP_RESEARCH", "FULL_SEC_FORENSIC", "ADVERSARIAL_AUDIT", "PORTFOLIO_REVIEW"], "scenario_value_hash": canonical_hash({"security_id": "SEC1", "evidence_ids": ["E1"], "bull_value": 14.0, "base_value": 10.5, "bear_value": 7.0, "bull_probability": 0.3, "base_probability": 0.5, "bear_probability": 0.2, "opportunity_cost_score": 0.1, "source_stage_lineage": ["ADVERSARIAL_AUDIT", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "PORTFOLIO_REVIEW"]})}}}))
        return ProductionStockAgent(config, provider=FakeProvider())

    def test_strict_hunt_uses_provider_dag(self):
        agent = self.make(); outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
        self.assertIsNone(outcome.authoritative_action)
        self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) FROM raw_artifacts WHERE artifact_type='MARKET_CONTEXT_ASSET'").fetchone()[0], 10)
        self.assertEqual(agent.store.connection.execute("SELECT COUNT(*) FROM evidence WHERE source_class='recorded-market'").fetchone()[0], 10)
        # Hardened strict HUNT materializes the full canonical dependency DAG:
        # market, sector, discovery, prescreen, three research capabilities,
        # deep research, full SEC, standard audit, and adversarial audit.
        self.assertEqual(agent.store.work_item_counts(outcome.run_id).get("SUCCEEDED"), 11)
        sec_evidence = agent.store.connection.execute(
            "SELECT raw_artifact_id FROM evidence WHERE source_class='recorded-sec'"
        ).fetchall()
        self.assertTrue(sec_evidence)
        self.assertTrue(all(row["raw_artifact_id"] for row in sec_evidence))
        stages = {row["stage"] for row in agent.store.list_stage_results(outcome.run_id)}
        self.assertEqual(stages, {
            "MARKET_ANALYSIS", "SECTOR_ANALYSIS", "STOCK_DISCOVERY",
            "CAPITAL_PRESCREEN", "CAP_FUNDAMENTAL_CHANGE",
            "CAP_CATALYST_EXPECTATION_RESEARCH", "CAP_DIRECTIONAL_PROBABILITY",
            "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "STANDARD_AUDIT",
            "ADVERSARIAL_AUDIT", "STAGE_GATE", "CAPITAL_PRESCREEN_GATE", "CATALYST_GATE", "EXPECTATION_GAP_GATE",
        })

    def test_strict_execution_materializes_full_18_work_item_dag(self):
        agent = self.make()
        # The recorded portfolio is only a transport fixture; refresh its
        # observation timestamp so the strict execution freshness fence is
        # exercised rather than bypassed by an old snapshot.
        agent.config.portfolio_provider = RecordedPortfolioProvider({"as_of": utc_now(), "cash": 1000.0, "total_equity": 1000.0, "positions": []})
        outcome = agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, {"requested_action": "WATCH"})
        self.assertEqual(outcome.outcome, "FINAL_ACTION_COMMITTED")
        self.assertEqual(outcome.authoritative_action.value, "WATCH")
        self.assertEqual(agent.store.work_item_counts(outcome.run_id).get("SUCCEEDED"), 18)
        self.assertEqual({row["stage"] for row in agent.store.list_stage_results(outcome.run_id)}, {
            "MARKET_ANALYSIS", "SECTOR_ANALYSIS", "STOCK_DISCOVERY", "CAPITAL_PRESCREEN",
            "CAP_FUNDAMENTAL_CHANGE", "CAP_CATALYST_EXPECTATION_RESEARCH", "CAP_DIRECTIONAL_PROBABILITY",
            "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "STANDARD_AUDIT", "ADVERSARIAL_AUDIT",
            "STAGE_GATE", "CAPITAL_PRESCREEN_GATE", "CATALYST_GATE", "EXPECTATION_GAP_GATE",
            "PORTFOLIO_REVIEW", "ECONOMIC_SCENARIO", "CAP_PROBABILITY_EDGE", "CAP_CATALYST_EXPECTATION_EXEC",
            "CAP_CAPITAL_FORENSICS", "CAP_ENTRY_READINESS", "CAP_FAILURE_INVALIDATION", "FINAL_SYNTHESIS",
        })

    def test_strict_missing_capital_field_fails_closed(self):
        row = raw_rows()[0]; row["capital_prescreen"].pop("toxic_convertible")
        self.assertEqual(self.make([row]).run(RunMode.HUNT_ONLY, {}).outcome, "NO_QUALIFIED_CANDIDATE")

    def test_live_research_failure_is_candidate_level_and_recorded(self):
        class FailingResearch:
            provider_name = "live-research"

            def fetch(self, subject_id, query):
                raise ProviderError(f"source unavailable for {subject_id}")

        agent = self.make()
        agent.config.research_provider = FailingResearch()
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")
        failure = next(row for row in agent.store.list_funnel(outcome.run_id) if row["funnel_stage"] == "RESEARCH_PROVIDER_FAILURE")
        self.assertEqual(failure["count"], 1)
        self.assertIn("source unavailable", failure["details_json"])

    def test_live_sec_failure_is_candidate_level_and_recorded(self):
        class FailingSEC:
            provider_name = "sec-edgar-http"

            def fetch_submissions(self, identity):
                raise ProviderError("SEC temporary failure")

        agent = self.make()
        agent.config.sec_provider = FailingSEC()
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")
        failure = next(row for row in agent.store.list_funnel(outcome.run_id) if row["funnel_stage"] == "SEC_PROVIDER_FAILURE")
        self.assertEqual(failure["count"], 1)
        self.assertIn("SEC temporary failure", failure["details_json"])

    def test_live_sec_stale_candidate_is_not_evaluated_not_outage(self):
        class StaleSEC:
            provider_name = "sec-edgar-http"

            def fetch_submissions(self, identity):
                raise ContractViolation("stale SEC cheap prescreen input exceeds max-age")

        agent = self.make()
        agent.config.sec_provider = StaleSEC()
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertEqual(outcome.outcome, "NO_QUALIFIED_CANDIDATE")
        stale = next(row for row in agent.store.list_funnel(outcome.run_id) if row["funnel_stage"] == "SEC_STALE_DATA")
        outage = next(row for row in agent.store.list_funnel(outcome.run_id) if row["funnel_stage"] == "SEC_PROVIDER_FAILURE")
        self.assertEqual(stale["count"], 1)
        self.assertEqual(outage["count"], 0)
        stage = next(row for row in agent.store.list_stage_results(outcome.run_id) if row["stage"] == "SEC_STALE_DATA")
        self.assertEqual(json.loads(stage["result_json"])["status"], "NOT_EVALUATED")
        agent.close()

    def test_typed_toxic_convertible_rejects(self):
        extraction = {"complete": True, "toxic_convertible": {"state": "TRUE"}}
        self.assertEqual(CapitalPrescreenGate().evaluate(extraction, EffectiveRuleSet()).decision.value, "REJECT")

    def test_position_sizer_is_python_authority(self):
        result = PositionSizer().size(current_price=10, execution_stop=9, account_equity=1000, per_position_budget_pct=1, portfolio_budget_pct=1, maximum_position_shares=3)
        self.assertEqual(result["shares"], 3)

    def test_strict_requires_market_provider(self):
        config = StockAgentConfig(LIBRARY, Path(":memory:"), strict_inputs=True)
        agent = ProductionStockAgent(config, provider=FakeProvider())
        self.assertEqual(agent.run(RunMode.HUNT_ONLY, {}).outcome, "BLOCKED_BY_CRITICAL_ISSUE")


if __name__ == "__main__":
    unittest.main()
