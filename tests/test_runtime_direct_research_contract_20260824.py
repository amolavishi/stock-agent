from __future__ import annotations

import copy
import importlib
import unittest

from stock_agent.models import RawArtifact, RunMode, canonical_hash, utc_now
from stock_agent.runtime import ProductionStockAgent

_production_helpers = importlib.import_module("tests.test_production_adapters")


class _DirectNormalizedResearchProvider:
    provider_name = "direct-normalized-test"

    def __init__(self, payload: dict):
        self.payload = copy.deepcopy(payload)

    def fetch(self, subject_id: str, query: dict) -> RawArtifact:
        payload = copy.deepcopy(self.payload)
        observed_at = utc_now()
        payload.update({
            "security_id": subject_id,
            "source_url": "https://issuer.example/ir/earnings",
            "source_observed_at": observed_at,
            "provider": self.provider_name,
            "content": payload.get("content") or "official issuer observation",
        })
        return RawArtifact(
            f"direct-{canonical_hash(payload)}",
            self.provider_name,
            "RESEARCH_EVIDENCE",
            subject_id,
            observed_at,
            payload,
            canonical_hash(payload),
            observed_at,
            utc_now(),
        )


class DirectResearchRuntimeContractTests(unittest.TestCase):
    def test_strict_hunt_accepts_canonical_direct_research_envelope(self):
        fixture = _production_helpers.ProductionAdapterTests("runTest")
        agent: ProductionStockAgent = fixture.make()
        recorded = agent.config.research_provider.recordings["SEC1"]
        agent.config.research_provider = _DirectNormalizedResearchProvider(recorded)
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
        row = agent.store.connection.execute(
            "SELECT provider,payload_json FROM raw_artifacts WHERE artifact_type='RESEARCH_EVIDENCE'"
        ).fetchone()
        self.assertEqual(row["provider"], "direct-normalized-test")
        self.assertIn('"source_url": "https://issuer.example/ir/earnings"', row["payload_json"])
        evidence = agent.store.connection.execute(
            "SELECT source_class,raw_artifact_id FROM evidence WHERE evidence_id LIKE 'E-RESEARCH_EVIDENCE:%'"
        ).fetchone()
        self.assertEqual(evidence["source_class"], "direct-normalized-test")
        self.assertTrue(evidence["raw_artifact_id"])


if __name__ == "__main__":
    unittest.main()
