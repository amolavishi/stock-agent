from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stock_agent import v8_main_source_fidelity as fidelity


ROOT = Path(__file__).resolve().parents[1]


class MainV84SourceFidelityTests(unittest.TestCase):
    def setUp(self):
        fidelity._SOURCE_STATE.clear()
        fidelity._CORE_STATE.clear()
        fidelity._LOCK_CACHE = None

    def test_packaged_v84_bundle_is_complete_and_exact(self):
        status = fidelity.source_bundle_status()
        self.assertTrue(status["complete"], status)
        self.assertEqual(status["package_version"], "8.4.0")
        self.assertEqual(status["scanner_count"], 13)
        self.assertEqual(status["pass_count"], 13)
        self.assertEqual(status["core_count"], 2)
        self.assertEqual(status["core_pass_count"], 2)
        for row in status["all_rows"]:
            self.assertEqual(row["status"], "PASS", row)
            self.assertEqual(row["actual_sha256"], row["expected_sha256"], row)
            self.assertEqual(row["actual_bytes"], row["expected_bytes"], row)

    def test_v84_lock_replaces_legacy_scanner_hash_authority(self):
        fidelity.prepare_v8_4_source_lock()
        from stock_agent import v8_main_discovery_coach as coach
        entries = fidelity._scanner_entries()
        self.assertEqual(set(entries), set(coach.V8_SCANNERS))
        for scanner_id, entry in entries.items():
            self.assertEqual(coach.V8_SCANNERS[scanner_id]["sha256"], entry["sha256"])
            self.assertEqual(coach.V8_SCANNERS[scanner_id]["source_package_version"], "8.4.0")

    def test_crlf_mutation_is_hash_mismatch_not_silently_normalized(self):
        entry = fidelity._scanner_entries()["02"]
        canonical = (ROOT / "prompts" / "v8_4" / entry["path"]).read_bytes()
        self.assertIn(b"\n", canonical)
        mutated = canonical.replace(b"\n", b"\r\n")
        self.assertNotEqual(mutated, canonical)
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp)
            (override / entry["path"]).write_bytes(mutated)
            with mock.patch.dict(os.environ, {"V8_SOURCE_ROOT": str(override)}, clear=False):
                result = fidelity.resolve_scanner_source("02")
        self.assertEqual(result["status"], "HASH_MISMATCH")
        self.assertNotEqual(result["actual_sha256"], result["expected_sha256"])

    def test_missing_packaged_sources_are_never_reconstructed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            with mock.patch.object(fidelity, "_PACKAGED_ROOT", missing), mock.patch.dict(os.environ, {"V8_SOURCE_ROOT": str(missing)}, clear=False), mock.patch.object(fidelity, "_archive_candidates", return_value=[]):
                status = fidelity.source_bundle_status()
        self.assertFalse(status["complete"])
        self.assertEqual(status["pass_count"], 0)
        self.assertEqual(status["core_pass_count"], 0)
        self.assertTrue(all(row["status"] == "MISSING" for row in status["all_rows"]))

    def test_compiled_scanner_prompt_contains_all_three_authorities(self):
        body, meta = fidelity._compiled_body("14")
        self.assertIn("# Discovery Common Contract — V8.4", body)
        self.assertIn("# Canonical Strategy Universe Rules — V8.4", body)
        self.assertIn("# 14. Discovery Scanner Profile — V8.4", body)
        self.assertEqual(meta["source_package_version"], "8.4.0")
        self.assertEqual(meta["scanner_source_sha256"], fidelity._scanner_entries()["14"]["sha256"])

    def test_production_composition_is_self_contained_v84_and_no_lite_runtime(self):
        code = (
            "import json; "
            "from stock_agent.production import production_composition; "
            "print(json.dumps(production_composition(), sort_keys=True))"
        )
        out = subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, text=True)
        value = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(value["main_is_sole_discovery_owner"])
        self.assertFalse(value["python_scanner_routing_authority"])
        self.assertFalse(value["discovery_recall_lite_runtime_installed"])
        self.assertEqual(value["v8_discovery_source_package_version"], "8.4.0")
        self.assertTrue(value["v8_source_bundle"]["complete"])
        mro = " ".join(value["mro"])
        self.assertIn("V8MainSourceGateProductionStockAgent", mro)
        self.assertIn("V8MainScannerFailureIsolationProductionStockAgent", mro)
        self.assertIn("V84DiscoveryConsistencyProductionStockAgent", mro)


if __name__ == "__main__":
    unittest.main()
