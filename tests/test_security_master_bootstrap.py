from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_agent.database import Database
from stock_agent.discovery.bootstrap import (
    NasdaqTraderSecurityTypeProvider,
    SecurityMasterBootstrapBuilder,
    SecurityMasterBootstrapError,
    SecurityMasterSnapshotValidationError,
    read_snapshot,
    snapshot_records,
    validate_snapshot,
)
from stock_agent.discovery.health import bootstrap_health
from stock_agent.discovery.ingestion import InMemoryDiscoveryMarketDataProvider
from stock_agent.discovery.providers_live import ValidatedSecurityMasterProvider
from stock_agent.discovery.schemas import DailyBar, FieldValue, MarketQuote, SecurityMasterRecord
from stock_agent.discovery.universe import InMemorySecurityMasterProvider, UniverseIntegrityEngine


AS_OF = "2026-08-11T00:00:00+00:00"
FLAGS = ("is_common_stock", "is_etf", "is_unit", "is_warrant", "is_preferred", "is_adr")


def base_record(ticker: str, cik: int, exchange: str = "NASDAQ") -> SecurityMasterRecord:
    return SecurityMasterRecord(
        f"SEC-{cik}-{ticker}", ticker, f"{ticker} issuer", cik=str(cik).zfill(10),
        exchange=exchange, country="US", is_common_stock=None, is_etf=None,
        is_unit=None, is_warrant=None, is_preferred=None, is_adr=None,
        sector_canonical="UNKNOWN", industry_canonical="UNKNOWN", source="SEC_DIRECTORY",
    )


def identity(common: bool = True, **overrides):
    values = {flag: False for flag in FLAGS}
    values["is_common_stock"] = common
    values.update(overrides)
    return values


class ListingFixture:
    def __init__(self, records):
        self._records = list(records)
        self.URL = "https://www.sec.gov/files/company_tickers_exchange.json"

    def records(self, as_of, refresh=False):
        return list(self._records)


class TypeFixture:
    SOURCES = {"fixture": "https://official.example/security-types"}

    def __init__(self, rows):
        self.rows = list(rows)

    def records(self, as_of, refresh=False):
        return list(self.rows)


class SectorFixture:
    URL = "https://data.sec.gov/submissions/CIK{cik}.json"

    def __init__(self, profiles):
        self.profiles_by_cik = dict(profiles)

    def profiles(self, records, refresh=False):
        return {record.cik: self.profiles_by_cik[record.cik]
                for record in records if record.cik in self.profiles_by_cik}


def type_row(ticker: str, cik: int, name: str, flags: dict, source: str = "OFFICIAL_A"):
    return {
        "ticker": ticker, "cik": str(cik).zfill(10), "company_name": name,
        "exchange": "NASDAQ", "security_type": "COMMON_STOCK" if flags.get("is_common_stock") else "UNKNOWN",
        "identity": dict(flags), "source": source, "source_url": f"https://official.example/{source}",
        "source_as_of": AS_OF,
    }


