from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from stock_agent.v8_gui import (
    _report_text,
    build_cli_command,
    bundle_summary,
    run_once,
)
from stock_agent.v8_challenger import V8PromptBundle


STAGES = ["00A", "01"] + [f"{number:02d}" for number in range(2, 19)]


class V8GuiContractTests(unittest.TestCase):
    def _bundle(self, directory: Path) -> Path:
        source = directory / "v8.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("README_V8_PIPELINE.md", "bundle")
            for stage in STAGES:
                archive.writestr(f"{stage}_stage_V8.md", f"# {stage}")
            archive.writestr("RUN_ALL_V8_MASTER_PROMPT.md", "run")
        return source

    def test_bundle_summary_reports_phase1_hash_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = bundle_summary(self._bundle(Path(tmp)))
            self.assertEqual(summary["stage_count"], 19)
            self.assertEqual(summary["phase1_stages"], STAGES)
            self.assertEqual(len(summary["bundle_hash"]), 64)

    def test_command_is_canonical_read_only_invocation(self):
        command = build_cli_command("bundle.zip", "manifest.json", "candidates.json", "evidence.json", "runs")
        self.assertEqual(command[1:3], ["-m", "stock_agent.v8_challenger"])
        self.assertIn("--reasoning-effort", command)
        self.assertNotIn("buy", " ".join(command).lower())
        self.assertNotIn("sell", " ".join(command).lower())
        self.assertNotIn("place_order", " ".join(command).lower())

    def test_missing_primary_inputs_fail_closed_and_write_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self._bundle(root)
            payload, report = run_once(
                bundle=bundle,
                manifest=root / "missing-manifest.json",
                candidates=root / "missing-candidates.json",
                evidence=root / "missing-evidence.json",
                output_root=root / "shadow_runs",
            )
            self.assertEqual(payload["status"], "BLOCKED_INPUT")
            self.assertEqual(payload["broker_write_count"], 0)
            self.assertTrue(report.is_file())
            text = report.read_text(encoding="utf-8")
            self.assertIn("missing immutable Primary input", text)
            self.assertIn("ORDER_EXECUTED = NO", text)

    def test_report_redacts_secret_like_values(self):
        report = _report_text({"status": "FAILED", "errors": ["bearer sk-test-secret"], "api_key": "sk-test-secret"})
        self.assertNotIn("sk-test-secret", report)
        self.assertIn("[REDACTED]", report)
        self.assertIn("## Errors\n\n- [REDACTED]\n\n## Artifacts", report)

    def test_existing_inputs_use_canonical_cli_and_persist_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self._bundle(root)
            manifest = root / "manifest.json"
            candidates = root / "candidates.json"
            evidence = root / "evidence.json"
            for path, value in ((manifest, {}), (candidates, []), (evidence, [])):
                path.write_text(json.dumps(value), encoding="utf-8")
            cli_payload = {"status": "SUCCEEDED", "challenger_run_id": "V8-1", "candidate_count": 1}
            completed = subprocess.CompletedProcess([], 0, json.dumps(cli_payload), "")
            with patch("stock_agent.v8_gui.subprocess.run", return_value=completed) as mocked:
                payload, report = run_once(
                    bundle=bundle,
                    manifest=manifest,
                    candidates=candidates,
                    evidence=evidence,
                    output_root=root / "shadow_runs",
                    reasoning_effort="high",
                )
            self.assertEqual(payload["status"], "SUCCEEDED")
            self.assertEqual(payload["broker_write_count"], 0)
            self.assertTrue(report.is_file())
            command = mocked.call_args.args[0]
            self.assertIn("stock_agent.v8_challenger", command)
            self.assertEqual(command[command.index("--reasoning-effort") + 1], "high")
            self.assertIn("V8-1", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
