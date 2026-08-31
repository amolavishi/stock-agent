from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from stock_agent.catalyst import CatalystGate, extract_catalyst_packet
from stock_agent.catalyst_acquisition_v15 import (
    CATALYST_ACQUISITION_VERSION,
    CatalystAwareYahooFinanceNewsEvidenceProvider,
    CatalystEvidenceCompositeResearchProvider,
    extract_grounded_catalysts,
)
from stock_agent.models import EffectiveRuleSet, RawArtifact, canonical_hash


class _Response:
    def __init__(self, body: bytes, url: str) -> None:
        self._body = body
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size=None):
        return self._body


class _StaticProvider:
    def __init__(self, artifact: RawArtifact, name: str) -> None:
        self.artifact = artifact
        self.provider_name = name
        self.calls = 0

    def fetch(self, subject_id: str, query: dict | None = None) -> RawArtifact:
        self.calls += 1
        return self.artifact


class CatalystAcquisitionV15Tests(unittest.TestCase):
    def test_event_local_quantification_rejects_unrelated_number(self):
        source = {
            "source_class": "MAJOR_MEDIA",
            "source_url": "https://finance.yahoo.com/news/xyz-update",
            "source_observed_at": "2026-08-31T14:00:00Z",
            "title": "XYZ announces a new contract",
            "content": "The company announced a new contract. Far outside the event discussion, an analyst has a $250 target.",
        }
        # The extractor binds numbers to the event-local window. This short
        # example is intentionally still inside the window, so use a very long
        # separator to prove a distant valuation number cannot grant a pass.
        source["content"] = "The company announced a new contract. " + ("background " * 120) + "An analyst has a $250 target."
        self.assertEqual(extract_grounded_catalysts(source), [])

    def test_yahoo_scans_past_first_generic_item_for_grounded_catalyst(self):
        rss = b"""<?xml version='1.0' encoding='UTF-8'?>
        <rss><channel>
          <item>
            <title>XYZ shares trade higher after analyst note</title>
            <link>https://finance.yahoo.com/news/xyz-analyst-note</link>
            <description>XYZ received a routine analyst comment with no company event.</description>
            <pubDate>Mon, 31 Aug 2026 13:00:00 GMT</pubDate>
          </item>
          <item>
            <title>XYZ awarded a $250 million contract</title>
            <link>https://finance.yahoo.com/news/xyz-contract-award</link>
            <description>XYZ was awarded a contract worth $250 million on August 31, 2026 for a multi-year program.</description>
            <pubDate>Mon, 31 Aug 2026 12:00:00 GMT</pubDate>
          </item>
        </channel></rss>"""
        provider = CatalystAwareYahooFinanceNewsEvidenceProvider()
        with patch("urllib.request.urlopen", return_value=_Response(rss, provider.BASE_URL)):
            artifact = provider.fetch("XYZ", {"catalyst_news_scan_limit": 30})
        payload = artifact.payload
        self.assertEqual(payload["catalyst_acquisition"]["issuer_identifiable_items_scanned"], 2)
        self.assertGreaterEqual(len(payload["catalysts"]), 1)
        self.assertEqual(payload["catalysts"][0]["event_type"], "CONTRACT_AWARD")
        self.assertEqual(payload["source_url"], "https://finance.yahoo.com/news/xyz-contract-award")

        packet = extract_catalyst_packet(
            payload,
            artifact_id=artifact.artifact_id,
            evidence_id="E-RESEARCH",
            fallback_source_observed_at=artifact.source_observed_at,
        )
        receipt = CatalystGate().evaluate(
            packet,
            EffectiveRuleSet(max_age_research_hours=24 * 45),
            now=datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(receipt.decision.value, "PASS")

    def test_unquantified_headline_does_not_create_catalyst(self):
        source = {
            "source_class": "COMPANY_IR",
            "source_url": "https://investor.xyz.com/news/new-contract",
            "source_observed_at": "2026-08-31T14:00:00Z",
            "title": "XYZ awarded a strategic contract",
            "content": "The customer and financial terms were not disclosed.",
        }
        self.assertEqual(extract_grounded_catalysts(source), [])

    def test_composite_attempts_both_authority_lanes_and_uses_secondary_catalyst(self):
        issuer_payload = {
            "security_id": "XYZ",
            "source_class": "COMPANY_IR",
            "source_url": "https://investor.xyz.com/news/general-update",
            "source_observed_at": "2026-08-30T12:00:00Z",
            "provider": "issuer-ir-html",
            "title": "XYZ general update",
            "content": "XYZ published a general corporate update without a quantified catalyst.",
        }
        issuer = RawArtifact(
            "issuer-art", "issuer-ir-html", "RESEARCH_EVIDENCE", "XYZ",
            issuer_payload["source_observed_at"], issuer_payload, canonical_hash(issuer_payload),
            issuer_payload["source_observed_at"], issuer_payload["source_observed_at"],
        )
        media_payload = {
            "security_id": "XYZ",
            "source_class": "MAJOR_MEDIA",
            "source_url": "https://finance.yahoo.com/news/xyz-guidance",
            "source_observed_at": "2026-08-31T12:00:00Z",
            "provider": "test-media",
            "title": "XYZ raises guidance by 20%",
            "content": "XYZ raised full-year guidance by 20% on August 31, 2026.",
        }
        media = RawArtifact(
            "media-art", "test-media", "RESEARCH_EVIDENCE", "XYZ",
            media_payload["source_observed_at"], media_payload, canonical_hash(media_payload),
            media_payload["source_observed_at"], media_payload["source_observed_at"],
        )
        primary = _StaticProvider(issuer, "issuer-ir-html")
        secondary = _StaticProvider(media, "test-media")
        provider = CatalystEvidenceCompositeResearchProvider(primary, secondary)
        bundle = provider.fetch("XYZ", {})
        self.assertEqual(primary.calls, 1)
        self.assertEqual(secondary.calls, 1)
        self.assertEqual(bundle.payload["catalyst_acquisition"]["attempted_lanes"], ["ISSUER_IR", "SECONDARY_MEDIA"])
        self.assertFalse(bundle.payload["catalyst_acquisition"]["cost_cap_applied"])
        self.assertTrue(bundle.payload["catalysts"])
        self.assertEqual(bundle.payload["catalysts"][0]["event_type"], "GUIDANCE_RAISE")
        self.assertEqual(bundle.payload["catalyst_acquisition"]["version"], CATALYST_ACQUISITION_VERSION)


if __name__ == "__main__":
    unittest.main()
