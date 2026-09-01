from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stock_agent import v8_main_source_fidelity as fidelity
from stock_agent.providers import ProviderRequestError


ROOT = Path(__file__).resolve().parents[1]


class MainV8SourceFidelityTests(unittest.TestCase):
    def setUp(self):
        fidelity._SOURCE_STATE.clear()

    @staticmethod
    def _write_fixture(root: Path, scanner_id: str, body: bytes, expected_sha: str | None = None):
        filename = fidelity._SCANNER_FILES[scanner_id]
        source_root = root / "sources"
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / filename).write_bytes(body)
        expected = expected_sha or hashlib.sha256(body).hexdigest()
        manifest = {
            "files": [{"file": f"prompts/v8/{filename}", "sha256": expected, "bytes": len(body)}]
        }
        manifest_path = root / "SOURCE_MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return source_root, manifest_path, expected

    def test_missing_source_is_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "SOURCE_MANIFEST.json"
            filename = fidelity._SCANNER_FILES["02"]
            manifest.write_text(json.dumps({"files": [{"file": f"prompts/v8/{filename}", "sha256": "a" * 64}]}), encoding="utf-8")
            with mock.patch.object(fidelity, "_MANIFEST", manifest), mock.patch.dict(os.environ, {"V8_SOURCE_ROOT": str(root / "missing")}, clear=False), mock.patch.object(fidelity, "_archive_candidates", return_value=[]):
                result = fidelity.resolve_scanner_source("02")
            self.assertEqual(result["status"], "MISSING")
            self.assertIsNone(result["source_text"])

    def test_hash_mismatch_is_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root, manifest, _ = self._write_fixture(root, "02", b"canonical body", expected_sha="b" * 64)
            with mock.patch.object(fidelity, "_MANIFEST", manifest), mock.patch.dict(os.environ, {"V8_SOURCE_ROOT": str(source_root)}, clear=False), mock.patch.object(fidelity, "_archive_candidates", return_value=[]):
                result = fidelity.resolve_scanner_source("02")
            self.assertEqual(result["status"], "HASH_MISMATCH")
            self.assertIsNone(result["source_text"])

    def test_exact_bytes_are_loaded_only_after_sha_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = "# CANONICAL V8 02\n실제 원문 전략\n".encode("utf-8")
            source_root, manifest, expected = self._write_fixture(root, "02", body)
            with mock.patch.object(fidelity, "_MANIFEST", manifest), mock.patch.dict(os.environ, {"V8_SOURCE_ROOT": str(source_root)}, clear=False), mock.patch.object(fidelity, "_archive_candidates", return_value=[]):
                result = fidelity.resolve_scanner_source("02")
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["actual_sha256"], expected)
            self.assertEqual(result["source_text"].encode("utf-8"), body)

    def test_scanner_provider_receives_runtime_universe_packet(self):
        captured = {}
        def fake_base(_self, request):
            captured.update(request)
            return {"ok": True}, {"provider": "fake"}

        fidelity._SOURCE_STATE["02"] = {
            "status": "PASS", "expected_sha256": "a" * 64,
            "actual_sha256": "a" * 64, "source_text": "source",
        }
        request = {
            "prompt_id": "v8_main.discovery_02",
            "messages": [{"role": "system", "content": "canonical source"}],
            "runtime_input": {"candidate_universe_packet": [{"security_id": "ABC"}]},
        }
        with mock.patch.object(fidelity, "_BASE_PROVIDER_CALL", fake_base):
            result, _ = fidelity._scanner_provider_call(object(), request)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["runtime_input"], {})
        joined = "\n".join(str(item.get("content") or "") for item in captured["messages"])
        self.assertIn("V8_CANONICAL_SCANNER_RUNTIME_INPUT", joined)
        self.assertIn("ABC", joined)

    def test_missing_source_blocks_even_before_provider_specific_logic(self):
        fidelity._SOURCE_STATE["02"] = {
            "status": "MISSING", "expected_sha256": "a" * 64,
            "actual_sha256": None, "source_text": None,
        }
        with self.assertRaises(ProviderRequestError):
            fidelity._scanner_provider_call(object(), {"prompt_id": "v8_main.discovery_02", "messages": [], "runtime_input": {}})

    def test_production_composition_has_no_lite_runtime(self):
        # Production composition mutates module-level class bindings by design.
        # Probe it in a child process so this test cannot contaminate unrelated
        # legacy/base-runtime tests in unittest discovery order.
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
        self.assertEqual(value["v8_main_source_fidelity_version"], fidelity.V8_MAIN_SOURCE_FIDELITY_VERSION)
        self.assertIn("V8MainSourceGateProductionStockAgent", " ".join(value["mro"]))


if __name__ == "__main__":
    unittest.main()
