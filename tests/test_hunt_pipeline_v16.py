from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import stock_agent.hunt_pipeline_v16 as hunt_pipeline_v16_module
from stock_agent.catalyst import CatalystGate
from stock_agent.catalyst_extractor_v16 import install_v16_extractor
from stock_agent.hunt_pipeline_v16 import (
    HUNT_PIPELINE_VERSION,
    V16MultiSourceResearchProvider,
    V16YahooEvidenceProvider,
    _ResearchAdmissionReceipt,
    _starvation_state,
    evidence_plan_for_lane,
)
from stock_agent.models import EffectiveRuleSet, GateDecision, RawArtifact, canonical_hash

# Production __main__ installs the same augmentation before CLI construction.
install_v16_extractor(hunt_pipeline_v16_module)


class _Response:
    def __init__(self, body: bytes, url: str) -> None:
        self.body = body
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size=None):
        return self.body


class _FailingDelegate:
    provider_name = "delegate"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, subject_id: str, query: dict | None = None) -> RawArtifact:
        self.calls += 1
        raise RuntimeError("delegate unavailable")


class _NoCatalystDelegate:
    provider_name = "delegate"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, subject_id: str, query: dict | None = None) -> RawArtifact:
        self.calls += 1
        payload = {
            "security_id": subject_id,
            "source_class": "MAJOR_MEDIA",
            "source_url": f"https://finance.yahoo.com/news/{subject_id.lower()}-general",
            "source_observed_at": "2026-08-31T12:00:00Z",
            "provider": self.provider_name,
            "title": f"{subject_id} general update",
            "content": f"{subject_id} published a general update without quantified catalyst evidence.",
        }
        return RawArtifact(
            f"art-{self.calls}", self.provider_name, "RESEARCH_EVIDENCE", subject_id,
            payload["source_observed_at"], payload, canonical_hash(payload),
            payload["source_observed_at"], payload["source_observed_at"],
        )


class _SEC:
    provider_name = "fake-sec"

    def __init__(self, document: str = "Routine filing without a catalyst.") -> None:
        self.document = document
        self.calls: list[str] = []

    def resolve_cik(self, ticker: str) -> str:
        return "0000000123"

    def fetch_filings(self, identity: dict, query: dict | None = None) -> RawArtifact:
        form = str((query or {}).get("form") or "8-K")
        self.calls.append(form)
        payload = {
            "form": form,
            "filing_date": "2026-08-31T00:00:00Z",
            "source_url": f"https://www.sec.gov/Archives/edgar/data/123/{form.replace('-', '').lower()}.htm",
            "document": self.document,
        }
        return RawArtifact(
            f"sec-{form}", self.provider_name, "SEC_FILING_DOCUMENT", identity["security_id"],
            payload["filing_date"], payload, canonical_hash(payload), payload["filing_date"], payload["filing_date"],
        )


