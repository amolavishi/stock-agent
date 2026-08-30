from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from stock_agent.adapters import HttpJsonSECProvider, ProviderError, _sec_source_time
from stock_agent.models import RawArtifact, canonical_hash
from stock_agent.store import SQLiteStore


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.url = "https://data.sec.gov/test.json"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return json.dumps(self.payload).encode("utf-8")


class SecTimestampSemanticsTests(unittest.TestCase):
    def test_future_report_period_never_becomes_submission_source_time(self):
        payload = {"filings": {"recent": {
            "filingDate": ["2026-08-14"],
            "acceptanceDateTime": ["2026-08-14T20:22:32.000Z"],
            "reportDate": ["2026-09-24"],
        }}}
        self.assertEqual(_sec_source_time(payload, "SEC_SUBMISSIONS"), "2026-08-14T20:22:32.000Z")

    def test_future_xbrl_period_and_contract_dates_never_poison_facts(self):
        payload = {"facts": {"us-gaap": {
            "Revenue": {"units": {"USD": [{
                "start": "2026-07-01", "end": "2026-09-24", "filed": "2026-08-14",
                "maturityDate": "2027-01-01", "expirationDate": "2028-01-01",
            }]}}
        }}}
        self.assertEqual(_sec_source_time(payload, "SEC_FACTS"), "2026-08-14")

    def test_future_filing_and_acceptance_are_rejected_at_persistence_boundary(self):
        future = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = {"filings": {"recent": {"filingDate": [future], "acceptanceDateTime": [future]}}}
        artifact = RawArtifact("future-sec", "sec-edgar-http", "SEC_SUBMISSIONS", "AMC",
                               future, payload, canonical_hash(payload), future, datetime.now(timezone.utc).isoformat())
        with SQLiteStore(":memory:") as store:
            with self.assertRaises(ValueError):
                store.save_raw_artifact(artifact)

    def test_retrieved_before_source_is_rejected(self):
        payload = {"filings": {"recent": {"filingDate": ["2026-08-24"]}}}
        artifact = RawArtifact("clock-skew", "sec-edgar-http", "SEC_SUBMISSIONS", "AMC",
                               "2026-08-24T00:00:00Z", payload, canonical_hash(payload),
                               "2026-08-24T12:00:00Z", "2026-08-24T00:00:00Z")
        with SQLiteStore(":memory:") as store:
            with self.assertRaises(ValueError):
                store.save_raw_artifact(artifact)

    def test_malformed_source_timestamp_is_rejected(self):
        payload = {"filings": {"recent": {"filingDate": ["not-a-timestamp"]}}}
        artifact = RawArtifact(
            "malformed-sec", "sec-edgar-http", "SEC_SUBMISSIONS", "AMC",
            "not-a-timestamp", payload, canonical_hash(payload),
            "not-a-timestamp", "2026-08-24T00:00:00Z",
        )
        with SQLiteStore(":memory:") as store:
            with self.assertRaises(ValueError):
                store.save_raw_artifact(artifact)

    def test_sec_get_uses_only_publication_metadata(self):
        payload = {"name": "AMC", "filings": {"recent": {
            "filingDate": ["2026-08-14"],
            "acceptanceDateTime": ["2026-08-14T20:22:32.000Z"],
            "reportDate": ["2026-09-24"],
        }}}
        provider = HttpJsonSECProvider(user_agent="Stock Agent <audit@stockagent.test>", min_interval=0)
        with patch("stock_agent.adapters.urllib.request.urlopen", return_value=_Response(payload)):
            artifact = provider._get("submissions/CIK0001411579.json", "SEC_SUBMISSIONS", "AMC")
        self.assertEqual(artifact.source_observed_at, "2026-08-14T20:22:32.000Z")

    def test_derived_cheap_facts_preserve_source_publication_time(self):
        provider = HttpJsonSECProvider(user_agent="Stock Agent <audit@stockagent.test>", min_interval=0)
        submissions = RawArtifact(
            "sub-derived", "sec-edgar-http", "SEC_SUBMISSIONS", "AMC",
            "2026-08-20T00:00:00Z", {"name": "AMC"}, canonical_hash({"name": "AMC"}),
            "2026-08-20T00:00:00Z", "2026-08-24T00:00:00Z",
        )
        facts = RawArtifact(
            "facts-derived", "sec-edgar-http", "SEC_FACTS", "AMC",
            "2026-08-21T00:00:00Z", {"entityName": "AMC", "facts": {}}, canonical_hash({"entityName": "AMC", "facts": {}}),
            "2026-08-21T00:00:00Z", "2026-08-24T00:00:00Z",
        )
        filing_payload = {"accession_number": "0001411579-26-000001", "form": "10-Q", "primary_document": "amc.htm", "filing_date": "2026-08-22", "document": ""}
        filing = RawArtifact(
            "filing-derived", "sec-edgar-http", "SEC_FILING_DOCUMENT", "AMC",
            "2026-08-22T00:00:00Z", filing_payload, canonical_hash(filing_payload),
            "2026-08-22T00:00:00Z", "2026-08-24T00:00:00Z",
        )
        with patch.object(provider, "fetch_filings", return_value=filing):
            result = provider.fetch_cheap_facts({"security_id": "AMC", "cik": "0001411579"}, submissions, facts)
        self.assertEqual(result.source_observed_at, "2026-08-22T00:00:00Z")
        self.assertNotEqual(result.source_observed_at, result.retrieved_at)


if __name__ == "__main__":
    unittest.main()
