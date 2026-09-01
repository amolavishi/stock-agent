from __future__ import annotations

import unittest

from stock_agent import shadow
from stock_agent.bootstrap import install_production_stack, production_composition
from stock_agent.shadow_health_v19 import SHADOW_HEALTH_VERSION, _health_schema


class EchoProvider:
    provider = "test"
    model = "echo"
    reasoning_effort = "medium"

    def __init__(self):
        self.requests = []

    def call(self, request):
        self.requests.append(request)
        schema = request["output_schema_definition"]
        return {
            "status": schema["properties"]["status"]["const"],
            "nonce": schema["properties"]["nonce"]["const"],
        }, {"model": self.model, "latency_ms": 1.0, "usage_source": "test"}


class ShadowHealthV19Tests(unittest.TestCase):
    def test_health_schema_is_minimal_and_fixed(self):
        schema = _health_schema()
        self.assertEqual(set(schema["properties"]), {"status", "nonce"})
        self.assertEqual(schema["properties"]["status"]["const"], "HEALTH_OK")
        self.assertEqual(len(schema["properties"]["nonce"]["const"]), 64)
        self.assertNotIn("context_manifest_receipt", schema["properties"])

    def test_production_health_check_does_not_use_market_analysis_schema(self):
        install_production_stack()
        provider = EchoProvider()
        checker = object.__new__(shadow.LunaHealthChecker)
        checker.provider = provider
        checker.prompt_runtime = object()
        result = checker.check()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["health_version"], SHADOW_HEALTH_VERSION)
        request = provider.requests[-1]
        self.assertEqual(request["prompt_id"], "health.luna_transport_v19")
        self.assertEqual(set(request["output_schema_definition"]["properties"]), {"status", "nonce"})

    def test_composition_reports_shadow_health_version(self):
        self.assertEqual(production_composition()["shadow_health_version"], SHADOW_HEALTH_VERSION)


if __name__ == "__main__":
    unittest.main()
