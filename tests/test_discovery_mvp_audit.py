from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from stock_agent.database import Database
from stock_agent.discovery.budget import DiscoveryBudgetGuard
from stock_agent.discovery.features import build_candidate
from stock_agent.discovery.gates import DiscoveryGateRules, global_gate
from stock_agent.discovery.health import bootstrap_health
from stock_agent.discovery.ingestion import InMemoryDiscoveryMarketDataProvider
from stock_agent.discovery.orchestrator import DiscoveryOrchestrator
from stock_agent.discovery.ranking import rank_candidates
from stock_agent.discovery.schemas import DailyBar, FieldValue, MarketQuote, SecurityMasterRecord, UnknownState
from stock_agent.discovery.tournament import final_selection
from stock_agent.discovery.stage import DiscoveryStageEngine
from stock_agent.discovery.fuel import FuelEngine
from stock_agent.discovery.universe import InMemorySecurityMasterProvider


AS_OF = "2026-08-11T00:00:00+00:00"


def record(ticker: str, sector: str = "Technology", **kwargs) -> SecurityMasterRecord:
    sector = kwargs.pop("sector_canonical", sector)
    return SecurityMasterRecord(f"US-{ticker}", ticker, ticker, exchange="NASDAQ",
                                sector_canonical=sector, industry_canonical=kwargs.pop("industry_canonical", sector),
                                source="FIXTURE", **kwargs)


def bars(ticker: str, prices: list[float], volume: int = 1_000_000) -> list[DailyBar]:
    return [DailyBar(ticker, f"2026-05-{index + 1:02d}", price, price * 1.01,
                     price * .99, price, price, volume, "FIXTURE", AS_OF, AS_OF)
            for index, price in enumerate(prices)]


def quote(ticker: str, price: float = 10.0, cap: float | None = 1_000_000_000) -> MarketQuote:
    return MarketQuote(ticker, FieldValue(price, "KNOWN", "FIXTURE", AS_OF),
                       FieldValue(cap, "KNOWN" if cap is not None else UnknownState.UNKNOWN_NOT_AVAILABLE.value,
                                  "FIXTURE", AS_OF), AS_OF, "FIXTURE", "CLOSED")


