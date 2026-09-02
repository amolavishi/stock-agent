from __future__ import annotations

import json
import subprocess
import sys
import unittest


class V8PreLiveProductionWiringTests(unittest.TestCase):
    def test_production_installs_exact_pre_live_functions_and_runtime_layers(self):
        code = r'''
import json
from stock_agent.production import production_composition
from stock_agent import runtime
from stock_agent import v8_main_discovery_coach as coach
from stock_agent import v8_evidence_origin_v19 as origin
c = production_composition()
print(json.dumps({
    "sentinel_module": coach._sentinel_sample.__module__,
    "sentinel_name": coach._sentinel_sample.__name__,
    "lineage_module": origin._source_lineage.__module__,
    "lineage_name": origin._source_lineage.__name__,
    "origin_map_module": origin._origin_map_for_evidence.__module__,
    "origin_map_name": origin._origin_map_for_evidence.__name__,
    "mro": [cls.__name__ for cls in runtime.ProductionStockAgent.__mro__],
    "output_contract": c.get("v8_main_scanner_output_contract_version"),
    "weak_parallel": c.get("discovery_recall_lite_runtime_installed"),
}))
'''
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        data = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertTrue(data["sentinel_module"].endswith("v8_pre_live_integrity_v204"))
        self.assertEqual(data["sentinel_name"], "sentinel_sample_v204")
        self.assertTrue(data["lineage_module"].endswith("v8_pre_live_integrity_v202"))
        self.assertEqual(data["lineage_name"], "source_lineage_v202")
        self.assertTrue(data["origin_map_module"].endswith("v8_pre_live_integrity_v203"))
        self.assertEqual(data["origin_map_name"], "origin_map_for_evidence_v203")
        self.assertIn("V8PreLiveIntegrityProductionStockAgent", data["mro"])
        self.assertIn("V8PreLiveSentinelProductionStockAgent", data["mro"])
        self.assertEqual(data["output_contract"], "V8_MAIN_SCANNER_OUTPUT_V1.3")
        self.assertFalse(data["weak_parallel"])


if __name__ == "__main__":
    unittest.main()
