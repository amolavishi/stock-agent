from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from stock_agent.adapters import IssuerIRWebEvidenceProvider, ProviderError


class _HTMLResponse:
    def __init__(self, html: str, url: str = "https://investor.example/ir") -> None:
        self.html = html.encode("utf-8")
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit: int | None = None) -> bytes:
        return self.html if limit is None else self.html[:limit]


def _provider(url: str = "https://investor.example/ir", **extra):
    config = {
        "source_url": url,
        "allowed_hosts": ["example"],
        "issuer_markers": ["example issuer"],
        "source_class": "COMPANY_IR",
        "evidence_type": "EARNINGS_RELEASE",
    }
    config.update(extra)
    return IssuerIRWebEvidenceProvider({"EX": config}, timeout=1, max_bytes=50_000)


class IssuerIRWebEvidenceProviderTests(unittest.TestCase):
    def test_normalizes_live_contract_and_deterministic_receipts(self):
        html = """
        <html><head><title>Example Issuer reports results</title>
        <meta property="og:title" content="Example Issuer results"></head>
        <body><h1>Example Issuer reports results</h1>
        <time datetime="2026-01-15T13:00:00Z">January 15, 2026</time>
        <p>Revenue increased and management issued guidance.</p></body></html>
        """
        provider = _provider()
        with patch("urllib.request.urlopen", return_value=_HTMLResponse(html)):
            first = provider.fetch("EX", {})
        with patch("urllib.request.urlopen", return_value=_HTMLResponse(html)):
            second = provider.fetch("EX", {})
        self.assertEqual(first.artifact_id, second.artifact_id)
        self.assertEqual(first.payload["security_id"], "EX")
        self.assertEqual(first.payload["source_class"], "COMPANY_IR")
        self.assertEqual(first.payload["source_observed_at"], "2026-01-15T13:00:00Z")
        self.assertEqual(first.payload["raw_artifact_id"], first.artifact_id)
        evidence = provider.evidence_from_artifact(first)
        self.assertEqual(evidence.evidence_id, "E-" + first.artifact_id)
        self.assertEqual(evidence.payload_hash, first.payload_hash)

    def test_missing_timestamp_fails_closed(self):
        provider = _provider()
        html = "<html><title>Example Issuer</title><body>Example Issuer release</body></html>"
        with patch("urllib.request.urlopen", return_value=_HTMLResponse(html)):
            with self.assertRaises(ProviderError):
                provider.fetch("EX", {})

    def test_future_timestamp_fails_closed(self):
        provider = _provider()
        html = "<html><title>Example Issuer</title><body><p>Example Issuer January 1, 2099</p></body></html>"
        with patch("urllib.request.urlopen", return_value=_HTMLResponse(html)):
            with self.assertRaises(ProviderError):
                provider.fetch("EX", {})

    def test_https_private_and_foreign_hosts_are_rejected(self):
        with self.assertRaises(ProviderError):
            _provider("http://investor.example/ir")
        with self.assertRaises(ProviderError):
            _provider("https://127.0.0.1/ir")
        provider = _provider()
        html = "<html><title>Other Company</title><body><time datetime='2026-01-01'>Other Company</time></body></html>"
        with patch("urllib.request.urlopen", return_value=_HTMLResponse(html)):
            with self.assertRaises(ProviderError):
                provider.fetch("EX", {})

    def test_secret_content_is_redacted_and_hash_lineage_is_checked(self):
        provider = _provider()
        html = """<html><title>Example Issuer</title><body>
        <time datetime="2026-01-01">January 1, 2026</time>
        Example Issuer Authorization: Bearer super-secret-token-value
        </body></html>"""
        with patch("urllib.request.urlopen", return_value=_HTMLResponse(html)):
            artifact = provider.fetch("EX", {})
        self.assertNotIn("super-secret-token-value", json.dumps(artifact.payload))
        artifact.payload["content"] = "tampered"
        with self.assertRaises(ProviderError):
            provider.evidence_from_artifact(artifact)

    def test_source_map_is_required_and_unknown_subject_fails(self):
        with self.assertRaises(ValueError):
            IssuerIRWebEvidenceProvider({})
        provider = _provider()
        with self.assertRaises(ProviderError):
            provider.fetch("AAPL", {})


if __name__ == "__main__":
    unittest.main()
