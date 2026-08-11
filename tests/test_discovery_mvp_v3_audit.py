from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stock_agent.capital_structure import (CapitalStructureSnapshot, ProvenancedValue,
                                           build_capital_structure)
from stock_agent.database import Database
from stock_agent.discovery.budget import DiscoveryBudgetGuard
from stock_agent.discovery.diversity import diversity_filter
from stock_agent.discovery.features import build_candidate
from stock_agent.discovery.ingestion import InMemoryDiscoveryMarketDataProvider
from stock_agent.discovery.orchestrator import DiscoveryOrchestrator
from stock_agent.discovery.pareto import pareto_filter
from stock_agent.discovery.providers_live import SECDiscoveryFundamentalProvider
from stock_agent.discovery.ranking import preliminary_priority_score, rank_candidates
from stock_agent.discovery.schemas import (CoverageMetrics, DailyBar, DiscoveryContext,
                                            DiscoveryResult, FieldValue, MarketQuote,
                                            SecurityMasterRecord)
from stock_agent.discovery.universe import InMemorySecurityMasterProvider
from stock_agent.discovery.tournament import compare_candidates, final_selection
from stock_agent.readiness import DataReadinessPreflight
from stock_agent.schemas import EvidenceItem, ResearchAnalysis, UserRequest, now_iso
from stock_agent.sec import SECCompanyFactsProvider


AS_OF = "2026-08-11T00:00:00+00:00"


def record(ticker: str, sector: str = "Technology") -> SecurityMasterRecord:
    return SecurityMasterRecord(f"US-{ticker}", ticker, ticker, exchange="NASDAQ",
                                sector_canonical=sector, industry_canonical=sector,
                                source="FIXTURE", themes=(sector,))


def bars(ticker: str) -> list[DailyBar]:
    return [DailyBar(ticker, f"2026-05-{index + 1:02d}", 10, 10.1, 9.9, 10,
                     10, 1_000_000, "FIXTURE", AS_OF, AS_OF) for index in range(61)]


def candidate(ticker: str, cap: float, sector: str = "Technology"):
    item = build_candidate(record(ticker, sector), MarketQuote(
        ticker, FieldValue(10, "KNOWN", "FIXTURE", AS_OF),
        FieldValue(cap, "KNOWN", "FIXTURE", AS_OF), AS_OF, "FIXTURE"),
        bars(ticker), "RUN", AS_OF)
    item.stage = "DISCOVERY_STAGE_1"
    item.eligibility = "ELIGIBLE"
    item.gate_results.update({"fuel_gate": "PASS", "global_gate": "PASS"})
    item.fields["capital_overhang_status"] = FieldValue("CLEAR", "KNOWN", "FIXTURE", AS_OF)
    item.fields["primary_financial_evidence"] = FieldValue(True, "KNOWN", "FIXTURE", AS_OF)
    return item


def eligible_final(ticker: str, scores: dict) -> dict:
    return {"ticker": ticker, "certified": True, "decision": "BUY",
            "risk_hard_filter_pass": True, "trade_plan_valid": True,
            "market_fresh": True, "no_material_unresolved_blocker": True,
            "scores": scores}


