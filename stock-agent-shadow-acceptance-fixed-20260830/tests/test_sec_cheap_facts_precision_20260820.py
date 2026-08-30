from __future__ import annotations

import unittest
from unittest.mock import patch

from stock_agent.adapters import HttpJsonSECProvider
from stock_agent.models import RawArtifact, canonical_hash, utc_now


class SecCheapFactsPrecisionTests(unittest.TestCase):
    def _artifact(self, document: str, form: str = "S-3", facts_payload: dict | None = None):
        provider = HttpJsonSECProvider(user_agent="StockAgent Audit <audit@stockagent.test>")
        stamp = utc_now()
        submissions_payload = {
            "name": "Issuer",
            "filings": {
                "recent": {
                    "form": [form],
                    "accessionNumber": ["0000000000-26-000001"],
                    "primaryDocument": ["recorded.htm"],
                }
            },
        }
        facts_payload = facts_payload or {"facts": {"us-gaap": {}}}
        submissions = RawArtifact("sub", "sec", "SEC_SUBMISSIONS", "SEC1", stamp, submissions_payload, canonical_hash(submissions_payload), stamp, stamp)
        facts = RawArtifact("facts", "sec", "SEC_FACTS", "SEC1", stamp, facts_payload, canonical_hash(facts_payload), stamp, stamp)
        filing_payload = {
            "accession_number": "0000000000-26-000001",
            "form": form,
            "primary_document": "recorded.htm",
            "document": document,
        }
        filing = RawArtifact("filing", "sec", "SEC_FILING_DOCUMENT", "SEC1", stamp, filing_payload, canonical_hash(filing_payload), stamp, stamp)
        with patch.object(provider, "fetch_filings", return_value=filing):
            return provider.fetch_cheap_facts({"security_id": "SEC1", "cik": "0000000000"}, submissions, facts)

    def test_historical_convertible_does_not_create_toxic_true(self):
        artifact = self._artifact(
            "The convertible note issued in 2022 was fully repaid and is no longer outstanding."
        )
        self.assertEqual(artifact.payload["toxic_convertible"]["state"], "UNKNOWN")

    def test_unrelated_historical_language_does_not_poison_current_toxic_convertible(self):
        artifact = self._artifact(
            "A legacy warrant expired in 2021. As of this filing, a convertible note remains outstanding "
            "with a variable-price conversion feature and a conversion price reset based on market price."
        )
        self.assertEqual(artifact.payload["toxic_convertible"]["state"], "TRUE")
        coverage = artifact.payload["coverage"]["toxic_convertible"]
        self.assertEqual(coverage["extraction_version"], "3")
        self.assertIsNotNone(coverage["matched_text_window_hash"])
        self.assertGreater(coverage["matched_text_window_length"], 0)

    def test_atm_program_without_remaining_capacity_is_not_active_true(self):
        artifact = self._artifact(
            "The company maintains an at-the-market sales agreement with its sales agent."
        )
        self.assertEqual(artifact.payload["active_atm"]["state"], "UNKNOWN")

    def test_current_atm_requires_program_and_remaining_capacity(self):
        artifact = self._artifact(
            "As of this filing, the at-the-market sales agreement remains in effect and has "
            "$42 million of remaining available capacity under the program."
        )
        self.assertEqual(artifact.payload["active_atm"]["state"], "TRUE")
        self.assertTrue(artifact.payload["coverage"]["active_atm"]["capacity_observed"])

    def test_terminated_atm_is_not_active_true(self):
        artifact = self._artifact(
            "The at-the-market sales agreement was terminated and is no longer available."
        )
        self.assertEqual(artifact.payload["active_atm"]["state"], "UNKNOWN")

    def test_historical_warrant_does_not_create_material_true(self):
        artifact = self._artifact(
            "All warrants expired in 2023 and no warrants remain outstanding."
        )
        self.assertEqual(artifact.payload["material_warrant"]["state"], "UNKNOWN")

    def test_current_warrant_requires_current_status_and_economics(self):
        artifact = self._artifact(
            "As of this filing, warrants remain outstanding to purchase common stock at an exercise price of $8.50 per share."
        )
        self.assertEqual(artifact.payload["material_warrant"]["state"], "TRUE")

    def test_shelf_capacity_alone_is_not_imminent_financing(self):
        artifact = self._artifact(
            "The company has an effective universal shelf registration statement and may raise capital from time to time."
        )
        self.assertEqual(artifact.payload["imminent_financing"]["state"], "UNKNOWN")

    def test_generic_registration_statement_and_liquidity_language_is_not_large_shelf(self):
        artifact = self._artifact(
            "The company may need liquidity and effective registration statements to access its financing sources."
        )
        self.assertEqual(artifact.payload["large_shelf_and_financing_need"]["state"], "UNKNOWN")

    def test_announced_registered_direct_offering_can_be_imminent(self):
        artifact = self._artifact(
            "The company announced a registered direct offering and entered into a securities purchase agreement "
            "to sell shares for expected gross proceeds of $75 million, with closing expected shortly."
        )
        self.assertEqual(artifact.payload["imminent_financing"]["state"], "TRUE")
        self.assertIsNotNone(artifact.payload["coverage"]["imminent_financing"]["matched_text_window_hash"])

    def test_unknown_field_has_no_fake_summary_window_hash(self):
        artifact = self._artifact("No relevant capital structure disclosure appears in this filing.")
        coverage = artifact.payload["coverage"]["active_atm"]
        self.assertEqual(artifact.payload["active_atm"]["state"], "UNKNOWN")
        self.assertIsNone(coverage["matched_text_window_hash"])
        self.assertEqual(coverage["matched_text_window_length"], 0)


if __name__ == "__main__":
    unittest.main()