class SecurityMasterBootstrapTests(unittest.TestCase):
    def builder(self, listing, types, sector=None, directory=None, **kwargs):
        directory = Path(directory or tempfile.mkdtemp())
        return SecurityMasterBootstrapBuilder(
            ListingFixture(listing), TypeFixture(types), sector,
            snapshot_path=directory / "security_master_enrichment.json",
            raw_cache_dir=directory / "raw", normalized_cache_dir=directory / "normalized",
            min_accepted=kwargs.pop("min_accepted", 1),
            min_identity_coverage_pct=kwargs.pop("min_identity_coverage_pct", 95),
            min_sector_coverage_pct=kwargs.pop("min_sector_coverage_pct", 90),
            **kwargs,
        )

    def test_official_nasdaq_directory_parser_maps_explicit_types_and_preserves_unknown(self):
        nasdaq = "\n".join([
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares",
            "AAA|AAA Corp - Class A Common Stock|Q|N|N|100|N|N",
            "AAAW|AAA Corp - Warrant|Q|N|N|100|N|N",
            "AAAU|AAA Corp - Units|Q|N|N|100|N|N",
            "AAAE|AAA Equity ETF|Q|N|N|100|Y|N",
            "AAAX|AAA Corp - Ordinary Shares|Q|N|N|100|N|N",
        ])
        other = "\n".join([
            "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol",
            "BBB|BBB Corp - Preferred Stock|N|BBB|N|100|N|BBB",
        ])

        class Response:
            status = 200
            def __init__(self, content):
                self.content = content.encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return self.content

        def opener(request, timeout=20):
            return Response(other if "otherlisted" in request.full_url else nasdaq)

        with tempfile.TemporaryDirectory() as directory:
            provider = NasdaqTraderSecurityTypeProvider(directory, opener=opener)
            rows = {row["ticker"]: row for row in provider.records(AS_OF)}
            self.assertTrue((Path(directory) / "nasdaq_listed.txt.meta.json").is_file())
            self.assertTrue((Path(directory) / "other_listed.txt.meta.json").is_file())
        self.assertTrue(rows["AAA"]["identity"]["is_common_stock"])
        self.assertTrue(rows["AAAW"]["identity"]["is_warrant"])
        self.assertTrue(rows["AAAU"]["identity"]["is_unit"])
        self.assertTrue(rows["AAAE"]["identity"]["is_etf"])
        self.assertTrue(rows["BBB"]["identity"]["is_preferred"])
        self.assertIsNone(rows["AAAX"]["identity"]["is_common_stock"])

    def test_unknown_identity_is_preserved_and_does_not_become_common_stock(self):
        listing = [base_record("UNKNOWN", 1)]
        with tempfile.TemporaryDirectory() as directory:
            payload = self.builder(listing, [], SectorFixture({}), directory).build()
        row = payload["records"][0]
        self.assertIsNone(row["is_common_stock"])
        self.assertIsNone(row["is_etf"])
        self.assertEqual(payload["metrics"]["accepted_common_stock_count"], 0)
        validate_snapshot(payload)

    def test_source_conflict_is_unknown_conflicted_and_not_selected(self):
        listing = [base_record("CONFLICT", 2)]
        rows = [
            type_row("CONFLICT", 2, "Conflict - Common Stock", identity(True), "OFFICIAL_A"),
            type_row("CONFLICT", 2, "Conflict ETF", identity(False, is_etf=True), "OFFICIAL_B"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            payload = self.builder(listing, rows, SectorFixture({}), directory).build()
        row = payload["records"][0]
        self.assertIsNone(row["is_common_stock"])
        self.assertIsNone(row["is_etf"])
        self.assertEqual(row["identity_states"]["is_common_stock"]["state"], "UNKNOWN_CONFLICTED")
        self.assertEqual(payload["metrics"]["identity_conflict_count"], 1)
        self.assertEqual(payload["metrics"]["accepted_common_stock_count"], 0)

    def test_cik_mismatch_blocks_ticker_only_join(self):
        listing = [base_record("REUSE", 3)]
        rows = [type_row("REUSE", 4, "Different issuer - Common Stock", identity(True))]
        with tempfile.TemporaryDirectory() as directory:
            payload = self.builder(listing, rows, SectorFixture({}), directory).build()
        self.assertIsNone(payload["records"][0]["is_common_stock"])
        self.assertEqual(payload["metrics"]["source_unmatched"], 1)

    def test_supported_scope_coverage_drives_readiness_not_global_coverage(self):
        listing = [base_record(f"S{index:03d}", index + 1) for index in range(75)]
        listing.extend(base_record(f"O{index:03d}", 1000 + index, "OTC") for index in range(25))
        rows = [type_row(f"S{index:03d}", index + 1, "Company - Common Stock", identity(True))
                for index in range(75)]
        profiles = {str(index + 1).zfill(10): {
            "cik": str(index + 1).zfill(10), "sic": "7372", "sic_description": "Services-Prepackaged Software",
            "source": "SEC_SUBMISSIONS", "source_url": "https://data.sec.gov/submissions", "source_as_of": AS_OF,
        } for index in range(75)}
        with tempfile.TemporaryDirectory() as directory:
            payload = self.builder(listing, rows, SectorFixture(profiles), directory).build()
        metrics = payload["metrics"]
        self.assertEqual(metrics["identity_coverage_global_pct"], 75.0)
        self.assertEqual(metrics["identity_coverage_supported_scope_pct"], 100.0)
        self.assertEqual(metrics["accepted_common_stock_count"], 75)
        self.assertEqual(metrics["sector_coverage_pct"], 100.0)
        self.assertTrue(metrics["security_master_ready"])

    def test_security_type_filters_remain_fail_closed(self):
        categories = [
            ("COMMON", 10, identity(True), True),
            ("ETF", 11, identity(False, is_etf=True), False),
            ("WARRANT", 12, identity(False, is_warrant=True), False),
            ("UNIT", 13, identity(False, is_unit=True), False),
            ("PREF", 14, identity(False, is_preferred=True), False),
            ("ADR", 15, identity(False, is_adr=True), False),
        ]
        listing = [base_record(ticker, cik) for ticker, cik, _, _ in categories]
        rows = [type_row(ticker, cik, f"{ticker} security", flags) for ticker, cik, flags, _ in categories]
        with tempfile.TemporaryDirectory() as directory:
            payload = self.builder(listing, rows, SectorFixture({}), directory).build()
        accepted = UniverseIntegrityEngine().build(
            InMemorySecurityMasterProvider(snapshot_records(payload)), AS_OF)["records"]
        self.assertEqual([record.ticker for record in accepted], ["COMMON"])

    def test_sic_mapping_and_unknown_sic_are_deterministic(self):
        listing = [base_record("KNOWN", 20), base_record("MISSING", 21)]
        rows = [type_row("KNOWN", 20, "Known - Common Stock", identity(True)),
                type_row("MISSING", 21, "Missing - Common Stock", identity(True))]
        sector = SectorFixture({
            "0000000020": {"sic": "7372", "sic_description": "Software", "source": "SEC_SUBMISSIONS"},
            "0000000021": {"sic": "9999", "sic_description": "Unknown", "source": "SEC_SUBMISSIONS"},
        })
        with tempfile.TemporaryDirectory() as directory:
            payload = self.builder(listing, rows, sector, directory).build()
        result = {row["ticker"]: row for row in payload["records"]}
        self.assertEqual(result["KNOWN"]["sector_canonical"], "Software/IT Services")
        self.assertEqual(result["MISSING"]["sector_canonical"], "UNKNOWN")

    def test_atomic_publication_preserves_last_known_good_on_validation_failure(self):
        listing = [base_record("AAA", 30)]
        rows = [type_row("AAA", 30, "AAA - Common Stock", identity(True))]
        with tempfile.TemporaryDirectory() as directory:
            builder = self.builder(listing, rows, SectorFixture({"0000000030": {"sic": "7372"}}), directory)
            first = builder.build_and_write(AS_OF)
            path = Path(first["snapshot_path"])
            before = path.read_bytes()
            with patch.object(builder, "build", return_value={"schema_version": "bad", "records": []}):
                with self.assertRaises(SecurityMasterSnapshotValidationError):
                    builder.build_and_write(AS_OF, refresh=True)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(read_snapshot(path)["records"][0]["ticker"], "AAA")

    def test_refresh_updates_existing_and_adds_new_record(self):
        with tempfile.TemporaryDirectory() as directory:
            listing = [base_record("AAA", 40), base_record("BBB", 41)]
            rows = [type_row("AAA", 40, "AAA - Common Stock", identity(True)),
                    type_row("BBB", 41, "BBB - Common Stock", identity(True))]
            sector = SectorFixture({"0000000040": {"sic": "7372"}, "0000000041": {"sic": "7372"}})
            builder = self.builder(listing, rows, sector, directory)
            builder.build_and_write(AS_OF)
            builder.listing_provider._records.append(base_record("CCC", 42))
            builder.security_type_provider.rows.append(
                type_row("CCC", 42, "CCC - Common Stock", identity(True)))
            sector.profiles_by_cik["0000000042"] = {"sic": "7372"}
            refreshed = builder.refresh(AS_OF)
            self.assertEqual({row["ticker"] for row in refreshed["records"]}, {"AAA", "BBB", "CCC"})
            self.assertEqual(refreshed["metrics"]["accepted_common_stock_count"], 3)

    def test_bootstrap_health_uses_snapshot_provider_path_and_does_not_need_toss_for_security_master(self):
        listing = [base_record("READY", 50)]
        rows = [type_row("READY", 50, "Ready - Common Stock", identity(True))]
        sector = SectorFixture({"0000000050": {"sic": "7372"}})

        def bars(ticker, count=21):
            return [DailyBar(ticker, f"2026-05-{index + 1:02d}", 10, 11, 9, 10, 10,
                             1_000_000, "FIXTURE", AS_OF, AS_OF) for index in range(count)]

        with tempfile.TemporaryDirectory() as directory:
            builder = self.builder(listing, rows, sector, directory)
            payload = builder.build_and_write(AS_OF)
            provider = ValidatedSecurityMasterProvider(
                ListingFixture(listing), Path(payload["snapshot_path"]))
            market = InMemoryDiscoveryMarketDataProvider(
                [MarketQuote("READY", FieldValue(10, "KNOWN", "FIXTURE", AS_OF),
                             FieldValue(1_000_000_000, "KNOWN", "FIXTURE", AS_OF), AS_OF, "FIXTURE")],
                bars("READY", 21) + sum((bars(ticker) for ticker in ("SPY", "QQQ", "IWM")), []),
            )
            db = Database(str(Path(directory) / "health.sqlite"))
            health = bootstrap_health(db, provider, market, market)
        self.assertEqual(health["status"], "MARKET_SCAN_READY")
        self.assertEqual(health["universe"]["identity_coverage_global_pct"], 100.0)
        self.assertEqual(health["universe"]["identity_coverage_supported_scope_pct"], 100.0)

    def test_snapshot_validation_rejects_missing_provenance_and_secrets_never_enter_payload(self):
        listing = [base_record("SAFE", 60)]
        rows = [type_row("SAFE", 60, "SAFE - Common Stock", identity(True))]
        with tempfile.TemporaryDirectory() as directory:
            payload = self.builder(listing, rows, SectorFixture({"0000000060": {"sic": "7372"}}), directory).build()
        invalid = json.loads(json.dumps(payload))
        invalid["records"][0]["provenance"] = {}
        with self.assertRaises(SecurityMasterSnapshotValidationError):
            validate_snapshot(invalid)
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("TOSS_APP_SECRET", rendered)
        self.assertNotIn("SEC_USER_AGENT", rendered)

    def test_no_paper_side_effect_from_builder(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "paper.sqlite"))
            db.init()
            db.initialize_paper_account(100_000, "PAPER_DEFAULT", 1.0)
            before = db.paper_account_state()
            builder = self.builder(
                [base_record("SAFE", 70)],
                [type_row("SAFE", 70, "SAFE - Common Stock", identity(True))],
                SectorFixture({"0000000070": {"sic": "7372"}}), directory)
            builder.build_and_write(AS_OF)
            after = db.paper_account_state()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
