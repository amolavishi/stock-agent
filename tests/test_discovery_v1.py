from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stock_agent.command_parser import CommandInterpreter
from stock_agent.database import Database
from stock_agent.discovery.features import build_candidate
from stock_agent.discovery.fuel import FuelEngine
from stock_agent.discovery.gates import DiscoveryGateRules, global_gate
from stock_agent.discovery.ingestion import InMemoryDiscoveryMarketDataProvider
from stock_agent.discovery.orchestrator import DiscoveryOrchestrator
from stock_agent.discovery.schemas import DailyBar, FieldValue, MarketQuote, SecurityMasterRecord, UnknownState
from stock_agent.discovery.sectors import rank_sectors
from stock_agent.discovery.stage import DiscoveryStageEngine
from stock_agent.discovery.scanners import AIBottleneckExpansionScanner
from stock_agent.discovery.backtest import PITBacktester
from stock_agent.discovery.evidence_packet import build_packet, validate_packet
from stock_agent.discovery.expiry import can_promote
from stock_agent.discovery.handoff import EvidencePreflight
from stock_agent.discovery.regime import RegimeHysteresis
from stock_agent.discovery.trajectory import compare_snapshots
from stock_agent.dispatcher import RequestDispatcher
from stock_agent.orchestrator import Orchestrator
from stock_agent.discovery.universe import InMemorySecurityMasterProvider


AS_OF = "2026-08-11T00:00:00+00:00"


def security(ticker: str, sector: str = "Technology", **kwargs) -> SecurityMasterRecord:
    return SecurityMasterRecord(security_id=f"US-{ticker}", ticker=ticker, company_name=ticker,
                                exchange="NASDAQ", sector_canonical=sector,
                                industry_canonical=sector, source="fixture", **kwargs)


def bars(ticker: str, prices: list[float], volume: int = 1_000_000) -> list[DailyBar]:
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    result = []
    for index, price in enumerate(prices):
        day = (start + timedelta(days=index)).date().isoformat()
        result.append(DailyBar(ticker, day, price, price * 1.01, price * .99, price, price,
                               volume, "FIXTURE", f"2026-08-11T00:00:00+00:00",
                               f"2026-08-11T00:00:00+00:00"))
    return result


def quote(ticker: str, price: float = 10.0, cap: float | None = 1_000_000_000) -> MarketQuote:
    return MarketQuote(ticker, FieldValue(price, "KNOWN", "FIXTURE", AS_OF, ingested_at=AS_OF),
                       FieldValue(cap, "KNOWN" if cap is not None else "UNKNOWN_NOT_AVAILABLE", "FIXTURE", AS_OF, ingested_at=AS_OF), AS_OF, "FIXTURE", "CLOSED")


