from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root
from unittest.mock import patch

from stock_agent.adapters import RecordedMarketDataProvider, RecordedPortfolioProvider, RecordedResearchEvidenceProvider, RecordedSECProvider, HttpJsonSECProvider
from stock_agent.models import RawArtifact, RunMode, canonical_hash, utc_now
from stock_agent.providers import FakeProvider
from stock_agent.references import ReferenceBuilder, ReferenceContractError, ReferenceRequirement
from stock_agent.reporting import AuthoritativeHuntReportRenderer, ReportContractError
from stock_agent.gates import ContractViolation, validate_sec_artifacts
from stock_agent.runtime import ProductionStockAgent, StockAgentConfig
from stock_agent.store import SQLiteStore
from tests.test_stock_agent import market_context_fixture


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = canonical_prompt_library_root()
FIXTURE = ROOT / "tests" / "fixtures" / "strict_provider_recorded_input.json"


def _fixture() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["provider_recordings"]["market_context"] = market_context_fixture()
    return data


def _agent(data: dict) -> ProductionStockAgent:
    recordings = data["provider_recordings"]
    config = StockAgentConfig(
        LIBRARY,
        Path(":memory:"),
        strict_inputs=True,
        market_data_provider=RecordedMarketDataProvider(recordings),
        sec_provider=RecordedSECProvider(recordings["sec"]),
        portfolio_provider=RecordedPortfolioProvider(recordings["portfolio_snapshot"]),
        research_provider=RecordedResearchEvidenceProvider(recordings["research"]),
    )
    return ProductionStockAgent(config, provider=FakeProvider())


class AuthoritativeReportingTests(unittest.TestCase):
    def test_report_requires_run_id(self):
        with self.assertRaises(ReportContractError):
            AuthoritativeHuntReportRenderer(SQLiteStore(":memory:")).render(None)

    def test_report_is_bound_to_sqlite_run_and_receipts(self):
        agent = _agent(_fixture())
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        report = AuthoritativeHuntReportRenderer(agent.store).render(outcome.run_id)
        self.assertIn(f"run_id: `{outcome.run_id}`", report)
        self.assertIn("authoritative outcome", report)
        self.assertIn("E1", report)
        self.assertIn("QUALIFIED_CANDIDATE_POOL", report)

    def test_labels_without_verified_market_completeness_fail_closed(self):
        data = _fixture()
        data["provider_recordings"]["market_context"] = {
            "complete": False,
            "regime": "RISK_ON",
            "breadth": "BROAD",
            "volatility": "NORMAL",
        }
        outcome = _agent(data).run(RunMode.HUNT_ONLY, {})
        self.assertNotEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")

    def test_historical_convertible_keyword_does_not_become_toxic_true(self):
        provider = HttpJsonSECProvider(user_agent="Stock Agent <audit@stockagent.test>")
        stamp = utc_now()
        submissions_payload = {"name": "Issuer", "filings": {"recent": {"form": ["10-K"], "accessionNumber": ["0000000000-26-000001"], "primaryDocument": ["recorded.htm"]}}}
        facts_payload = {"facts": {"us-gaap": {"ConvertibleDebtCurrent": {"units": {"USD": [{"val": 100}]}}}}}
        filing_payload = {"accession_number": "0000000000-26-000001", "form": "10-K", "primary_document": "recorded.htm", "document": "A historical convertible note was repaid and extinguished; no longer outstanding."}
        submissions = RawArtifact("sub", "sec", "SEC_SUBMISSIONS", "SEC1", stamp, submissions_payload, canonical_hash(submissions_payload), stamp, stamp)
        facts = RawArtifact("facts", "sec", "SEC_FACTS", "SEC1", stamp, facts_payload, canonical_hash(facts_payload), stamp, stamp)
        filing = RawArtifact("filing", "sec", "SEC_FILING_DOCUMENT", "SEC1", stamp, filing_payload, canonical_hash(filing_payload), stamp, stamp)
        with patch.object(provider, "fetch_filings", return_value=filing):
            artifact = provider.fetch_cheap_facts({"security_id": "SEC1", "cik": "0000000000"}, submissions, facts)
        self.assertEqual(artifact.payload["toxic_convertible"]["state"], "UNKNOWN")

    def test_reference_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(":memory:")
            with self.assertRaises(ReferenceContractError):
                ReferenceBuilder(store, directory).build(
                    ReferenceRequirement("../escape", "1", "GENERATED_REFERENCE"),
                    "body",
                    ["receipt:1"],
                )

    def test_index_only_sec_document_is_not_full_forensic_complete(self):
        stamp = utc_now()
        submissions = RawArtifact("sub", "sec", "SEC_SUBMISSIONS", "SEC1", stamp, {"name": "Issuer", "filings": {"recent": {"form": ["10-K"]}}}, "a" * 64, stamp, stamp)
        facts = RawArtifact("facts", "sec", "SEC_FACTS", "SEC1", stamp, {"facts": {"us-gaap": {"Revenue": {}}}}, "b" * 64, stamp, stamp)
        index = RawArtifact("index", "sec", "SEC_FILINGS_INDEX", "SEC1", stamp, {"accession_number": "0000000000-26-000001", "document": "filing index"}, "c" * 64, stamp, stamp)
        with self.assertRaises(ContractViolation):
            validate_sec_artifacts([submissions, facts, index])


if __name__ == "__main__":
    unittest.main()