class DiscoveryMvpAuditTests(unittest.TestCase):
    def candidate(self, ticker: str = "AUDIT", cap: float | None = 1_000_000_000):
        return build_candidate(record(ticker), quote(ticker, cap=cap),
                               bars(ticker, [10.0] * 61), "RUN", AS_OF)

    def test_mixed_hard_failure_and_unknown_is_ineligible(self):
        candidate = self.candidate("HARD", cap=None)
        candidate.fields["current_price"] = FieldValue(1.0, "KNOWN", "FIXTURE", AS_OF)
        DiscoveryStageEngine().apply(candidate)
        status, reasons = global_gate(candidate, DiscoveryGateRules())
        self.assertEqual(status, "INELIGIBLE")
        self.assertIn("PRICE_BELOW_HARD_FLOOR", reasons)
        self.assertIn("MARKET_CAP_UNVERIFIED", reasons)

    def test_fuel_fail_vetoes_momentum_candidate(self):
        candidate = self.candidate("FUEL")
        candidate.stage = "DISCOVERY_STAGE_1"
        candidate.eligibility = "ELIGIBLE"
        candidate.gate_results["fuel_gate"] = "FAIL"
        candidate.scanner_hits = ["MOMENTUM_INFLECTION"]
        candidate.fields["capital_overhang_status"] = FieldValue("CLEAR", "KNOWN", "FIXTURE", AS_OF)
        rank_candidates([candidate])
        self.assertEqual(candidate.eligibility, "INELIGIBLE")
        self.assertNotEqual(candidate.discovery_bucket, "P1_DEEP_ANALYSIS")

    def test_unknown_axes_reduce_coverage_and_are_not_neutral_fifty(self):
        candidate = self.candidate("UNKNOWN_AXES")
        candidate.stage = "DISCOVERY_STAGE_1"
        candidate.eligibility = "ELIGIBLE"
        candidate.gate_results["fuel_gate"] = "PASS"
        candidate.fields["capital_overhang_status"] = FieldValue("CLEAR", "KNOWN", "FIXTURE", AS_OF)
        rank_candidates([candidate])
        self.assertNotIn("expectation_gap", candidate.scores)
        self.assertLess(candidate.score_coverage_pct, 100.0)
        self.assertNotEqual(candidate.composite_score, 50.0)

    def test_watch_candidate_never_calls_child_handoff(self):
        calls: list[str] = []
        config = {"discovery": {"enabled": True, "cost": {"max_llm_calls_per_discovery": 10}}}
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = DiscoveryOrchestrator(Database(str(Path(directory) / "audit.sqlite")), config,
                                                  handoff=lambda request: calls.append(request.tickers[0]))
            candidate = self.candidate("WATCH")
            candidate.discovery_bucket = "WATCH"
            candidate.eligibility = "REVIEW_REQUIRED"
            result = SimpleNamespace(candidates=[candidate])
            outputs = orchestrator.deep_analyze(result, object(), DiscoveryBudgetGuard({"max_llm_calls": 10}))
        self.assertEqual(calls, [])
        self.assertEqual(outputs[0]["reason_codes"], ["NOT_P1_DEEP_ANALYSIS"])

    def test_zero_llm_budget_blocks_child_before_handoff(self):
        calls: list[str] = []
        config = {"discovery": {"enabled": True, "cost": {"max_llm_calls_per_discovery": 0}}}
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = DiscoveryOrchestrator(Database(str(Path(directory) / "audit.sqlite")), config,
                                                  handoff=lambda request: calls.append(request.tickers[0]))
            candidate = self.candidate("ZERO_LLM")
            candidate.discovery_bucket = "P1_DEEP_ANALYSIS"
            candidate.eligibility = "ELIGIBLE"
            result = SimpleNamespace(candidates=[candidate])
            outputs = orchestrator.deep_analyze(result, object(), DiscoveryBudgetGuard({"max_llm_calls": 0}))
        self.assertEqual(calls, [])
        self.assertIn("MAX_LLM_CALLS_PER_DISCOVERY", outputs[0]["reason_codes"])

    def test_benchmark_provider_is_separate_from_common_stock_universe(self):
        stock = record("STOCK")
        market = InMemoryDiscoveryMarketDataProvider(
            [quote("STOCK")], bars("STOCK", [10.0] * 41 + [10.2] * 20, 1_200_000)
            + [bar for ticker in ("SPY", "QQQ", "IWM")
               for bar in bars(ticker, [10.0] * 41 + [11.0] * 20)])
        with tempfile.TemporaryDirectory() as directory:
            result = DiscoveryOrchestrator(
                Database(str(Path(directory) / "audit.sqlite")),
                {"report_dir": str(Path(directory) / "reports"), "discovery": {"enabled": True}},
                InMemorySecurityMasterProvider([stock]), market).run(as_of=AS_OF)
        self.assertEqual([item.security.ticker for item in result.all_candidates], ["STOCK"])
        self.assertNotEqual(result.regime["regime"], "UNKNOWN")
        self.assertEqual(set(result.regime["benchmarks"]), {"SPY", "QQQ", "IWM"})

    def test_bootstrap_health_rejects_identity_only_and_accepts_validated_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "audit.sqlite"))
            valid = record("VALID")
            market = InMemoryDiscoveryMarketDataProvider(
                [quote("VALID")], bars("VALID", [10.0] * 10)
                + [bar for ticker in ("SPY", "QQQ", "IWM") for bar in bars(ticker, [10.0] * 10)])
            health = bootstrap_health(db, InMemorySecurityMasterProvider([valid]), market, market)
            self.assertEqual(health["status"], "MARKET_SCAN_READY")
            self.assertTrue(health["legacy_discovery_ready"])
            identity_only = record("UNKNOWN", is_common_stock=None, is_etf=None, is_unit=None,
                                   is_warrant=None, is_preferred=None, is_adr=None,
                                   sector_canonical="UNKNOWN", industry_canonical="UNKNOWN")
            blocked = bootstrap_health(db, InMemorySecurityMasterProvider([identity_only]), market, market)
            self.assertEqual(blocked["status"], "BOOTSTRAP_REQUIRED")
            self.assertIn("IDENTITY_ENRICHMENT_MISSING", blocked["reason_codes"])

    def test_certified_only_final_selection_allows_none(self):
        self.assertEqual(final_selection([{"ticker": "A", "certified": False}]), "NONE")
        eligible = {"certified": True, "decision": "BUY", "risk_hard_filter_pass": True,
                    "trade_plan_valid": True, "market_fresh": True,
                    "no_material_unresolved_blocker": True}
        complete_scorecard = {axis: 80 for axis in (
            "signal_strength", "catalyst_quality", "expectation_gap", "surge_elasticity",
            "entry_readiness", "capital_structure_safety", "strategy_fit", "data_confidence")}
        complete_scorecard["reward_risk"] = 2.0
        self.assertEqual(final_selection([{**eligible, "ticker": "A",
                                          "scores": complete_scorecard},
                                         {"ticker": "B", "certified": False,
                                          "scores": {"data_confidence": 100}}], {
                                             "portfolio_context_status": "READY",
                                             "remaining_risk_budget_usd": 1000}), "A")


if __name__ == "__main__":
    unittest.main()
