import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stock_agent.command_parser import CommandInterpreter
from stock_agent.capital_structure import build_capital_structure
from stock_agent.database import Database
from stock_agent.discovery.features import build_candidate
from stock_agent.discovery.ingestion import InMemoryDiscoveryMarketDataProvider
from stock_agent.discovery.orchestrator import DiscoveryOrchestrator
from stock_agent.discovery.providers_live import TossDiscoveryBenchmarkProvider
from stock_agent.discovery.schemas import DailyBar, FieldValue, MarketQuote, SecurityMasterRecord
from stock_agent.discovery.tournament import compare_candidates, final_selection
from stock_agent.discovery.universe import InMemorySecurityMasterProvider
from stock_agent.schemas import EvidenceItem


AS_OF = "2026-08-11T00:00:00+00:00"


def record(ticker: str, sector: str = "Technology") -> SecurityMasterRecord:
    return SecurityMasterRecord(f"US-{ticker}", ticker, ticker, exchange="NASDAQ",
                                sector_canonical=sector, industry_canonical=sector,
                                source="FIXTURE", themes=(sector,))


def price_bars(ticker: str, rising: bool = True) -> list[DailyBar]:
    prices = [10.0] * 55 + ([10.1, 10.2, 10.3, 10.4, 10.5, 10.6] if rising else [10.0] * 6)
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    rows = []
    for index, price in enumerate(prices):
        day = (start + timedelta(days=index)).date().isoformat()
        volume = 2_000_000 if index == len(prices) - 1 else 1_000_000
        rows.append(DailyBar(ticker, day, price, price * 1.01, price * .99, price, price,
                             volume, "FIXTURE", AS_OF, AS_OF))
    return rows


def quote(ticker: str, cap: float = 800_000_000) -> MarketQuote:
    return MarketQuote(ticker, FieldValue(10.6, "KNOWN", "FIXTURE", AS_OF),
                       FieldValue(cap, "KNOWN", "FIXTURE", AS_OF), AS_OF, "FIXTURE", "CLOSED")


class FundamentalFixture:
    calls = []

    def fundamentals(self, tickers, as_of):
        self.calls.append(list(tickers))
        return {ticker: {
            "primary_financial_evidence": FieldValue(True, "KNOWN", "COMPANYFACTS", as_of),
            "revenue_growth_acceleration_pp": FieldValue(12.0, "KNOWN", "COMPANYFACTS", as_of,
                                                            source_ids=(f"SEC_{ticker}_REV",)),
            "gross_margin_delta_pp": FieldValue(4.0, "KNOWN", "COMPANYFACTS", as_of,
                                                 source_ids=(f"SEC_{ticker}_GM",)),
            "operating_cash_flow_current": FieldValue(10.0, "KNOWN", "COMPANYFACTS", as_of),
            "expectation_gap": FieldValue(80.0, "KNOWN", "COMPANYFACTS", as_of),
            "surge_elasticity": FieldValue(80.0, "KNOWN", "COMPANYFACTS", as_of),
            "strategy_fit": FieldValue(80.0, "KNOWN", "COMPANYFACTS", as_of),
        } for ticker in tickers}


class CapitalFixture:
    calls = []

    def preflight(self, tickers, as_of):
        self.calls.append(list(tickers))
        return {ticker: {
            "capital_overhang_status": FieldValue("CLEAR", "KNOWN", "SEC_EDGAR", as_of),
            "capital_structure_safety": FieldValue(80.0, "KNOWN", "SEC_EDGAR", as_of),
        } for ticker in tickers}


def live_config(directory: str, preflight_n: int = 1) -> dict:
    return {"report_dir": directory, "discovery": {
        "enabled": True, "shadow_mode": True,
        "universe": {"min_price": 3, "min_market_cap_usd": 300_000_000, "min_adv20_usd": 10_000_000},
        "coverage": {"market_min_pct": 0, "feature_min_pct": 0,
                     "fundamental_enrichment_min_pct": 0, "capital_preflight_min_pct": 80},
        "enrichment": {"market_survivor_n": 10},
        "shortlist": {"python_top_n": 10, "evidence_preflight_n": preflight_n},
        "final_scorecard": {"min_coverage_pct": 75, "min_reward_risk": 1.5},
        "cost": {"max_companyfacts_calls": 20, "max_sec_calls": 20,
                 "max_actual_llm_calls": 0},
    }}


