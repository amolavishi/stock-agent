from __future__ import annotations

import json
import unittest
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root
from unittest.mock import patch

from stock_agent.adapters import (
    ConfiguredResearchEvidenceProvider,
    HttpJsonSECProvider,
    deterministic_market_context_from_payload,
)
from stock_agent.models import RawArtifact, canonical_hash, utc_now
from stock_agent.runtime import StockAgent, _merge_deterministic_market_context
from stock_agent.providers import OpenAICompatibleProvider


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = canonical_prompt_library_root()


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class P1RuntimeTransitionTests(unittest.TestCase):
    def test_deterministic_summary_does_not_replace_live_asset_receipts(self):
        receipt = {
            "symbol": "SPY", "unit": "USD_PER_SHARE", "currency": "USD",
            "raw_artifact_id": "raw-1", "evidence_id": "e-1",
            "payload_hash": "hash-1", "observation_count": 30,
        }
        context = {"assets": {"SPY": receipt}, "regime": "UNKNOWN"}
        derived = {
            "assets": {"SPY": {"symbol": "SPY", "observation_count": 30}},
            "regime": "RISK_ON", "breadth": "BROAD", "complete": True,
        }
        merged = _merge_deterministic_market_context(context, derived)
        self.assertIs(merged["assets"]["SPY"], receipt)
        self.assertEqual(merged["assets"]["SPY"]["raw_artifact_id"], "raw-1")
        self.assertEqual(merged["regime"], "RISK_ON")
        self.assertEqual(merged["breadth"], "BROAD")

    def test_toss_raw_candles_are_deterministically_normalized(self):
        payload = [
            {"result": [{"close": 100}, {"close": 101}, {"close": 102}, {"close": 103}]},
            {"result": [{"close": 50}, {"close": 50.5}, {"close": 51}, {"close": 52}]},
        ]
        normalized = deterministic_market_context_from_payload(payload)
        self.assertEqual(normalized["regime"], "RISK_ON")
        self.assertEqual(normalized["breadth"], "BROAD")
        self.assertIn(normalized["volatility"], {"LOW", "NORMAL"})
        self.assertTrue(normalized["complete"])
        self.assertEqual(normalized["normalization_version"], "market-context-v2")

    def test_configured_research_provider_requires_provenance(self):
        provider = ConfiguredResearchEvidenceProvider("https://research.example", "/evidence", timeout=1)
        with patch("urllib.request.urlopen", return_value=_Response({"source_url": "https://issuer.example/ir", "published_at": "2026-08-20T00:00:00Z", "title": "IR", "content": "raw observation"})):
            artifact = provider.fetch("SEC1", {"topic": "earnings"})
        self.assertEqual(artifact.artifact_type, "RESEARCH_EVIDENCE")
        self.assertEqual(artifact.source_observed_at, "2026-08-20T00:00:00Z")
        self.assertEqual(artifact.payload["source_url"], "https://issuer.example/ir")
        self.assertEqual(artifact.payload["provider"], provider.provider_name)

        with patch("urllib.request.urlopen", return_value=_Response({"content": "raw observation"})):
            with self.assertRaises(Exception):
                provider.fetch("SEC1", {})

    def test_configured_research_provider_blocks_private_endpoint_and_strips_secrets(self):
        with self.assertRaises(Exception):
            ConfiguredResearchEvidenceProvider("http://127.0.0.1:8080", "/evidence")
        provider = ConfiguredResearchEvidenceProvider("https://research.example", "/evidence")
        payload = {
            "source_url": "https://issuer.example/ir",
            "published_at": "2026-08-20T00:00:00Z",
            "content": "official release",
            "authorization": "Bearer should-not-persist",
            "api_key": "should-not-persist",
            "nested": {"access_token": "should-not-persist"},
        }
        with patch("urllib.request.urlopen", return_value=_Response(payload)):
            artifact = provider.fetch("SEC1", {"topic": "earnings"})
        serialized = json.dumps(artifact.payload, sort_keys=True)
        self.assertNotIn("should-not-persist", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_sec_cheap_facts_exposes_field_coverage(self):
        provider = HttpJsonSECProvider(user_agent="Stock Agent <audit@stockagent.test>")
        stamp = utc_now()
        submissions_payload = {"filings": {"recent": {"form": ["S-3"], "accessionNumber": ["0000000000-26-000001"], "primaryDocument": ["recorded.htm"]}}}
        facts_payload = {"facts": {"us-gaap": {"ConvertibleDebtCurrent": {"units": {"USD": [{"val": 100}]}}}}}
        submissions = RawArtifact("sub", "sec", "SEC_SUBMISSIONS", "SEC1", stamp, submissions_payload, canonical_hash(submissions_payload), stamp, stamp)
        facts = RawArtifact("facts", "sec", "SEC_FACTS", "SEC1", stamp, facts_payload, canonical_hash(facts_payload), stamp, stamp)
        filing_payload = {"accession_number": "0000000000-26-000001", "document": "at-the-market offering, convertible note and warrant outstanding"}
        filing = RawArtifact("filing", "sec", "SEC_FILING_DOCUMENT", "SEC1", stamp, filing_payload, canonical_hash(filing_payload), stamp, stamp)
        with patch.object(provider, "fetch_filings", return_value=filing):
            artifact = provider.fetch_cheap_facts({"security_id": "SEC1", "cik": "0000000000"}, submissions, facts)
        self.assertEqual(set(artifact.payload["coverage"]), {"active_atm", "large_shelf_and_financing_need", "toxic_convertible", "material_warrant", "imminent_financing", "cash_runway_critical"})
        self.assertEqual(artifact.payload["source_artifact_ids"], ["sub", "facts", "filing"])

    def test_sec_default_filing_skips_ownership_notice_without_primary_document(self):
        provider = HttpJsonSECProvider(user_agent="Stock Agent <audit@stockagent.test>")
        stamp = utc_now()
        submissions = RawArtifact(
            "sub", "sec", "SEC_SUBMISSIONS", "IONQ", stamp,
            {"filings": {"recent": {
                "form": ["SCHEDULE 13G", "10-K"],
                "accessionNumber": ["0000000000-26-000001", "0000000000-26-000002"],
                "primaryDocument": [None, "ionq-10k.htm"],
            }}},
            "hash", stamp, stamp,
        )
        with patch.object(provider, "fetch_submissions", return_value=submissions):
            with patch("urllib.request.urlopen", return_value=_Response({"document": "10-K body"})):
                filing = provider.fetch_filings({"security_id": "IONQ", "cik": "0000000000"})
        self.assertEqual(filing.artifact_type, "SEC_FILING_DOCUMENT")
        self.assertEqual(filing.payload["form"], "10-K")
        self.assertEqual(filing.payload["primary_document"], "ionq-10k.htm")

    def test_luna_high_and_xhigh_wire_profiles(self):
        captured = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"choices":[{"message":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}],"usage":{}}'

        def fake_open(request, timeout=0):
            captured.append(json.loads(request.data.decode("utf-8")))
            return Response()

        with patch("urllib.request.urlopen", side_effect=fake_open):
            OpenAICompatibleProvider("dummy", endpoint="https://luna.example", reasoning_effort="high").call({"prompt_body": "ping"})
            OpenAICompatibleProvider("dummy", endpoint="https://luna.example", reasoning_effort="xhigh").call({"prompt_body": "ping"})
        self.assertEqual([body["reasoning_effort"] for body in captured], ["high", "xhigh"])

    def test_multi_candidate_rank_is_not_first_row_authority(self):
        candidates = [
            {"security_id": "LOW", "reverse_valuation": {"receipt_type": "ReverseValuationReceiptV2", "status": "COMPLETE", "benchmark_implied_upside_pct": 0.10}},
            {"security_id": "HIGH", "reverse_valuation": {"receipt_type": "ReverseValuationReceiptV2", "status": "COMPLETE", "benchmark_implied_upside_pct": 0.80}},
        ]
        data = {"economic_assessments": {
            "LOW": {"bull_value": 11, "base_value": 10, "bear_value": 8, "bull_probability": .2, "base_probability": .5, "bear_probability": .3, "opportunity_cost_score": 2, "current_price": 10},
            "HIGH": {"bull_value": 20, "base_value": 12, "bear_value": 7, "bull_probability": .4, "base_probability": .4, "bear_probability": .2, "opportunity_cost_score": .5, "current_price": 10},
        }}
        ranked = StockAgent._rank_execution_candidates(candidates, data)
        self.assertEqual(ranked[0]["security_id"], "HIGH")


if __name__ == "__main__":
    unittest.main()
