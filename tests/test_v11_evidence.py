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
from stock_agent.evidence import (LiveEdgarEvidenceCollector, company_facts_evidence,
                                  market_snapshot_evidence, normalize_evidence_request)
from stock_agent.hermes_agents import _normalize_debate_collections
from stock_agent.schemas import EvidenceItem, MarketSnapshot, now_iso
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

    def test_live_collector_classifies_substantive_transaction_exhibit(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector = LiveEdgarEvidenceCollector(tmp, "Agent test@example.com")
            item = EvidenceItem("E", "IONQ", "SEC", "EX-99", now_iso(),
                                "IONQ Exhibit 99", "https://example.test/ex99",
                                "UNCLASSIFIED", "EXHIBIT", "")
            collector.metadata.collect = lambda ticker: [item]
            collector.downloader.download = lambda value: (
                b"<html><body>Transaction Details. Under the terms of the agreement, "
                b"SkyWater shareholders are receiving cash and shares at close of the "
                b"transaction.</body></html>")

            result = collector.collect("IONQ")

            self.assertEqual(result[0].evidence_grade, "B")
            self.assertEqual(result[0].lifecycle_status, "READY_FOR_ANALYSIS")
            self.assertIn("substantive transaction terms", result[0].grade_reason)

    def test_market_snapshot_becomes_claim_addressable_evidence(self):
        snapshot = MarketSnapshot(
            ticker="IONQ", timestamp=now_iso(), current=40, change_1d_pct=1,
            return_5d_pct=2, return_20d_pct=5, volume=1000, avg_20d_volume=900,
            market_cap_usd=1_000_000, ma20=39, ma50=38, atr_14=2,
            source="TOSS_OPEN_API", is_mock=False)

        item = market_snapshot_evidence(snapshot)

        self.assertEqual(item.source_type, "MARKET_DATA")
        self.assertEqual(item.document_type, "MARKET_SNAPSHOT")
        self.assertEqual(item.evidence_grade, "B")
        self.assertEqual(item.facts["ma50"], 38)
        self.assertEqual(item.lifecycle_status, "READY_FOR_ANALYSIS")

    def test_partial_market_snapshot_is_not_promoted_to_grade_b(self):
        snapshot = MarketSnapshot(
            ticker="IONQ", timestamp=now_iso(), current=40, change_1d_pct=1,
            return_5d_pct=2, return_20d_pct=5, volume=1000, avg_20d_volume=900,
            market_cap_usd=1_000_000, ma20=39, ma50=38, atr_14=2,
            source="TOSS_OPEN_API", data_quality="LOW", is_mock=False,
            indicator_readiness="UNCERTIFIED", volume_validity="INVALID",
            quote_freshness="FRESH", candle_freshness="FRESH")

        item = market_snapshot_evidence(snapshot)

        self.assertEqual(item.evidence_grade, "C")

    def test_companyfacts_becomes_claim_addressable_xbrl_evidence(self):
        item = company_facts_evidence("IONQ", {
            "cik": "1824920",
            "revenue": {"value": 100, "unit": "USD", "fact_id": "X1",
                        "accn": "0001"},
            "derived": {"gross_margin_pct": 42},
            "normalized_facts": [{"fact_id": "X1", "filed": "2026-08-10",
                                   "accn": "0001"}],
        })

        self.assertEqual(item.source_type, "XBRL_FACT")
        self.assertEqual(item.document_type, "COMPANYFACTS")
        self.assertEqual(item.evidence_grade, "B")
        self.assertIn("revenue=100", item.normalized_fact)
        self.assertIn("X1", item.facts["source_fact_ids"])


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
