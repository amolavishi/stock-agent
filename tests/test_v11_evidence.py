from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from stock_agent.capital_structure import build_capital_structure, sector_from_sic
from stock_agent.edgar_documents import EvidenceClassifier
from stock_agent.evidence import LiveEdgarEvidenceCollector, normalize_evidence_request
from stock_agent.hermes_agents import _normalize_debate_collections
from stock_agent.schemas import EvidenceItem, now_iso
from stock_agent.sec import EdgarError, EdgarMetadataCollector, SECCompanyFactsProvider


class EvidenceRequestTests(unittest.TestCase):
    def test_string_evidence_request_is_normalized(self):
        request = normalize_evidence_request("ATM 잔여 여력을 확인하라", 2)
        self.assertEqual(request.question, "ATM 잔여 여력을 확인하라")
        self.assertIn("S-3", request.target_forms)

    def test_agent_string_collections_become_structured_objects(self):
        result = _normalize_debate_collections({
            "evidence_requests": ["424B7 등록 주식 수 확인"],
            "issue_updates": ["희석 규모 미확인"],
        })
        self.assertEqual(result["evidence_requests"][0]["question"], "424B7 등록 주식 수 확인")
        self.assertEqual(result["issue_updates"][0]["status"], "OPEN")

    def test_atm_and_contract_requests_change_forms_and_keywords(self):
        atm = normalize_evidence_request({"question": "현재 ATM 잔여 여력을 확인하라"}, 2)
        contract = normalize_evidence_request({"question": "계약이 funded인지 확인하라"}, 2)
        self.assertIn("S-3", atm.target_forms)
        self.assertIn("424B5", atm.target_forms)
        self.assertIn("remaining capacity", atm.keywords)
        self.assertEqual(set(contract.target_forms), {"8-K", "10-Q", "10-K"})
        self.assertIn("funded", contract.keywords)
        self.assertNotEqual(atm.request_id, contract.request_id)

    def test_collect_for_request_uses_directed_metadata_and_keywords(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector = LiveEdgarEvidenceCollector(tmp, "Agent test@example.com")
            calls = {}
            item = EvidenceItem("E", "IONQ", "SEC", "S-3", now_iso(), "x", "https://x",
                                "UNCLASSIFIED", "FILING", "metadata")

            def collect(ticker, **kwargs):
                calls.update(kwargs)
                return [item]

            collector.metadata.collect = collect
            collector.downloader.download = lambda value: b"prospectus at-the-market remaining capacity"
            request = normalize_evidence_request({"question": "ATM remaining capacity"}, 1)
            result = collector.collect_for_request("IONQ", request)
            self.assertEqual(calls["target_forms"], set(request.target_forms))
            self.assertEqual(result[0].query_request_id, request.request_id)
            self.assertIn("remaining capacity", result[0].normalized_fact)

    def test_sec_403_is_typed_and_never_falls_back(self):
        error = urllib.error.HTTPError("https://sec", 403, "Forbidden", {}, io.BytesIO())
        collector = EdgarMetadataCollector("Agent test@example.com", max_attempts=1)
        with patch("stock_agent.sec.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(EdgarError, "SEC_ACCESS_DENIED"):
                collector._get_json("https://sec")

    def test_grade_requires_economic_text_not_form_alone(self):
        item = EvidenceItem("E", "IONQ", "SEC", "S-3", now_iso(), "x", "u",
                            "UNCLASSIFIED", "FILING", "")
        classified = EvidenceClassifier().classify(item, "ordinary boilerplate")
        self.assertEqual(classified.evidence_grade, "UNCLASSIFIED")
        classified = EvidenceClassifier().classify(item, "registration statement for an offering of securities")
        self.assertEqual(classified.evidence_grade, "C")
        self.assertTrue(classified.grade_reason)


class CompanyFactsCapitalTests(unittest.TestCase):
    def test_normalized_facts_preserve_unit_concept_and_prefer_quarter_frame(self):
        payload = {"facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                {"start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-01",
                 "val": 300, "form": "10-Q", "fy": 2026, "fp": "Q2", "accn": "A"},
                {"start": "2026-04-01", "end": "2026-06-30", "filed": "2026-08-01",
                 "val": 180, "form": "10-Q", "fy": 2026, "fp": "Q2", "accn": "A",
                 "frame": "CY2026Q2"},
            ]}}
        }}}
        rows = SECCompanyFactsProvider._normalized_rows(payload)
        selected = SECCompanyFactsProvider._select_period_aware(rows)
        self.assertEqual(selected["value"], 180)
        self.assertEqual(selected["unit"], "USD")
        self.assertEqual(selected["concept"], "RevenueFromContractWithCustomerExcludingAssessedTax")
        self.assertEqual(selected["period_type"], "DURATION")

    def test_capital_snapshot_distinguishes_known_and_unknown(self):
        facts = {
            "shares_outstanding": {"value": 1000}, "cash": {"value": 500},
            "stock_based_compensation": None,
            "derived": {"cash_burn": 100, "estimated_runway_months": 60},
            "normalized_facts": [{"filed": "2026-08-01"}],
        }
        evidence = [EvidenceItem("E", "IONQ", "SEC", "S-3", "2026-08-01", "x", "u", "C",
                                 "FILING", "at-the-market warrant offering",
                                 normalized_fact="at-the-market warrant offering")]
        snapshot = build_capital_structure("IONQ", facts, evidence)
        self.assertEqual(snapshot.shares_outstanding, 1000)
        self.assertEqual(snapshot.warrants, "UNKNOWN")
        self.assertIn("atm_capacity", snapshot.unknown_fields)
        self.assertEqual(sector_from_sic("7372"), "Software/IT Services")


if __name__ == "__main__":
    unittest.main()