class DiscoveryMvpV4AuditTests(unittest.TestCase):
    def test_market_flow_pending_then_companyfacts_fundamental_final_fuel_pass_reaches_p1(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "fuel-stage.sqlite"))
            fundamental = FundamentalFixture()
            capital = CapitalFixture()
            market = InMemoryDiscoveryMarketDataProvider([quote("SMALLX")], price_bars("SMALLX"))
            orchestrator = DiscoveryOrchestrator(
                db, live_config(directory), InMemorySecurityMasterProvider([record("SMALLX")]),
                market, fundamental, market, capital)
            result = orchestrator.run(as_of=AS_OF, shadow=True)
            item = next(candidate for candidate in result.all_candidates if candidate.security.ticker == "SMALLX")
            self.assertNotEqual(item.gate_results.get("market_gate_status"), "INELIGIBLE")
            self.assertEqual(item.gate_results.get("preliminary_fuel_gate"), "PENDING_ENRICHMENT")
            self.assertEqual(item.gate_results.get("fundamental_hydration_status"), "READY")
            self.assertEqual(item.gate_results.get("final_fuel_status"), "PASS")
            self.assertIn("FLOW", item.signal_families)
            self.assertIn("FUNDAMENTAL", item.signal_families)
            self.assertEqual(item.gate_results.get("capital_preflight_status"), "READY")
            self.assertEqual(item.gate_results.get("final_candidate_gate"), "PASS")
            self.assertEqual(item.discovery_bucket, "P1_DEEP_ANALYSIS")
            self.assertEqual(result.api_telemetry["funnel"]["final_fuel_pass"], 1)

    def test_market_flow_without_fundamental_fails_only_after_hydration_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "fuel-fail.sqlite"))
            fundamental = FundamentalFixture()
            fundamental.fundamentals = lambda tickers, as_of: {
                ticker: {"primary_financial_evidence": FieldValue(False, "KNOWN", "FIXTURE", as_of)}
                for ticker in tickers}
            market = InMemoryDiscoveryMarketDataProvider([quote("NOFUEL")], price_bars("NOFUEL"))
            result = DiscoveryOrchestrator(
                db, live_config(directory), InMemorySecurityMasterProvider([record("NOFUEL")]),
                market, fundamental, market, CapitalFixture()).run(as_of=AS_OF, shadow=True)
            item = result.all_candidates[0]
            self.assertEqual(item.gate_results.get("preliminary_fuel_gate"), "PENDING_ENRICHMENT")
            self.assertEqual(item.gate_results.get("final_fuel_status"), "FAIL")
            self.assertEqual(item.gate_results.get("final_candidate_gate"), "INELIGIBLE")
            self.assertEqual(item.discovery_bucket, "REJECT")

    def test_preflight_coverage_is_success_over_requested_and_scope_is_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            records = [record("PFX1"), record("PFX2"), record("PFX3")]
            quotes = [quote(record.ticker) for record in records]
            market = InMemoryDiscoveryMarketDataProvider(
                quotes, sum((price_bars(record.ticker) for record in records), []))
            result = DiscoveryOrchestrator(
                Database(str(Path(directory) / "coverage.sqlite")),
                live_config(directory, preflight_n=1), InMemorySecurityMasterProvider(records),
                market, FundamentalFixture(), market, CapitalFixture()).run(as_of=AS_OF, shadow=True)
            self.assertEqual(result.coverage.capital_preflight_coverage_pct, 100.0)
            self.assertAlmostEqual(result.coverage.capital_preflight_scope_pct, 33.3333, places=3)

    def test_natural_language_discovery_promotion_is_explicit_and_bounded(self):
        for text in ("DISCOVERY DEEP", "DISCOVERY PROMOTE", "DISCOVERY 상위 3개 정밀분석",
                     "방금 DISCOVERY 상위 3개 정밀분석해",
                     "디스커버리 상위 3개 정밀분석해",
                     "방금 디스커버리 상위 3개 분석해",
                     "방금 Discovery 상위 3개 정밀분석해"):
            request = CommandInterpreter().parse(text)
            self.assertEqual(request.intent, "DISCOVERY_DEEP_HANDOFF", text)
            if "3" in text:
                self.assertEqual(request.promotion_limit, 3, text)
        shadow = CommandInterpreter().parse("오늘 미국시장 전체 훑어줘")
        self.assertEqual(shadow.intent, "DISCOVER_MARKET")

    def test_sparse_scorecard_cannot_gain_tournament_advantage(self):
        sparse = {"ticker": "SPARSE", "certified": True, "decision": "BUY",
                  "risk_hard_filter_pass": True, "trade_plan_valid": True,
                  "market_fresh": True, "no_material_unresolved_blocker": True,
                  "scores": {"signal_strength": 95}}
        complete_scores = {axis: 80 for axis in (
            "signal_strength", "catalyst_quality", "expectation_gap", "surge_elasticity",
            "entry_readiness", "capital_structure_safety", "strategy_fit", "data_confidence")}
        complete_scores["reward_risk"] = 2.0
        complete = {**sparse, "ticker": "COMPLETE", "scores": complete_scores}
        self.assertNotEqual(compare_candidates(sparse, complete).winner, "SPARSE")
        self.assertEqual(final_selection([sparse, complete], {"remaining_risk_budget_usd": 1000}), "COMPLETE")
        partial_scores = {**complete_scores}
        partial_scores.pop("strategy_fit")
        partial_scores.update({"signal_strength": 95, "catalyst_quality": 95, "expectation_gap": 95})
        partial = {**complete, "ticker": "PARTIAL", "scores": partial_scores}
        self.assertEqual(compare_candidates(partial, complete).winner, "COMPLETE")
        self.assertEqual(final_selection([partial, complete], {"remaining_risk_budget_usd": 1000}), "COMPLETE")
        low_rr = {**complete, "ticker": "LOW_RR", "scores": {**complete_scores, "reward_risk": 1.4}}
        self.assertEqual(final_selection([low_rr], {"remaining_risk_budget_usd": 1000}), "NONE")

    def test_capital_negative_states_require_explicit_filing_language(self):
        evidence = [EvidenceItem(
            "CAP_NEGATIVE", "ABC", "SEC", "10-Q", "2026-08-11", "Capital", "u", "B",
            "CAPITAL", "No active ATM program. The warrants are no longer outstanding.",
            normalized_fact="No active ATM program. The warrants are no longer outstanding.",
            accession="ACC", filed_at="2026-08-11",
        )]
        snapshot = build_capital_structure("ABC", {"normalized_facts": [], "derived": {}}, evidence)
        self.assertEqual(snapshot.atm_active.status, "KNOWN")
        self.assertFalse(snapshot.atm_active.value)
        self.assertEqual(snapshot.warrant_outstanding.status, "KNOWN")
        self.assertFalse(snapshot.warrant_outstanding.value)

    def test_portfolio_context_counts_current_and_pending_exposure_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "portfolio.sqlite"))
            db.init()
            db.initialize_paper_account(100_000, "PAPER_DEFAULT", 1.0)
            with db.connect() as connection:
                connection.execute("""INSERT INTO portfolio_positions(
                    ticker,quantity,average_price,updated_at,mode,account_id,sector,status,
                    market_value,position_risk_usd,risk_provenance_json,latest_mark,mark_timestamp,
                    mark_source,mark_status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("SOUN", 100, 100, AS_OF, "PAPER", "PAPER_DEFAULT", "Technology", "OPEN",
                     10_000, 100, '{"status":"KNOWN","components":[{"stop_price":99,"quantity":100}]}',
                     100, AS_OF, "FIXTURE", "FRESH"))
                connection.execute("""INSERT INTO paper_orders(
                    order_id,account_id,run_id,ticker,side,order_type,status,quantity,reserved_cash,
                    sector,created_at,updated_at,risk_per_share,risk_provenance_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("ORDER_PENDING", "PAPER_DEFAULT", "RUN", "IONQ", "BUY", "CONDITIONAL",
                     "PENDING", 50, 5_000, "Technology", AS_OF, AS_OF, 2,
                     '{"status":"KNOWN"}'))
            before = db.paper_account_state()
            orchestrator = DiscoveryOrchestrator(db, {"paper": {"max_sector_exposure_pct": 25}})
            context = orchestrator._portfolio_context()
            after = db.paper_account_state()
            self.assertEqual(before["risk_budget_used"], after["risk_budget_used"])
            self.assertAlmostEqual(context["existing_sector_exposure_pct"]["Technology"], 9.0909, places=3)
            self.assertAlmostEqual(context["pending_sector_exposure_pct"]["Technology"], 4.5455, places=3)
            self.assertAlmostEqual(context["existing_ticker_exposure_pct"]["SOUN"], 9.0909, places=3)
            self.assertAlmostEqual(context["pending_ticker_exposure_pct"]["IONQ"], 4.5455, places=3)

    def test_live_benchmark_adapter_keeps_etfs_out_of_candidate_provider(self):
        class MarketFixture:
            def daily_bars(self, ticker, as_of):
                return [ticker, as_of]

        provider = TossDiscoveryBenchmarkProvider(MarketFixture())
        self.assertEqual(provider.benchmark_bars(["SPY", "QQQ", "IWM"], AS_OF), {
            "SPY": ["SPY", AS_OF], "QQQ": ["QQQ", AS_OF], "IWM": ["IWM", AS_OF]})


if __name__ == "__main__":
    unittest.main()
