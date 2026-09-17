from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from stock_agent.shadow_health_v19 import SHADOW_HEALTH_VERSION, _health_schema


ROOT = Path(__file__).resolve().parents[1]


class ShadowHealthV19Tests(unittest.TestCase):
    def test_health_schema_is_minimal_and_fixed(self):
        schema = _health_schema()
        self.assertEqual(set(schema["properties"]), {"status", "nonce"})
        self.assertEqual(schema["properties"]["status"]["const"], "HEALTH_OK")
        self.assertEqual(len(schema["properties"]["nonce"]["const"]), 64)
        self.assertNotIn("context_manifest_receipt", schema["properties"])

    def test_production_health_check_does_not_use_market_analysis_schema(self):
        # Production installation intentionally monkey-patches runtime/shadow
        # classes. Probe in a subprocess so unittest discovery remains isolated
        # and legacy/base-runtime tests cannot inherit production composition.
        script = r'''
import json
import stock_agent.production
from stock_agent import shadow

class EchoProvider:
    provider = "test"
    model = "echo"
    reasoning_effort = "medium"
    def __init__(self): self.requests = []
    def call(self, request):
        self.requests.append(request)
        schema = request["output_schema_definition"]
        return {
            "status": schema["properties"]["status"]["const"],
            "nonce": schema["properties"]["nonce"]["const"],
        }, {"model": self.model, "latency_ms": 1.0, "usage_source": "test"}

provider = EchoProvider()
checker = object.__new__(shadow.LunaHealthChecker)
checker.provider = provider
checker.prompt_runtime = object()
result = checker.check()
request = provider.requests[-1]
print(json.dumps({
    "result": result,
    "prompt_id": request["prompt_id"],
    "properties": sorted(request["output_schema_definition"]["properties"]),
}))
'''
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            text=True, capture_output=True, check=True,
        )
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["result"]["status"], "PASS")
        self.assertEqual(payload["result"]["health_version"], SHADOW_HEALTH_VERSION)
        self.assertEqual(payload["prompt_id"], "health.luna_transport_v19")
        self.assertEqual(set(payload["properties"]), {"status", "nonce"})

    def test_composition_reports_shadow_health_version(self):
        code = (
            "import json; from stock_agent.production import production_composition; "
            "print(json.dumps(production_composition(), sort_keys=True))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT,
            text=True, capture_output=True, check=True,
        )
        composition = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(composition["shadow_health_version"], SHADOW_HEALTH_VERSION)


if __name__ == "__main__":
    unittest.main()
