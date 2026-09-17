from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from stock_agent.paths import canonical_prompt_library_root
from stock_agent.prompt_runtime import PromptRuntime
from stock_agent.v8_discovery_receipt_contract_v206 import (
    V8_DISCOVERY_RECEIPT_CONTRACT_VERSION,
    bind_authoritative_receipts,
    patch_registry_for_preserved_discovery_receipts,
)
from stock_agent.v8_market_discovery_admission import _DiscoveryAdmissionReceipt
from stock_agent.models import GateDecision


ROOT = Path(__file__).resolve().parents[1]


def _case(schema_id: str) -> dict:
    payload = json.loads(
        (canonical_prompt_library_root() / "VALIDATION" / "schema_positive_cases.json").read_text(encoding="utf-8")
    )
    return copy.deepcopy(next(item["instance"] for item in payload["cases"] if item["schema_id"] == schema_id))


class V8DiscoveryReceiptContractV206Tests(unittest.TestCase):
    def test_only_discovery_provenance_gate_defs_accept_insufficient_evidence(self):
        runtime = PromptRuntime(canonical_prompt_library_root())
        patch_registry_for_preserved_discovery_receipts(runtime.registry)
        defs = runtime.registry["$defs"]

        for name in ("MarketContextGateReceipt", "SectorGateReceipt"):
            enum = defs[name]["properties"]["decision"]["enum"]
            self.assertIn("INSUFFICIENT_EVIDENCE", enum, name)

        for name in ("MarketExecutionGateReceipt", "StageGateReceipt", "CapitalPrescreenGateReceipt"):
            enum = defs[name]["properties"]["decision"]["enum"]
            self.assertNotIn("INSUFFICIENT_EVIDENCE", enum, name)

    def test_live_market_context_insufficient_receipt_validates_in_sector_analysis(self):
        runtime = PromptRuntime(canonical_prompt_library_root())
        patch_registry_for_preserved_discovery_receipts(runtime.registry)
        value = _case("SectorOpportunityAssessmentV2")
        value["market_context_gate_receipt"]["decision"] = "INSUFFICIENT_EVIDENCE"
        self.assertEqual(runtime.validate("SectorOpportunityAssessmentV2", value), [])

    def test_live_sector_insufficient_receipt_validates_in_stock_discovery(self):
        runtime = PromptRuntime(canonical_prompt_library_root())
        patch_registry_for_preserved_discovery_receipts(runtime.registry)
        value = _case("DiscoveryCandidateSetV2")
        value["sector_gate_receipt"]["decision"] = "INSUFFICIENT_EVIDENCE"
        self.assertEqual(runtime.validate("DiscoveryCandidateSetV2", value), [])

    def test_execution_gate_remains_fail_closed(self):
        runtime = PromptRuntime(canonical_prompt_library_root())
        patch_registry_for_preserved_discovery_receipts(runtime.registry)
        value = _case("FinalSynthesisRecommendationV2")
        value["market_execution_gate_receipt"]["decision"] = "INSUFFICIENT_EVIDENCE"
        errors = runtime.validate("FinalSynthesisRecommendationV2", value)
        self.assertTrue(errors)
        self.assertTrue(any("INSUFFICIENT_EVIDENCE" in item for item in errors))

    def test_python_bound_receipt_overrides_model_echo_without_forging_pass(self):
        runtime = PromptRuntime(canonical_prompt_library_root())
        patch_registry_for_preserved_discovery_receipts(runtime.registry)
        schema = copy.deepcopy(runtime.registry["schemas"]["SectorOpportunityAssessmentV2"])
        schema["$defs"] = runtime.registry["$defs"]
        payload = _case("SectorOpportunityAssessmentV2")
        self.assertEqual(payload["market_context_gate_receipt"]["decision"], "PASS")

        canonical = copy.deepcopy(payload["market_context_gate_receipt"])
        canonical["decision"] = "INSUFFICIENT_EVIDENCE"
        context = {
            "entries": [
                {
                    "id": "market_context_gate_receipt",
                    "content": {
                        "source_stage": "MARKET_CONTEXT_GATE",
                        "content_type": "GateReceipt",
                        "value": canonical,
                    },
                }
            ]
        }
        rebound = bind_authoritative_receipts(payload, context, schema)
        self.assertEqual(rebound["market_context_gate_receipt"]["decision"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(runtime.validate("SectorOpportunityAssessmentV2", rebound), [])

    def test_discovery_admission_does_not_forge_canonical_receipt(self):
        canonical = SimpleNamespace(
            decision=GateDecision.INSUFFICIENT_EVIDENCE,
            as_dict=lambda: {
                "gate_type": "MarketContextGate",
                "decision": "INSUFFICIENT_EVIDENCE",
                "input_hash": "a" * 64,
                "rule_set_hash": "b" * 64,
                "evaluated_at": "2026-09-02T12:00:00Z",
                "receipt_hash": "c" * 64,
            },
        )
        admission = _DiscoveryAdmissionReceipt(canonical, admitted=True)
        self.assertEqual(admission.decision, GateDecision.PASS)
        self.assertEqual(admission.as_dict()["decision"], "INSUFFICIENT_EVIDENCE")

    def test_production_install_patches_new_prompt_runtime_instances(self):
        code = r'''
import json
from stock_agent.production import ProductionStockAgent
from stock_agent.paths import canonical_prompt_library_root
from stock_agent.prompt_runtime import PromptRuntime
from stock_agent.v8_discovery_receipt_contract_v206 import V8_DISCOVERY_RECEIPT_CONTRACT_VERSION
r = PromptRuntime(canonical_prompt_library_root())
defs = r.registry["$defs"]
print(json.dumps({
  "v": V8_DISCOVERY_RECEIPT_CONTRACT_VERSION,
  "market": defs["MarketContextGateReceipt"]["properties"]["decision"]["enum"],
  "sector": defs["SectorGateReceipt"]["properties"]["decision"]["enum"],
  "execution": defs["MarketExecutionGateReceipt"]["properties"]["decision"]["enum"],
}))
'''
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True, check=True
        )
        data = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(data["v"], V8_DISCOVERY_RECEIPT_CONTRACT_VERSION)
        self.assertIn("INSUFFICIENT_EVIDENCE", data["market"])
        self.assertIn("INSUFFICIENT_EVIDENCE", data["sector"])
        self.assertNotIn("INSUFFICIENT_EVIDENCE", data["execution"])


if __name__ == "__main__":
    unittest.main()