class DiscoveryGoldenFixtureTests(unittest.TestCase):
    def candidate(self, ticker: str, prices: list[float], sector: str = "Technology", volume: int = 1_000_000,
                  cap: float | None = 1_000_000_000):
        record = security(ticker, sector)
        return build_candidate(record, quote(ticker, prices[-1], cap), bars(ticker, prices, volume), "RUN", AS_OF)

    def test_fuel_less_bottom_is_rejected(self):
        candidate = self.candidate("BOTTOM", [10.0] * 61)
        DiscoveryStageEngine().apply(candidate)
        FuelEngine().evaluate(candidate)
        self.assertEqual(candidate.gate_results["fuel_gate"], "FAIL")
        self.assertIn("NO_FUEL", candidate.risk_flags)

    def test_stage3_rocket_is_not_new_entry(self):
        candidate = self.candidate("ROCKET", [5.0] * 41 + [8.0] * 20)
        DiscoveryStageEngine().apply(candidate)
        self.assertEqual(candidate.stage, "DISCOVERY_STAGE_3")
        self.assertIn("REJECT_NEW_ENTRY", candidate.risk_flags)

    def test_unknown_market_cap_is_not_zero_or_pass(self):
        candidate = self.candidate("UNKNOWN", [10.0] * 61, cap=None)
        DiscoveryStageEngine().apply(candidate)
        status, reasons = global_gate(candidate, DiscoveryGateRules())
        self.assertEqual(status, "INELIGIBLE")
        self.assertIn("MARKET_CAP_UNVERIFIED", reasons)
        self.assertIsNone(candidate.fields["market_cap_usd"].value)

    def test_atm_unknown_is_not_clear(self):
        candidate = self.candidate("ATM", [10.0] * 61)
        candidate.fields["atm_status"] = FieldValue(None, UnknownState.UNKNOWN_NOT_FETCHED.value)
        DiscoveryStageEngine().apply(candidate)
        status, reasons = global_gate(candidate, DiscoveryGateRules())
        self.assertEqual(status, "INELIGIBLE")
        self.assertIn("ATM_UNVERIFIED", reasons)

    def test_duplicate_earnings_signals_are_one_family(self):
        candidate = self.candidate("DUP", [10.0] * 61)
        candidate.fuel_events = [
            {"event_id": "E1", "event_type": "EPS_BEAT", "signal_family": "EARNINGS_EVENT", "material": True},
            {"event_id": "E1", "event_type": "REVENUE_BEAT", "signal_family": "EARNINGS_EVENT", "material": True},
            {"event_id": "E1", "event_type": "GUIDANCE_RAISE", "signal_family": "EARNINGS_EVENT", "material": True},
        ]
        FuelEngine().evaluate(candidate)
        self.assertEqual(candidate.signal_families, ["EARNINGS_EVENT"])
        self.assertEqual(candidate.gate_results["fuel_gate"], "FAIL")

    def test_sector_overheat_is_not_early_inflection(self):
        candidates = []
        for index, price in enumerate((14.0, 14.1, 14.2, 9.8, 9.9)):
            candidates.append(self.candidate(f"HOT{index}", [10.0] * 41 + [price] * 20, "Hot", 1_000_000))
        rows = rank_sectors(candidates)
        self.assertEqual(rows[0]["rotation_phase"], "OVERHEATED")

    def test_ai_press_release_without_numbers_is_not_a_bottleneck_hit(self):
        candidate = self.candidate("AIPR", [10.0] * 61)
        DiscoveryStageEngine().apply(candidate)
        result = AIBottleneckExpansionScanner().evaluate(candidate, None)
        self.assertFalse(result.hit)
        self.assertIn("AI_NUMERIC_EVIDENCE_INSUFFICIENT", result.reason_codes)

    def test_weak_sector_leader_keeps_blind_path(self):
        records = [security("WEAK", "Weak Sector"), security("OTHER", "Other Sector")]
        quotes = [quote("WEAK"), quote("OTHER")]
        provider = InMemoryDiscoveryMarketDataProvider(
            quotes, bars("WEAK", [10.0] * 41 + [10.3 + i * .03 for i in range(20)], 1_200_000)
            + bars("OTHER", [10.0] * 61, 1_200_000))
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "blind.sqlite"))
            result = DiscoveryOrchestrator(db, {"report_dir": str(Path(tmp) / "reports"), "discovery": {"enabled": True}},
                                           InMemorySecurityMasterProvider(records), provider).run(as_of=AS_OF)
            weak = next(item for item in result.all_candidates if item.security.ticker == "WEAK")
            self.assertIn("BLIND", weak.paths)

    def test_regime_hysteresis_requires_confirmation_but_panic_is_immediate(self):
        hysteresis = RegimeHysteresis(confirmation_required=2)
        first = hysteresis.apply("RISK_OFF", "BROAD_RISK_ON", 0)
        second = hysteresis.apply("RISK_OFF", first["certified_regime"], first["confirmation_count"])
        panic = hysteresis.apply("PANIC", second["certified_regime"], second["confirmation_count"])
        self.assertFalse(first["changed"])
        self.assertTrue(second["changed"])
        self.assertEqual(panic["certified_regime"], "PANIC")

    def test_pit_backtest_does_not_use_future_filing_or_bar_at_as_of(self):
        provider = InMemoryDiscoveryMarketDataProvider(
            [quote("PIT")], bars("PIT", [10.0] * 45 + [11.0] * 20, 1_000_000))
        result = PITBacktester(InMemorySecurityMasterProvider([security("PIT")]), provider).run("2026-06-15", ["PIT"])
        five = next(item for item in result["results"] if item.horizon_days == 5)
        self.assertIsNotNone(five.forward_return)
        self.assertEqual(result["survivorship_bias_risk"], "SURVIVORSHIP_BIAS_RISK")

    def test_preflight_and_packet_are_fail_closed(self):
        candidate = self.candidate("PKT", [10.0] * 61).to_dict()
        candidate["ticker"] = "PKT"
        packet = build_packet(candidate, {"regime": "UNKNOWN"}, {"rotation_phase": "UNAVAILABLE"})
        valid, missing = validate_packet(packet)
        self.assertTrue(valid, missing)
        feature_candidate = self.candidate("PREF", [10.0] * 61)
        self.assertEqual(EvidencePreflight().evaluate(feature_candidate)["status"], "BLOCKED")

    def test_candidate_trajectory_and_ttl(self):
        trajectory = compare_snapshots({"ticker": "T", "score": 70, "stage": "DISCOVERY_STAGE_1", "fuel": ["FLOW"], "scanner_hits": ["A"]},
                                       {"ticker": "T", "score": 70, "stage": "DISCOVERY_STAGE_1", "fuel": ["FLOW"], "scanner_hits": ["A"]})
        self.assertTrue(trajectory.repeated_without_new_fuel)
        self.assertEqual(can_promote({"expires_at": "2026-01-01", "last_validated_at": "x", "discovery_bucket": "P1_DEEP_ANALYSIS"}, AS_OF)[0], False)

    def test_discord_production_call_path_is_discovery_only(self):
        record = security("DEF", "Defense")
        provider = InMemoryDiscoveryMarketDataProvider(
            [quote("DEF")], bars("DEF", [10.0] * 41 + [10.2 + i * .02 for i in range(20)], 1_200_000))
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "dispatcher.sqlite"))
            config = {
                "database_path": str(Path(tmp) / "dispatcher.sqlite"), "vault_path": str(Path(tmp) / "vault"),
                "report_dir": str(Path(tmp) / "reports"), "mode": "PAPER", "market_data_provider": "mock",
                "agent_provider": "mock", "risk_rules": {"max_position_pct": 10, "max_loss_pct": .75},
                "paper": {"initial_cash_usd": 100000, "account_id": "PAPER_DEFAULT", "max_sector_exposure_pct": 25},
                "obsidian": {"enabled": False}, "discovery": {"enabled": True, "shadow_mode": True},
            }
            app = Orchestrator(config, database=db,
                discovery_security_master=InMemorySecurityMasterProvider([record]),
                discovery_market_data=provider)
            request = CommandInterpreter().parse("방산 섹터 전체 훑어줘 최소 강도로")
            response = RequestDispatcher(app).execute(request)
            self.assertEqual(response["kind"], "DISCOVERY")
            self.assertEqual(response["result"].context.mode, "SECTOR")
            self.assertEqual(response["result"].context.requested_sector, "Defense")
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_transactions").fetchone()[0], 0)

    def test_parser_has_market_and_sector_discovery_intents(self):
        parser = CommandInterpreter()
        market = parser.parse("오늘 미국 시장 전체 훑어서 1~2개월 유망주 찾아줘 최소 강도로")
        sector = parser.parse("방산 섹터 전체 훑어줘")
        self.assertEqual(market.intent, "DISCOVER_MARKET")
        self.assertEqual(market.analysis_intensity, "MINIMUM")
        self.assertEqual(sector.intent, "DISCOVER_SECTOR")
        self.assertEqual(sector.requested_sector, "Defense")

    def test_discovery_end_to_end_is_read_only_and_persists_rejections(self):
        records = [security("AAA", "Technology"), security("BBB", "Industrials"), security("CCC", "Energy")]
        quotes = [quote(record.ticker, 10.0) for record in records]
        all_bars = [bar for record in records for bar in bars(record.ticker, [10.0] * 41 + [10.1 + i * .02 for i in range(20)], 1_200_000)]
        provider = InMemoryDiscoveryMarketDataProvider(quotes, all_bars)
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "discovery.sqlite"))
            db.init()
            db.initialize_paper_account(100_000)
            before = db.paper_account_state()
            config = {"report_dir": str(Path(tmp) / "reports"), "discovery": {"enabled": True, "shadow_mode": True}}
            result = DiscoveryOrchestrator(db, config, InMemorySecurityMasterProvider(records), provider).run(as_of=AS_OF)
            after = db.paper_account_state()
            self.assertEqual(result.status, "COMPLETED_SHADOW_MARKET_ONLY")
            self.assertEqual(before["cash"], after["cash"])
            self.assertEqual(before["open_positions"], after["open_positions"])
            self.assertEqual(len(provider.quote_calls), 1)
            self.assertEqual(len(provider.bar_calls), 3)
            with db.connect() as connection:
                count = connection.execute("SELECT COUNT(*) FROM discovery_candidates WHERE discovery_run_id=?", (result.run_id,)).fetchone()[0]
            self.assertEqual(count, 3)
            second = DiscoveryOrchestrator(db, config, InMemorySecurityMasterProvider(records), provider).run(as_of=AS_OF)
            self.assertEqual(len(provider.bar_calls), 3, "cached completed bars must not be refetched")
            self.assertEqual(result.coverage.to_dict(), second.coverage.to_dict())


if __name__ == "__main__":
    unittest.main()