class HuntPipelineV16Tests(unittest.TestCase):
    def test_initial_catalyst_insufficiency_is_research_admission_only_not_final_pass(self):
        packet = {"catalysts": []}
        strict = CatalystGate().evaluate(
            packet,
            EffectiveRuleSet(max_age_research_hours=24 * 45),
            now=datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(strict.decision, GateDecision.INSUFFICIENT_EVIDENCE)
        admission = _ResearchAdmissionReceipt(strict)
        self.assertEqual(admission.decision, GateDecision.PASS)
        self.assertEqual(admission.as_dict()["decision"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(admission.as_dict()["final_authority"])

    def test_post_research_resolution_restores_strict_catalyst_authority(self):
        initial = CatalystGate().evaluate({}, EffectiveRuleSet())
        admission = _ResearchAdmissionReceipt(initial)
        valid = {
            "catalysts": [{
                "catalyst_id": "C1",
                "event_type": "CONTRACT_AWARD",
                "event_at": "2026-08-31T12:00:00Z",
                "verification_status": "OFFICIAL",
                "binding_status": "BINDING",
                "economic_transmission": {"metric": "revenue", "direction": "POSITIVE", "amount": 250_000_000},
                "confirmation_metric": "Confirm revenue conversion",
                "source_url": "https://www.sec.gov/Archives/edgar/data/123/contract.htm",
                "source_observed_at": "2026-08-31T12:00:00Z",
                "artifact_id": "A1",
                "evidence_id": "E1",
            }]
        }
        final = CatalystGate().evaluate(
            valid,
            EffectiveRuleSet(max_age_research_hours=24 * 45),
            now=datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(final.decision, GateDecision.PASS)
        admission.final = final
        self.assertEqual(admission.decision, GateDecision.PASS)
        self.assertEqual(admission.as_dict()["decision"], "PASS")
        self.assertTrue(admission.as_dict()["final_authority"])

    def test_lane_specific_evidence_plans_are_not_generic(self):
        refinancing = evidence_plan_for_lane("10")
        customer = evidence_plan_for_lane("12")
        policy = evidence_plan_for_lane("05")
        self.assertIn("SEC_CREDIT_OR_FINANCING", refinancing["requested_source_lanes"])
        self.assertIn("CUSTOMER_OR_INDUSTRY", customer["requested_source_lanes"])
        self.assertIn("GOVERNMENT_OR_REGULATOR", policy["requested_source_lanes"])
        for plan in (refinancing, customer, policy):
            self.assertFalse(plan["grade_authority"])
            self.assertFalse(plan["execution_authority"])
            self.assertEqual(plan["version"], HUNT_PIPELINE_VERSION)

    def test_yahoo_malformed_item_is_skipped_and_valid_item_continues(self):
        rss = b"""<?xml version='1.0'?><rss><channel>
        <item><title>CME bad link update</title><link>ftp://bad.example/cme</link>
        <description>CME routine update</description><pubDate>Mon, 31 Aug 2026 13:00:00 GMT</pubDate></item>
        <item><title>CME awarded a $250 million contract</title>
        <link>https://finance.yahoo.com/news/cme-contract</link>
        <description>CME was awarded a $250 million contract on August 31, 2026.</description>
        <pubDate>Mon, 31 Aug 2026 12:00:00 GMT</pubDate></item>
        </channel></rss>"""
        article = b"<html><head><title>CME contract</title></head><body>CME was awarded a $250 million contract on August 31, 2026.</body></html>"
        provider = V16YahooEvidenceProvider()
        with patch("urllib.request.urlopen", side_effect=[
            _Response(rss, provider.BASE_URL),
            _Response(article, "https://finance.yahoo.com/news/cme-contract"),
        ]):
            artifact = provider.fetch("CME", {"v8_lane_terms": ["contract"], "catalyst_news_scan_limit": 30})
        acquisition = artifact.payload["evidence_acquisition"]
        self.assertEqual(acquisition["full_article_fetch_success"], 1)
        self.assertTrue(acquisition["item_errors"])
        self.assertGreaterEqual(acquisition["grounded_catalyst_count"], 1)
        self.assertEqual(artifact.payload["catalysts"][0]["event_type"], "CONTRACT_AWARD")

    def test_multisource_provider_uses_sec_when_delegate_fails(self):
        sec = _SEC("CME was awarded a $300 million contract on August 31, 2026.")
        delegate = _FailingDelegate()
        provider = V16MultiSourceResearchProvider(delegate, sec, lane_resolver=lambda sid: "10")
        artifact = provider.fetch("CME", {})
        acquisition = artifact.payload["evidence_acquisition"]
        self.assertEqual(set(sec.calls), {"8-K", "10-Q", "10-K"})
        self.assertIn("SEC_8K", acquisition["successful_lanes"])
        self.assertIn("GROUNDED_CATALYST", acquisition["successful_lanes"])
        self.assertTrue(artifact.payload["catalysts"])
        self.assertFalse(acquisition["grade_authority"])
        self.assertFalse(acquisition["pre_a_authority"])
        self.assertFalse(acquisition["execution_authority"])

    def test_no_catalyst_triggers_refresh_before_source_exhausted(self):
        delegate = _NoCatalystDelegate()
        sec = _SEC("Routine filing with no qualifying event evidence.")
        provider = V16MultiSourceResearchProvider(delegate, sec, lane_resolver=lambda sid: "11")
        artifact = provider.fetch("XYZ", {})
        acquisition = artifact.payload["evidence_acquisition"]
        self.assertEqual(delegate.calls, 2)
        self.assertEqual(acquisition["refresh_attempts"], 1)
        self.assertTrue(acquisition["source_exhausted"])
        self.assertEqual(acquisition["state"], "SOURCE_EXHAUSTED_AVAILABLE_LANES")
        self.assertEqual(acquisition["grounded_catalyst_count"], 0)

    def test_pipeline_starvation_is_explicit_engineering_state(self):
        self.assertEqual(
            _starvation_state({"CAPITAL_PRESCREEN_PASS": 14, "DEEP_RESEARCH": 0}),
            (14, "PRESCREEN_SURVIVORS_NEVER_REACHED_DEEP_RESEARCH"),
        )
        self.assertEqual(
            _starvation_state({"CAPITAL_PRESCREEN_PASS": 14, "DEEP_RESEARCH": 4, "FULL_SEC_FORENSIC": 0}),
            (4, "DEEP_RESEARCH_NEVER_REACHED_FULL_SEC"),
        )
        self.assertEqual(
            _starvation_state({"CAPITAL_PRESCREEN_PASS": 14, "DEEP_RESEARCH": 4, "FULL_SEC_FORENSIC": 3, "ADVERSARIAL_AUDIT": 0}),
            (3, "FULL_SEC_NEVER_REACHED_ADVERSARIAL_AUDIT"),
        )
        self.assertEqual(
            _starvation_state({"CAPITAL_PRESCREEN_PASS": 14, "DEEP_RESEARCH": 4, "FULL_SEC_FORENSIC": 3, "ADVERSARIAL_AUDIT": 2}),
            (0, None),
        )


if __name__ == "__main__":
    unittest.main()
