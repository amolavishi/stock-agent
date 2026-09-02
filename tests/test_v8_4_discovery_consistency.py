from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from stock_agent import v8_4_discovery_consistency as consistency
from stock_agent import v8_main_scanner_failure_isolation as isolation
from stock_agent.providers import ProviderRequestError
from stock_agent.runtime import ContractViolation


ROOT = Path(__file__).resolve().parents[1]


class V84DiscoveryStructuralConsistencyTests(unittest.TestCase):
    def test_schema_uses_v84_signal_vocabulary_and_early_trajectory(self):
        base = {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "signal_strength": {"type": "string", "enum": ["STRONG"]},
                            "recommended_discovery_action": {"type": "string", "enum": ["EXCLUDE"]},
                        },
                    },
                },
                "coverage_ledger": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"signal_strength": {"type": "string", "enum": ["STRONG"]}},
                    },
                },
            },
        }
        patched = consistency._patch_schema(base)
        candidate = patched["properties"]["candidates"]["items"]["properties"]
        ledger = patched["properties"]["coverage_ledger"]["items"]["properties"]
        self.assertEqual(candidate["signal_strength"]["enum"], list(consistency.V8_4_SIGNAL_STRENGTH))
        self.assertEqual(ledger["signal_strength"]["enum"], list(consistency.V8_4_SIGNAL_STRENGTH))
        self.assertIn("EARLY_TRAJECTORY", candidate["recommended_discovery_action"]["enum"])
        self.assertNotIn("STRONG", candidate["signal_strength"]["enum"])

    def test_v84_high_medium_low_are_real_signals_in_round_metrics(self):
        def fake_base(scanner_id, round_id, chunk, result, cumulative, prior_evidence):
            return ({"new_signal": 0, "new_signal_security_ids": [], "new_partial_signal": 0}, set())

        result = {
            "candidates": [
                {"security_id": "AAA", "signal_strength": "HIGH", "partial_signal": False},
                {"security_id": "BBB", "signal_strength": "MEDIUM", "partial_signal": False},
                {"security_id": "CCC", "signal_strength": "LOW", "partial_signal": False},
                {"security_id": "DDD", "signal_strength": "UNKNOWN", "partial_signal": True},
            ]
        }
        with mock.patch.object(consistency, "_BASE_ROUND_METRICS", fake_base):
            metrics, _ = consistency._round_metrics_v84("02", "02-R001", [], result, 0, set())
        self.assertEqual(metrics["new_signal"], 3)
        self.assertEqual(metrics["new_signal_security_ids"], ["AAA", "BBB", "CCC"])
        self.assertEqual(metrics["new_partial_signal"], 1)

    def test_full_strategy_universe_claim_requires_every_manifest_proof(self):
        ok, failures = consistency._validated_full_scope_manifest({})
        self.assertFalse(ok)
        self.assertTrue(failures)

        manifest = {
            "scope_code": "FULL_STRATEGY_UNIVERSE_SCAN",
            "validation_status": "PASS",
            "authoritative_listing_source_coverage": True,
            "identity_reconciliation_complete": True,
            "security_type_classification_complete": True,
            "price_filter_reconciled": True,
            "market_cap_filter_reconciled": True,
            "mdv20_filter_reconciled": True,
            "eligibility_count_reconciled": True,
            "material_unresolved_eligibility_count": 0,
        }
        ok, failures = consistency._validated_full_scope_manifest({"universe_manifest": manifest})
        self.assertTrue(ok, failures)
        broken = dict(manifest)
        broken["identity_reconciliation_complete"] = False
        ok, failures = consistency._validated_full_scope_manifest({"universe_manifest": broken})
        self.assertFalse(ok)
        self.assertIn("IDENTITY_RECONCILIATION_COMPLETE", failures)

    def test_one_scanner_engineering_failure_is_data_block_not_rejection(self):
        raw = {"candidate_universe_packet": [{"security_id": "AAA"}, {"security_id": "BBB"}]}
        value = isolation._isolated_round_payload("02", raw, ProviderRequestError("HTTP 500", retryable=True, status_code=500))
        self.assertEqual(value["execution_status"], "DATA_BLOCKED")
        self.assertEqual(value["screened_count"], 0)
        self.assertEqual(len(value["coverage_ledger"]), 2)
        self.assertTrue(all(row["disposition"] == "DATA_BLOCK" for row in value["coverage_ledger"]))
        self.assertTrue(all(row["failure_class"] == "DATA_INTEGRITY_BLOCK" for row in value["coverage_ledger"]))
        self.assertFalse(value["source_exhaustion"])
        self.assertEqual(value["candidates"], [])

    def test_source_integrity_failure_is_run_global_not_isolated(self):
        self.assertFalse(isolation._isolatable(ContractViolation("V8_SOURCE_INTEGRITY:02:MISSING")))
        self.assertFalse(isolation._isolatable(ProviderRequestError("V8 source integrity failure scanner=02", retryable=False)))
        self.assertTrue(isolation._isolatable(ProviderRequestError("HTTP 500", retryable=True, status_code=500)))

    def test_composed_production_schema_and_versions_are_v84_consistent(self):
        code = r'''
import json
from stock_agent.production import production_composition
from stock_agent import v8_main_discovery_coach as coach
c = production_composition()
s = coach._scanner_schema()
p = s["properties"]["candidates"]["items"]["properties"]
l = s["properties"]["coverage_ledger"]["items"]["properties"]
print(json.dumps({
  "signal": p["signal_strength"]["enum"],
  "actions": p["recommended_discovery_action"]["enum"],
  "ledger_signal": l["signal_strength"]["enum"],
  "package": c["v8_discovery_source_package_version"],
  "isolation": c["v8_main_scanner_failure_isolation_version"],
  "consistency": c["v8_4_discovery_consistency_version"],
  "source_complete": c["v8_source_bundle"]["complete"],
  "mro": c["mro"],
}, sort_keys=True))
'''
        completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True, check=True)
        data = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(data["signal"], list(consistency.V8_4_SIGNAL_STRENGTH))
        self.assertEqual(data["ledger_signal"], list(consistency.V8_4_SIGNAL_STRENGTH))
        self.assertIn("EARLY_TRAJECTORY", data["actions"])
        self.assertEqual(data["package"], "8.4.0")
        self.assertEqual(data["isolation"], isolation.V8_MAIN_SCANNER_FAILURE_ISOLATION_VERSION)
        self.assertEqual(data["consistency"], consistency.V8_4_DISCOVERY_CONSISTENCY_VERSION)
        self.assertTrue(data["source_complete"])
        joined = " ".join(data["mro"])
        self.assertIn("V8MainScannerFailureIsolationProductionStockAgent", joined)
        self.assertIn("V84DiscoveryConsistencyProductionStockAgent", joined)


if __name__ == "__main__":
    unittest.main()
