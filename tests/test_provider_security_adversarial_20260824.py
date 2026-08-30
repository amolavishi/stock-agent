from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from stock_agent.adapters import ConfiguredJsonMarketDataProvider, ConfiguredResearchEvidenceProvider, ProviderError, TossMarketDataProvider
from stock_agent.models import Evidence, RawArtifact, canonical_hash, utc_now
from stock_agent.providers import DeepSeekProvider
from stock_agent.store import SQLiteStore


class _Response:
    def __init__(self, payload: object, *, url: str | None = None):
        self.payload = payload
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int | None = None):
        raw = json.dumps(self.payload).encode("utf-8")
        return raw if size is None else raw[:size]


class ProviderSecurityAdversarialTests(unittest.TestCase):
    def test_configured_market_provider_rejects_private_base_and_oversize(self):
        with self.assertRaises(ProviderError):
            ConfiguredJsonMarketDataProvider(
                "http://127.0.0.1:8080", {"market_context": "/m", "universe": "/u", "execution": "/e"}
            )
        provider = ConfiguredJsonMarketDataProvider(
            "https://market.example", {"market_context": "/m", "universe": "/u", "execution": "/e"}, max_bytes=16
        )
        with patch("urllib.request.urlopen", return_value=_Response({"large": "payload" * 20}, url="https://market.example/m")):
            with self.assertRaises(ProviderError):
                provider.fetch_market_context({})

    def test_configured_market_provider_rejects_redirect_to_foreign_host(self):
        provider = ConfiguredJsonMarketDataProvider(
            "https://market.example", {"market_context": "/m", "universe": "/u", "execution": "/e"}
        )
        with patch("urllib.request.urlopen", return_value=_Response({"ok": True}, url="https://evil.example/m")):
            with self.assertRaises(ProviderError):
                provider.fetch_market_context({})

    def test_toss_rejects_redirect_to_foreign_host(self):
        provider = TossMarketDataProvider("id", "secret", min_interval=0)
        with patch("urllib.request.urlopen", return_value=_Response({"result": []}, url="https://evil.example/oauth2/token")):
            with self.assertRaises(ProviderError):
                provider._request_json("POST", "/oauth2/token", form={"grant_type": "client_credentials"}, auth=False)

    def test_configured_research_provider_rejects_foreign_redirect(self):
        provider = ConfiguredResearchEvidenceProvider("https://research.example", "/evidence")
        with patch("urllib.request.urlopen", return_value=_Response({"source_url": "https://issuer.example/ir", "published_at": "2026-08-20T00:00:00Z", "content": "raw"}, url="https://evil.example/evidence")):
            with self.assertRaises(ProviderError):
                provider.fetch("SEC", {})

    def test_model_provider_rejects_private_endpoint_and_oversize(self):
        with self.assertRaises(ValueError):
            DeepSeekProvider("secret", endpoint="http://127.0.0.1:8080/v1")
        with self.assertRaises(ValueError):
            DeepSeekProvider("secret", endpoint="https://model.example/v1?api_key=leak")
        provider = DeepSeekProvider("secret", endpoint="https://model.example/v1", max_bytes=16)
        with patch("urllib.request.urlopen", return_value=_Response({"choices": [{"message": {"content": "{}"}}]}, url="https://model.example/v1")):
            with self.assertRaises(RuntimeError):
                provider.call({"prompt_body": "x", "output_schema_definition": {"type": "object"}})

    def test_model_provider_rejects_foreign_redirect(self):
        provider = DeepSeekProvider("secret", endpoint="https://model.example/v1")
        with patch("urllib.request.urlopen", return_value=_Response({"choices": []}, url="https://evil.example/v1")):
            with self.assertRaises(RuntimeError):
                provider.call({"prompt_body": "x", "output_schema_definition": {"type": "object"}})

    def test_sqlite_artifact_boundary_rejects_forged_hash_and_future_source(self):
        store = SQLiteStore(":memory:")
        payload = {"value": 1}
        with self.assertRaises(ValueError):
            store.save_raw_artifact(RawArtifact("bad", "provider", "TEST", "SEC", utc_now(), payload, "0" * 64, utc_now(), utc_now()))
        with self.assertRaises(ValueError):
            store.save_raw_artifact(RawArtifact("future", "provider", "TEST", "SEC", utc_now(), payload, canonical_hash(payload), "2099-01-01T00:00:00Z", utc_now()))

    def test_sqlite_evidence_receipt_must_reference_matching_artifact(self):
        store = SQLiteStore(":memory:")
        payload = {"value": 1}
        artifact = RawArtifact("artifact-1", "provider", "TEST", "SEC", utc_now(), payload, canonical_hash(payload), utc_now(), utc_now())
        store.save_raw_artifact(artifact)
        with self.assertRaises(ValueError):
            store.upsert_evidence(Evidence("E-missing", "SEC", "provider", artifact.observed_at, 0, artifact.payload_hash, "RAW", raw_artifact_id="missing"))
        with self.assertRaises(ValueError):
            store.upsert_evidence(Evidence("E-wrong", "SEC", "provider", artifact.observed_at, 0, "0" * 64, "RAW", raw_artifact_id=artifact.artifact_id))


if __name__ == "__main__":
    unittest.main()