class DiscoveryMvpV3AuditTests(unittest.TestCase):
    def test_small_strong_candidate_can_reach_enrichment_without_market_cap_bonus(self):
        large = candidate("LARGE", 50_000_000_000)
        small = candidate("SMALL", 800_000_000)
        large.scanner_hits = []
        large.signal_families = []
        large.fuel_events = []
        small.scanner_hits = ["MOMENTUM_INFLECTION", "GENERAL_INFLECTION"]
        small.signal_families = ["FLOW", "FUNDAMENTAL"]
        small.fuel_events = [{"signal_family": "FLOW", "effective_strength": 95}]
        for item in (large, small):
            item.fields["sector_regime_fit"] = FieldValue(80, "KNOWN", "FIXTURE", AS_OF)
            item.data_confidence = 90
            item.score_coverage_pct = 90
            item.preliminary_priority_score, _ = preliminary_priority_score(item)
        ordered = sorted((large, small), key=lambda item: -item.preliminary_priority_score)
        self.assertEqual(ordered[0].security.ticker, "SMALL")
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = DiscoveryOrchestrator(Database(str(Path(directory) / "v3.sqlite")),
                                                  {"discovery": {"enabled": True}})
            selected = orchestrator._diversified_prefix(ordered, 1, 2, 2, 2)
        self.assertEqual([item.security.ticker for item in selected], ["SMALL"])

    def test_diversity_is_applied_to_capital_preflight_slots(self):
        items = [candidate(f"T{index}", 1_000_000_000, "Technology" if index < 4 else "Energy")
                 for index in range(5)]
        for index, item in enumerate(items):
            item.composite_score = 100 - index
            item.size_bucket = "MID"
        selected = diversity_filter(items, max_same_sector=2, max_same_theme=5, max_same_size_bucket=5)
        self.assertEqual([item.security.ticker for item in selected[:3]], ["T0", "T1", "T4"])

    def test_companyfacts_production_fundamental_path_separates_growth_and_acceleration(self):
        def rows(values):
            return [
                {"start": "2025-01-01", "end": "2025-03-31", "val": values[0], "form": "10-Q", "filed": "2025-05-01", "fy": 2025, "fp": "Q1", "accn": "25Q1"},
                {"start": "2025-04-01", "end": "2025-06-30", "val": values[1], "form": "10-Q", "filed": "2025-08-01", "fy": 2025, "fp": "Q2", "accn": "25Q2"},
                {"start": "2026-01-01", "end": "2026-03-31", "val": values[2], "form": "10-Q", "filed": "2026-05-01", "fy": 2026, "fp": "Q1", "accn": "26Q1"},
                {"start": "2026-04-01", "end": "2026-06-30", "val": values[3], "form": "10-Q", "filed": "2026-08-01", "fy": 2026, "fp": "Q2", "accn": "26Q2"},
            ]
        payload = {"facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rows([100, 118, 118, 153.4])}},
            "GrossProfit": {"units": {"USD": rows([60, 76.7, 76.7, 92.04])}},
            "OperatingIncomeLoss": {"units": {"USD": rows([10, 11, 12, 18])}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": rows([5, 6, 7, 10])}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": rows([2, 2, 2, 2])}},
        }}}
        with tempfile.TemporaryDirectory() as directory:
            provider = SECDiscoveryFundamentalProvider("Agent test@example.com", directory)
            with patch.object(provider.provider, "_find_cik", return_value="0000000001"), \
                 patch.object(provider.provider, "_get_json", return_value=payload):
                output = provider.fundamentals(["ABC"], AS_OF)["ABC"]
        self.assertAlmostEqual(output["revenue_growth_current_pct"].value, 30, places=4)
        self.assertAlmostEqual(output["revenue_growth_previous_pct"].value, 18, places=4)
        self.assertAlmostEqual(output["revenue_growth_acceleration_pp"].value, 12, places=4)
        self.assertAlmostEqual(output["gross_margin_current_pct"].value, 60, places=2)
        self.assertAlmostEqual(output["gross_margin_previous_pct"].value, 65, places=2)
        self.assertAlmostEqual(output["gross_margin_delta_pp"].value, -5, places=2)
        self.assertAlmostEqual(output["operating_cash_flow_inflection"].value, 3, places=4)

    def test_capital_three_state_and_resale_semantics(self):
        false = ProvenancedValue(False, "KNOWN")
        true = ProvenancedValue(True, "KNOWN")
        unknown = ProvenancedValue(None, "UNKNOWN")
        resale = [{"offering_type": "SELLING_STOCKHOLDER_RESALE"}]
        clear = CapitalStructureSnapshot("ABC", AS_OF, atm_active=false,
                                         warrant_outstanding=false,
                                         convertible_outstanding=false,
                                         offering_events=resale)
        self.assertEqual(clear.capital_overhang_status, "CLEAR")
        self.assertNotEqual(CapitalStructureSnapshot("ABC", AS_OF, atm_active=false,
                                                      warrant_outstanding=true,
                                                      convertible_outstanding=false).capital_overhang_status,
                            "CLEAR")
        self.assertEqual(CapitalStructureSnapshot("ABC", AS_OF, atm_active=unknown,
                                                   warrant_outstanding=false,
                                                   convertible_outstanding=false).capital_overhang_status,
                         "UNKNOWN")

    def test_scan_only_never_calls_child_handoff(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = DiscoveryOrchestrator(Database(str(Path(directory) / "v3.sqlite")),
                                                  {"report_dir": directory, "discovery": {"enabled": True}},
                                                  handoff=lambda request: calls.append(request))
            # No provider universe is a valid fail-closed shadow scan.
            result = orchestrator.run(shadow=True, as_of=AS_OF)
        self.assertEqual(calls, [])
        self.assertEqual(result.deep_analysis_results, [])

    def test_provider_telemetry_is_run_local_and_warm_cache_avoids_full_bar_refetch(self):
        security = record("CACHE")
        quote = MarketQuote("CACHE", FieldValue(10, "KNOWN", "FIXTURE", AS_OF),
                            FieldValue(800_000_000, "KNOWN", "FIXTURE", AS_OF), AS_OF, "FIXTURE")
        market = InMemoryDiscoveryMarketDataProvider([quote], bars("CACHE"))
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "telemetry.sqlite"))
            orchestrator = DiscoveryOrchestrator(
                db, {"report_dir": directory, "discovery": {"enabled": True}},
                InMemorySecurityMasterProvider([security]), market)
            first = orchestrator.run(as_of=AS_OF)
            second = orchestrator.run(as_of=AS_OF)
            with db.connect() as connection:
                rows = connection.execute(
                    "SELECT discovery_run_id,operation,requested_count,returned_count,cache_hits "
                    "FROM discovery_provider_calls WHERE operation IN ('BATCH_QUOTES','DAILY_BARS') "
                    "ORDER BY discovery_run_id,operation").fetchall()
            self.assertEqual(len(rows), 4)
            by_run = {run_id: {(row[1], row[2], row[3], row[4]) for row in rows if row[0] == run_id}
                      for run_id in (first.run_id, second.run_id)}
            self.assertEqual(by_run[first.run_id], {("BATCH_QUOTES", 1, 1, 0), ("DAILY_BARS", 1, 1, 0)})
            self.assertEqual(by_run[second.run_id], {("BATCH_QUOTES", 1, 1, 0), ("DAILY_BARS", 1, 1, 1)})

    def test_explicit_handoff_uses_existing_analyze_child_and_preserves_paper_off(self):
        calls = []
        plan = SimpleNamespace(reward_risk=2.0)
        child = {"run_id": "", "certification": SimpleNamespace(certified=True, certification_status="CERTIFIED", decision_confidence=80, required_data_failures=[], important_data_warnings=[]),
                 "decision": SimpleNamespace(decision="BUY", trade_plan=plan),
                 "risk": SimpleNamespace(hard_filter_pass=True, trade_plan=plan),
                 "research": SimpleNamespace(signal_strength=80, catalyst_quality=70, expectation_gap=60,
                                             surge_elasticity=65, entry_readiness=75, strategy_fit=80,
                                             score_details={})}
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = DiscoveryOrchestrator(Database(str(Path(directory) / "v3.sqlite")),
                                                  {"discovery": {"enabled": True, "cost": {"max_actual_llm_calls": 5}}},
                                                  handoff=lambda request: calls.append(request) or child)
            item = candidate("PROMOTE", 800_000_000)
            item.discovery_bucket = "P1_DEEP_ANALYSIS"
            result = SimpleNamespace(run_id="RUN", candidates=[item], context=SimpleNamespace(discovery_as_of=AS_OF), analysis_links=[])
            request = UserRequest("REQ", "MSG", "USER", AS_OF, "DISCOVERY DEEP RUN", "DISCOVERY_DEEP_HANDOFF", [],
                                  paper_action_enabled=False)
            outputs = orchestrator.deep_analyze(result, request, DiscoveryBudgetGuard({"max_actual_llm_calls": 5}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].intent, "ANALYZE")
        self.assertFalse(calls[0].paper_action_enabled)
        self.assertTrue(outputs[0]["certified"])
        self.assertIn("expectation_gap", outputs[0]["scores"])
        self.assertNotIn("data_confidence", outputs[0]["scores"])

    def test_production_promotion_resolves_stored_run_and_blocks_expired_watch(self):
        calls = []
        plan = SimpleNamespace(reward_risk=2.0)
        child = {"run_id": "CHILD_PROMOTED",
                 "certification": SimpleNamespace(certified=True, certification_status="CERTIFIED",
                                                  decision_confidence=80, required_data_failures=[],
                                                  important_data_warnings=[]),
                 "decision": SimpleNamespace(decision="BUY", trade_plan=plan),
                 "risk": SimpleNamespace(hard_filter_pass=True, trade_plan=plan),
                 "research": ResearchAnalysis(
                     ticker="VALID", market_regime="RISK_ON", sector="Technology",
                     signal_strength=80, catalyst_quality=70, expectation_gap=60,
                     surge_elasticity=65, entry_readiness=75,
                     capital_structure_risk=20, strategy_fit=80,
                     bull_case=[], bear_case=[], suggested_decision="BUY",
                     confidence=80, evidence_ids=[])}
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "promotion.sqlite"))
            db.init()
            run_context = DiscoveryContext(
                "RUN", "MARKET", "", "MINIMUM", AS_OF, AS_OF, AS_OF, AS_OF, AS_OF,
                "rules", "features", "sha", "SNAPSHOT", shadow=True)
            valid = candidate("VALID", 800_000_000)
            valid.discovery_run_id = "RUN"
            valid.discovery_bucket = "P1_DEEP_ANALYSIS"
            valid.expires_at = "2099-01-01T00:00:00+00:00"
            expired = candidate("EXPIRED", 800_000_000)
            expired.discovery_run_id = "RUN"
            expired.discovery_bucket = "P1_DEEP_ANALYSIS"
            expired.expires_at = "2000-01-01T00:00:00+00:00"
            watch = candidate("WATCH", 800_000_000)
            watch.discovery_run_id = "RUN"
            watch.discovery_bucket = "WATCH"
            watch.expires_at = "2099-01-01T00:00:00+00:00"
            result = DiscoveryResult(
                "RUN", "COMPLETED_SHADOW_ENRICHED", "SHADOW_ENRICHED", run_context,
                CoverageMetrics(3, 3, 3, 3, 3, 100, 100, 100, 100), {}, [],
                [valid, expired, watch], all_candidates=[valid, expired, watch])
            orchestrator = DiscoveryOrchestrator(
                db, {"discovery": {"enabled": True, "cost": {
                    "max_actual_llm_calls": 5, "max_llm_input_tokens": 1000,
                    "max_llm_output_tokens": 1000, "max_estimated_cost_usd": 1,
                    "max_child_analysis_runs": 3}}},
                handoff=lambda request: calls.append(request) or child)
            with patch.object(orchestrator, "_portfolio_context", return_value={
                    "portfolio_context_status": "READY", "remaining_risk_budget_usd": 1000}):
                orchestrator.store.save_run(result, AS_OF, AS_OF)
                request = UserRequest("REQ", "MSG", "USER", AS_OF, "DISCOVERY DEEP RUN",
                                      "DISCOVERY_DEEP_HANDOFF", [], paper_action_enabled=False,
                                      discovery_run_id="RUN", promotion_limit=3)
                promoted = orchestrator.promote("RUN", request, 3)
            self.assertEqual([request.intent for request in calls], ["ANALYZE"])
            self.assertEqual([item.security.ticker for item in promoted.candidates], ["VALID"])
            self.assertEqual(promoted.deep_analysis_results[0]["analysis_run_id"], "CHILD_PROMOTED")
            self.assertEqual(promoted.deep_analysis_results[0]["scores"]["capital_structure_safety"], 80.0)
            self.assertEqual(
                promoted.deep_analysis_results[0]["score_provenance"]["capital_structure_safety"]["source_field"],
                "capital_structure_risk")
            self.assertEqual(promoted.final_selection, "VALID")
            self.assertEqual(
                promoted.api_telemetry["final_selection_diagnostics"]["reason_codes"],
                ["CERTIFIED_CHILD_SELECTED"])
            with db.connect() as connection:
                rows = {row["ticker"]: row["promotion_status"] for row in connection.execute(
                    "SELECT ticker,promotion_status FROM discovery_candidates WHERE discovery_run_id='RUN'")}
            self.assertEqual(rows["VALID"], "READY")
            self.assertEqual(rows["EXPIRED"], "BLOCKED")
            self.assertEqual(rows["WATCH"], "BLOCKED")

    def test_all_known_ranking_axes_have_full_score_coverage(self):
        item = candidate("KNOWN", 800_000_000)
        for name in ("expectation_gap", "surge_elasticity", "sector_regime_fit",
                     "capital_structure_safety", "strategy_fit", "revenue_growth_acceleration_pp"):
            item.fields[name] = FieldValue(80, "KNOWN", "FIXTURE", AS_OF)
        item.scanner_hits = ["GENERAL_INFLECTION"]
        item.signal_families = ["FUNDAMENTAL"]
        item.fuel_events = [{"effective_strength": 80}]
        rank_candidates([item])
        self.assertEqual(item.score_coverage_pct, 100.0)

    def test_pareto_dominance_does_not_turn_missing_axis_into_zero(self):
        complete = candidate("COMPLETE", 800_000_000)
        missing = candidate("MISSING", 800_000_000)
        for item in (complete, missing):
            item.eligibility = "ELIGIBLE"
        complete.scores = {"capital_structure_safety": 80}
        missing.scores = {}
        survivors = pareto_filter([complete, missing])
        self.assertEqual({item.security.ticker for item in survivors}, {"COMPLETE", "MISSING"})

    def test_actual_usage_is_measured_and_limit_blocks_next_child(self):
        def usage_run(db, run_id):
            db.start_run(run_id, "ABC", "ANALYZE")
            for index in range(5):
                db.record_llm_call({"api_call_id": f"{run_id}-{index}", "run_id": run_id,
                                    "role": "research", "phase": "TEST", "round_no": index,
                                    "input_tokens": 10, "output_tokens": 20,
                                    "estimated_cost_usd": .01, "provider": "fixture", "model": "fixture"})
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "v3.sqlite"))
            db.init()
            usage_run(db, "CHILD_A")
            usage_run(db, "CHILD_B")
            calls = iter(["CHILD_A", "CHILD_B"])
            orchestrator = DiscoveryOrchestrator(db, {"discovery": {"enabled": True}},
                                                  handoff=lambda request: {"run_id": next(calls)})
            items = [candidate("A", 800_000_000), candidate("B", 900_000_000)]
            for item in items:
                item.discovery_bucket = "P1_DEEP_ANALYSIS"
            result = SimpleNamespace(run_id="RUN", candidates=items, context=SimpleNamespace(discovery_as_of=AS_OF), analysis_links=[])
            request = UserRequest("REQ", "MSG", "USER", AS_OF, "DISCOVERY DEEP", "DISCOVERY_DEEP_HANDOFF", [])
            budget = DiscoveryBudgetGuard({"max_actual_llm_calls": 3, "max_child_analysis_runs": 3})
            outputs = orchestrator.deep_analyze(result, request, budget)
        self.assertEqual(budget.used["actual_llm_calls"], 5)
        self.assertEqual(outputs[1]["reason_codes"], ["MAX_LLM_CALLS_PER_DISCOVERY"])

    def test_final_tournament_uses_actual_scorecard_and_zero_risk_budget_returns_none(self):
        complete = {axis: 80 for axis in (
            "signal_strength", "catalyst_quality", "expectation_gap", "surge_elasticity",
            "entry_readiness", "capital_structure_safety", "strategy_fit", "data_confidence")}
        complete["reward_risk"] = 2.0
        a = eligible_final("A", complete)
        b = eligible_final("B", {**complete, "signal_strength": 70})
        comparison = compare_candidates(a, b)
        self.assertEqual(comparison.winner, "A")
        self.assertEqual(final_selection([a], {
            "portfolio_context_status": "READY", "remaining_risk_budget_usd": 0}), "NONE")
        self.assertEqual(final_selection([], {}), "NONE")

    def test_latest_periodic_filing_uses_filed_at_not_accession_lexical_order(self):
        older_accession = EvidenceItem("OLD", "ABC", "SEC", "10-Q", "2026-08-01", "old", "u", "B", "FILING", "old",
                                       accession="999", filed_at="2026-08-01", raw_document_hash="h1", parsed_at=AS_OF)
        newer_filed = EvidenceItem("NEW", "ABC", "SEC", "10-Q", "2026-08-02", "new", "u", "B", "FILING", "new",
                                   accession="100", filed_at="2026-08-02", raw_document_hash="h2", parsed_at=AS_OF)
        result = DataReadinessPreflight().evaluate([older_accession, newer_filed], {"100"})
        self.assertEqual(result.status, "READY")

    def test_v26_migration_is_additive_and_preserves_existing_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "v3.sqlite"))
            db.init()
            with db.connect() as connection:
                self.assertIsNotNone(connection.execute("SELECT 1 FROM schema_migrations WHERE version=24").fetchone())
                self.assertIsNotNone(connection.execute("SELECT 1 FROM schema_migrations WHERE version=25").fetchone())
                self.assertIsNotNone(connection.execute("SELECT 1 FROM schema_migrations WHERE version=26").fetchone())
                columns = {row[1] for row in connection.execute("PRAGMA table_info(discovery_runs)")}
                self.assertTrue({"market_scan_status", "actual_llm_calls", "actual_cost_usd",
                                 "capital_preflight_scope_pct"}.issubset(columns))
                self.assertIsNotNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='paper_accounts'").fetchone())


if __name__ == "__main__":
    unittest.main()
