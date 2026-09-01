from __future__ import annotations

import json
import subprocess
import sys
import unittest

from stock_agent.discovery_recall_lite_v15 import (
    DEFAULT_ADV_PROBE_TARGET,
    FORENSIC_AUDIT_SHA256,
    MIN_SIGNAL_COVERAGE,
    PREFERRED_SIGNAL_COVERAGE,
    SCANNERS,
)
from stock_agent.discovery_recall_firewall_v15 import DISCOVERY_RECALL_FIREWALL_VERSION


class DiscoveryRecallReferenceOnlyTests(unittest.TestCase):
    def test_reference_scanner_identity_and_breadth_constants_are_retained(self):
        self.assertEqual(sorted(SCANNERS), [f"{n:02d}" for n in range(2, 15)])
        self.assertEqual(MIN_SIGNAL_COVERAGE, 150)
        self.assertGreaterEqual(PREFERRED_SIGNAL_COVERAGE, 200)
        self.assertGreaterEqual(DEFAULT_ADV_PROBE_TARGET, 1000)
        self.assertEqual(FORENSIC_AUDIT_SHA256, "47494df8fd0464c3fb63c6f2a5facd7dd6296616bec635b6faebe15e4ddab616")
        self.assertEqual(DISCOVERY_RECALL_FIREWALL_VERSION, "V8_DISCOVERY_RECALL_FIREWALL_V1.5")

    def test_production_uses_main_v8_coach_not_lite_runtime(self):
        code = "import json; from stock_agent.bootstrap import production_composition; print(json.dumps(production_composition(), sort_keys=True))"
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        value = json.loads(completed.stdout.strip().splitlines()[-1])
        mro = " ".join(value["mro"])
        self.assertTrue(value["main_is_sole_discovery_owner"])
        self.assertFalse(value["python_scanner_routing_authority"])
        self.assertFalse(value["discovery_recall_lite_runtime_installed"])
        self.assertIn("V8MainDiscoveryCoachProductionStockAgent", mro)
        self.assertNotIn("DiscoveryRecallLiteProductionStockAgent", mro)
        self.assertNotIn("DiscoveryRecallStopBridgeProductionStockAgent", mro)
        self.assertIsNotNone(value["discovery_breadth_provider_version"])
        self.assertEqual(value["v8_main_forensic_audit_sha256"], FORENSIC_AUDIT_SHA256)


if __name__ == "__main__":
    unittest.main()
