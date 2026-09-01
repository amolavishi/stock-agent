from __future__ import annotations

import inspect
import json
import subprocess
import sys
import unittest
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root
from stock_agent.prompt_runtime import PromptRuntime
from stock_agent.v8_main_discovery_coach import (
    COMMON_GUARDRAIL,
    V8_MAIN_DISCOVERY_COACH_VERSION,
    V8_MAIN_FORENSIC_AUDIT_SHA256,
    V8_SCANNERS,
    _install_prompts,
)


class V8MainDiscoveryCoachTests(unittest.TestCase):
    def test_canonical_manifest_hashes_match_all_02_14_scanners(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "docs" / "v8_canonical" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
        actual = {}
        for item in manifest["files"]:
            name = str(item["file"]).split("/")[-1]
            if name[:2].isdigit() and 2 <= int(name[:2]) <= 14:
                actual[name[:2]] = item["sha256"]
        self.assertEqual(set(actual), set(V8_SCANNERS))
        for scanner_id, spec in V8_SCANNERS.items():
            self.assertEqual(spec["sha256"], actual[scanner_id], scanner_id)

    def test_runtime_registers_thirteen_model_scanners_and_keeps_stock_scout_owner(self):
        runtime = PromptRuntime(canonical_prompt_library_root())
        original_owner = runtime.prompts["workflow.stock_scout"]["output_schema"]
        _install_prompts(runtime)
        self.assertEqual(original_owner, "DiscoveryCandidateSetV2")
        self.assertEqual(runtime.prompts["workflow.stock_scout"]["output_schema"], "DiscoveryCandidateSetV2")
        for scanner_id in sorted(V8_SCANNERS):
            meta = runtime.prompts[f"v8_main.discovery_{scanner_id}"]
            self.assertEqual(meta["output_schema"], "V8MainDiscoveryScannerResultV1")
            body = meta["_body"]
            self.assertIn(V8_SCANNERS[scanner_id]["sha256"], body)
            self.assertIn("HUNT_ONLY_RECALL_FIRST", body)
        self.assertIn("V8 MAIN COACHED DISCOVERY", runtime.prompts["workflow.stock_scout"]["_body"])

    def test_common_guardrail_is_recall_first_grade_blind(self):
        self.assertIn("UNKNOWN is not PASS and is not FAIL", COMMON_GUARDRAIL)
        self.assertIn("DEEP_DIVE_SECONDARY", COMMON_GUARDRAIL)
        self.assertIn("do NOT create Research Grade", COMMON_GUARDRAIL)
        self.assertIn("No target price", COMMON_GUARDRAIL)

    def test_coach_does_not_import_or_call_lite_python_evaluator(self):
        import stock_agent.v8_main_discovery_coach as coach
        source = inspect.getsource(coach)
        self.assertNotIn("_evaluate(", source)
        self.assertNotIn("_scanner_receipt(", source)
        self.assertNotIn("install_discovery_recall_lite_runtime", source)
        self.assertIn("workflow.stock_scout", source)

    def test_bootstrap_does_not_install_lite_runtime(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "stock_agent" / "bootstrap.py").read_text(encoding="utf-8")
        self.assertNotIn("install_discovery_recall_lite_runtime()", source)
        self.assertNotIn("install_discovery_recall_failure_guard_v16()", source)
        self.assertIn("install_v8_main_discovery_coach()", source)

    def test_production_composition_has_main_authority_only(self):
        code = "import json; from stock_agent.bootstrap import production_composition; print(json.dumps(production_composition(), sort_keys=True))"
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        value = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(value["v8_main_discovery_coach_version"], V8_MAIN_DISCOVERY_COACH_VERSION)
        self.assertEqual(value["v8_main_forensic_audit_sha256"], V8_MAIN_FORENSIC_AUDIT_SHA256)
        self.assertTrue(value["main_is_sole_discovery_owner"])
        self.assertFalse(value["python_scanner_routing_authority"])
        self.assertFalse(value["discovery_recall_lite_runtime_installed"])
        mro = " ".join(value["mro"])
        self.assertIn("V8MainDiscoveryCoachProductionStockAgent", mro)
        self.assertNotIn("DiscoveryRecallLiteProductionStockAgent", mro)


if __name__ == "__main__":
    unittest.main()
