from __future__ import annotations

import unittest
from unittest.mock import patch

from stock_agent.adapters import HttpJsonSECProvider, ProviderError
from stock_agent.models import RawArtifact, canonical_hash, utc_now


def artifact(artifact_id, artifact_type, payload):
    stamp = utc_now()
    return RawArtifact(artifact_id, "sec-test", artifact_type, "TEST", stamp, payload, canonical_hash(payload), stamp, stamp)


def submissions(form="10-Q", primary="issuer.htm"):
    return artifact("sub", "SEC_SUBMISSIONS", {
        "cik": "0000000001",
        "name": "Test Issuer",
        "source_url": "https://data.sec.gov/submissions/CIK0000000001.json",
        "filings": {"recent": {
            "form": [form],
            "accessionNumber": ["0000000001-26-000001"],
            "primaryDocument": [primary],
            "filingDate": ["2026-08-10"],
            "reportDate": ["2026-06-30"],
        }},
    })


def facts(ocf=500.0, cash=1000.0, cash_end="2026-06-30", ocf_end="2026-06-30"):
    return artifact("facts", "SEC_FACTS", {"entityName": "Test Issuer", "cik": "0000000001", "facts": {"us-gaap": {
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [{"val": cash, "end": cash_end, "filed": "2026-08-10", "form": "10-Q"}]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [{"val": ocf, "start": "2026-01-01", "end": ocf_end, "filed": "2026-08-10", "form": "10-Q"}]}},
    }}})


class SecLiveAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.provider = HttpJsonSECProvider(user_agent="Stock Agent <audit@stockagent.test>")
        self.identity = {"security_id": "TEST", "cik": "0000000001"}

    def test_wrong_cik_identity_is_rejected(self):
        with self.assertRaises(ProviderError):
            self.provider._assert_issuer_identity({"cik": "0000000002", "name": "Other"}, self.identity, "SEC_SUBMISSIONS")

    def test_wrong_companyfacts_identity_is_rejected(self):
        with self.assertRaises(ProviderError):
            self.provider._assert_issuer_identity({"cik": "0000000002", "entityName": "Other", "facts": {}}, self.identity, "SEC_FACTS")

    def test_positive_operating_cash_flow_is_not_burn(self):
        sub = submissions(); fact = facts(ocf=500.0)
        filing = artifact("filing", "SEC_FILING_DOCUMENT", {"accession_number": "0000000001-26-000001", "form": "10-Q", "primary_document": "issuer.htm", "document": "cash and operating activities"})
        with patch.object(self.provider, "fetch_filings", return_value=filing):
            result = self.provider.fetch_cheap_facts(self.identity, sub, fact)
        self.assertEqual(result.payload["cash_runway_critical"]["state"], "FALSE")
        self.assertIn("positive", result.payload["cash_runway_critical"]["details"]["summary"])
        self.assertEqual(result.payload["xbrl_period_coverage"]["cash_rows"][0]["namespace"], "us-gaap")

    def test_historical_matching_period_cannot_supply_current_runway(self):
        sub = submissions(); fact = facts(ocf=-500.0, cash_end="2025-06-30", ocf_end="2025-06-30")
        filing = artifact("filing", "SEC_FILING_DOCUMENT", {
            "accession_number": "0000000001-26-000001",
            "form": "10-Q",
            "filing_date": "2026-08-10",
            "report_date": "2026-06-30",
            "primary_document": "issuer.htm",
            "document": "cash and operating activities",
        })
        with patch.object(self.provider, "fetch_filings", return_value=filing):
            result = self.provider.fetch_cheap_facts(self.identity, sub, fact)
        self.assertEqual(result.payload["cash_runway_critical"]["state"], "UNKNOWN")

    def test_incompatible_cash_and_ocf_periods_are_unknown(self):
        sub = submissions(); fact = facts(ocf=-500.0, cash_end="2026-06-30", ocf_end="2026-03-31")
        filing = artifact("filing", "SEC_FILING_DOCUMENT", {"accession_number": "0000000001-26-000001", "form": "10-Q", "primary_document": "issuer.htm", "document": "cash and operating activities"})
        with patch.object(self.provider, "fetch_filings", return_value=filing):
            result = self.provider.fetch_cheap_facts(self.identity, sub, fact)
        self.assertEqual(result.payload["cash_runway_critical"]["state"], "UNKNOWN")

    def test_restricted_cash_is_not_used_as_unrestricted_runway(self):
        sub = submissions()
        fact_payload = {
            "entityName": "Test Issuer", "cik": "0000000001", "facts": {"us-gaap": {
                "RestrictedCashAndCashEquivalents": {"units": {"USD": [{"val": 1000.0, "end": "2026-06-30", "filed": "2026-08-10", "form": "10-Q"}]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [{"val": -500.0, "start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-10", "form": "10-Q"}]}},
            }},
        }
        fact = artifact("facts-restricted", "SEC_FACTS", fact_payload)
        filing = artifact("filing", "SEC_FILING_DOCUMENT", {"accession_number": "0000000001-26-000001", "form": "10-Q", "primary_document": "issuer.htm", "filing_date": "2026-08-10", "report_date": "2026-06-30", "document": "restricted cash"})
        with patch.object(self.provider, "fetch_filings", return_value=filing):
            result = self.provider.fetch_cheap_facts(self.identity, sub, fact)
        self.assertEqual(result.payload["cash_runway_critical"]["state"], "UNKNOWN")

    def test_incompatible_xbrl_unit_is_not_used_for_runway(self):
        sub = submissions()
        fact_payload = {
            "entityName": "Test Issuer", "cik": "0000000001", "facts": {"us-gaap": {
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"shares": [{"val": 1000.0, "end": "2026-06-30", "filed": "2026-08-10", "form": "10-Q"}]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [{"val": -500.0, "start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-10", "form": "10-Q"}]}},
            }},
        }
        fact = artifact("facts-unit", "SEC_FACTS", fact_payload)
        filing = artifact("filing", "SEC_FILING_DOCUMENT", {"accession_number": "0000000001-26-000001", "form": "10-Q", "filing_date": "2026-08-10", "report_date": "2026-06-30", "primary_document": "issuer.htm", "document": "cash and operating activities"})
        with patch.object(self.provider, "fetch_filings", return_value=filing):
            result = self.provider.fetch_cheap_facts(self.identity, sub, fact)
        self.assertEqual(result.payload["cash_runway_critical"]["state"], "UNKNOWN")

    def test_expired_warrant_is_not_current(self):
        sub = submissions(); fact = facts(ocf=500.0)
        filing = artifact("filing", "SEC_FILING_DOCUMENT", {"accession_number": "0000000001-26-000001", "form": "10-Q", "primary_document": "issuer.htm", "document": "The warrants expired and are no longer outstanding; exercise price was $5."})
        with patch.object(self.provider, "fetch_filings", return_value=filing):
            result = self.provider.fetch_cheap_facts(self.identity, sub, fact)
        self.assertEqual(result.payload["material_warrant"]["state"], "UNKNOWN")
        self.assertEqual(result.payload["coverage"]["material_warrant"]["temporal_status"], "HISTORICAL")

    def test_repaid_convertible_is_not_current(self):
        sub = submissions(); fact = facts(ocf=500.0)
        filing = artifact("filing", "SEC_FILING_DOCUMENT", {"accession_number": "0000000001-26-000001", "form": "10-Q", "primary_document": "issuer.htm", "document": "The convertible note was repaid and extinguished; its variable conversion price no longer applies."})
        with patch.object(self.provider, "fetch_filings", return_value=filing):
            result = self.provider.fetch_cheap_facts(self.identity, sub, fact)
        self.assertEqual(result.payload["toxic_convertible"]["state"], "UNKNOWN")
        self.assertEqual(result.payload["coverage"]["toxic_convertible"]["temporal_status"], "HISTORICAL")

    def test_terminated_atm_is_not_active(self):
        sub = submissions(); fact = facts(ocf=500.0)
        filing = artifact("filing", "SEC_FILING_DOCUMENT", {"accession_number": "0000000001-26-000001", "form": "10-Q", "primary_document": "issuer.htm", "document": "The at-the-market sales agreement was terminated; remaining capacity was zero."})
        with patch.object(self.provider, "fetch_filings", return_value=filing):
            result = self.provider.fetch_cheap_facts(self.identity, sub, fact)
        self.assertEqual(result.payload["active_atm"]["state"], "UNKNOWN")
        self.assertEqual(result.payload["coverage"]["active_atm"]["temporal_status"], "HISTORICAL")

    def test_future_at_the_market_sales_without_program_is_not_active(self):
        sub = submissions(); fact = facts(ocf=500.0)
        filing = artifact("filing", "SEC_FILING_DOCUMENT", {
            "accession_number": "0000000001-26-000001",
            "form": "10-Q",
            "primary_document": "issuer.htm",
            "document": "Authorized shares may be used for future at-the-market sales and other dilutive issuances.",
        })
        with patch.object(self.provider, "fetch_filings", return_value=filing):
            result = self.provider.fetch_cheap_facts(self.identity, sub, fact)
        self.assertEqual(result.payload["active_atm"]["state"], "UNKNOWN")

    def test_missing_primary_document_is_index_only(self):
        provider = self.provider
        missing = submissions(primary="")
        with patch.object(provider, "fetch_submissions", return_value=missing):
            result = provider.fetch_filings(self.identity, {"form": "10-Q"})
        self.assertEqual(result.artifact_type, "SEC_FILINGS_INDEX")
        self.assertEqual(result.payload["document_status"], "MISSING_PRIMARY_DOCUMENT")

    def test_filing_document_cik_marker_mismatch_is_rejected(self):
        sub = submissions()
        provider = self.provider
        with patch.object(provider, "fetch_submissions", return_value=sub), patch("stock_agent.adapters.urllib.request.urlopen") as opener:
            response = opener.return_value.__enter__.return_value
            response.read.return_value = b"<html>Central Index Key: 0000000002</html>"
            with self.assertRaises(ProviderError):
                provider.fetch_filings(self.identity, {"form": "10-Q"})

    def test_default_filing_prefers_latest_periodic_report_over_8k(self):
        stamp = utc_now()
        payload = {
            "cik": "0000000001",
            "name": "Test Issuer",
            "filings": {"recent": {
                "form": ["8-K", "10-Q"],
                "accessionNumber": ["0000000001-26-000002", "0000000001-26-000001"],
                "primaryDocument": ["issuer-8k.htm", "issuer-10q.htm"],
                "filingDate": ["2026-08-12", "2026-08-10"],
                "reportDate": ["2026-08-12", "2026-06-30"],
            }},
        }
        sub = RawArtifact("sub-periodic", "sec-test", "SEC_SUBMISSIONS", "TEST", stamp, payload, canonical_hash(payload), stamp, stamp)
        with patch.object(self.provider, "fetch_submissions", return_value=sub), patch("stock_agent.adapters.urllib.request.urlopen") as opener:
            response = opener.return_value.__enter__.return_value
            response.read.return_value = b"<html>periodic document</html>"
            result = self.provider.fetch_filings(self.identity)
        self.assertEqual(result.payload["form"], "10-Q")
        self.assertEqual(result.payload["primary_document"], "issuer-10q.htm")


if __name__ == "__main__":
    unittest.main()
