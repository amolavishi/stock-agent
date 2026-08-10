from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stock_agent.analysis_context import DebateContextBuilder
from stock_agent.cost_guard import CostGuard
from stock_agent.database import Database
from stock_agent.hermes import HermesCLIAdapter, HermesError, parse_usage_report


USAGE = {
    "estimated_cost_usd": 0.0123, "input_tokens": 1000, "output_tokens": 200,
    "cache_read_tokens": 800, "cache_write_tokens": 10, "reasoning_tokens": 50,
    "total_tokens": 2060, "api_calls": 1, "model": "deepseek-v4-flash",
    "provider": "deepseek", "session_id": "abc", "completed": True, "failed": False,
}


class UsageTelemetryTests(unittest.TestCase):
    def test_usage_sample_parser_preserves_all_buckets(self):
        parsed = parse_usage_report(USAGE)
        self.assertEqual(parsed["input_tokens"], 1000)
        self.assertEqual(parsed["reasoning_tokens"], 50)
        self.assertEqual(parsed["cache_read_tokens"], 800)
        self.assertEqual(parsed["estimated_cost_usd"], 0.0123)

    def test_cli_uses_oneshot_usage_file_and_records_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "hermes.exe"
            executable.write_bytes(b"")
            records = []

            def fake_run(command, **kwargs):
                usage_path = Path(command[command.index("--usage-file") + 1])
                usage_path.write_text(json.dumps(USAGE), encoding="utf-8")
                self.assertIn("-z", command)
                self.assertIn("--safe-mode", command)
                return SimpleNamespace(returncode=0, stdout='{"decision":"WAIT","confidence":70}', stderr="")

            adapter = HermesCLIAdapter(str(executable), "deepseek-v4-flash",
                                       usage_recorder=records.append)
            adapter.set_call_context(run_id="R", request_id="Q", ticker="IONQ", round_no=1,
                                     phase="CHAIRMAN", reasoning_effort="high", repair_attempt=False)
            with patch("stock_agent.hermes.subprocess.run", side_effect=fake_run):
                response = adapter.invoke_json("prompt", "chairman")
            self.assertEqual(response.input_tokens, 1000)
            self.assertEqual(response.estimated_cost_usd, 0.0123)
            self.assertEqual(records[0]["role"], "chairman")
            self.assertEqual(records[0]["round_no"], 1)

    def test_failed_cli_call_still_records_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "hermes.exe"
            executable.write_bytes(b"")
            records = []

            def fake_run(command, **kwargs):
                usage_path = Path(command[command.index("--usage-file") + 1])
                usage_path.write_text(json.dumps(USAGE | {"completed": False, "failed": True}),
                                      encoding="utf-8")
                return SimpleNamespace(returncode=2, stdout="", stderr="provider failed")

            adapter = HermesCLIAdapter(str(executable), "deepseek-v4-flash",
                                       usage_recorder=records.append)
            adapter.set_call_context(run_id="R", role="research", round_no=1,
                                     phase="DEBATE", reasoning_effort="high")
            with patch("stock_agent.hermes.subprocess.run", side_effect=fake_run):
                with self.assertRaises(HermesError):
                    adapter.invoke_json("prompt", "research")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["failed"], 1)
            self.assertEqual(records[0]["input_tokens"], 1000)

    def test_windows_long_prompt_uses_stdin_bridge_not_command_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "Scripts"
            scripts.mkdir()
            executable = scripts / "hermes.exe"
            python = scripts / "python.exe"
            executable.write_bytes(b"")
            python.write_bytes(b"")

            def fake_run(command, **kwargs):
                usage_path = Path(command[command.index("--usage-file") + 1])
                usage_path.write_text(json.dumps(USAGE), encoding="utf-8")
                self.assertNotIn("-z", command)
                self.assertNotIn("x" * 1000, " ".join(command))
                self.assertGreater(len(kwargs["input"]), 40000)
                return SimpleNamespace(returncode=0,
                    stdout='{"decision":"WAIT","confidence":70}', stderr="")

            adapter = HermesCLIAdapter(str(executable), "deepseek-v4-flash")
            with patch("stock_agent.hermes.subprocess.run", side_effect=fake_run):
                adapter.invoke_json("x" * 40000, "chairman")

    def test_timeout_error_names_role_without_echoing_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "hermes.exe"
            executable.write_bytes(b"")
            adapter = HermesCLIAdapter(str(executable), "deepseek-v4-flash", timeout=12)
            adapter.set_call_context(phase="DEBATE_CRITIC")
            with patch("stock_agent.hermes.subprocess.run",
                       side_effect=subprocess.TimeoutExpired(["secret-command"], 12)):
                with self.assertRaisesRegex(HermesError,
                    r"timeout after 12s \(role=critic, phase=DEBATE_CRITIC\)") as caught:
                    adapter.invoke_json("prompt", "critic")
            self.assertNotIn("secret-command", str(caught.exception))

    def test_run_total_is_sum_of_each_call_including_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite"))
            db.init()
            base = {
                "run_id": "R", "request_id": "Q", "ticker": "IONQ", "role": "research",
                "round_no": 1, "phase": "DEBATE", "provider": "deepseek", "model": "m",
                "reasoning_effort": "high", "started_at": "a", "finished_at": "b",
                "latency_ms": 10, "input_tokens": 100, "output_tokens": 20,
                "reasoning_tokens": 5, "cache_read_tokens": 10, "cache_write_tokens": 0,
                "total_tokens": 135, "api_calls": 1, "estimated_cost_usd": 0.01,
                "completed": 1, "failed": 0, "error_type": "", "prompt_chars": 1000,
                "response_chars": 200,
            }
            db.record_llm_call(base | {"api_call_id": "A", "repair_attempt": 0})
            db.record_llm_call(base | {"api_call_id": "B", "phase": "DEBATE_REPAIR",
                                       "repair_attempt": 1, "estimated_cost_usd": 0.02})
            total = db.usage_summary("R")
            self.assertEqual(total["llm_calls"], 2)
            self.assertEqual(total["input_tokens"], 200)
            self.assertAlmostEqual(total["estimated_cost_usd"], 0.03)


class ContextAndCostGuardTests(unittest.TestCase):
    def test_agent_responses_are_bounded_before_next_round(self):
        builder = DebateContextBuilder()
        huge = {"current_decision": "WAIT", "bull_case": ["x" * 5000] * 30,
                "issue_updates": [{"topic": "y" * 5000}] * 30}
        payload = builder.round_payload({"ticker": "IONQ"}, [], huge, huge, [], 2)
        self.assertLess(len(json.dumps(payload)), 12_000)
        self.assertEqual(len(payload["opponent_previous_response"]["bull_case"]), 5)
    def test_round_ten_context_does_not_accumulate_full_history(self):
        context = {"ticker": "IONQ", "evidence_index": [
            {"evidence_id": f"E{i}", "normalized_fact": "x" * 5000} for i in range(30)]}
        history = [{"round": i, "reason": "y" * 1000} for i in range(100)]
        builder = DebateContextBuilder(max_evidence_items=5, max_snippet_chars=200,
                                       max_history_items=4)
        r1 = builder.round_payload(context, [], {}, {}, history[:10], 1)
        r10 = builder.round_payload(context, [], {}, {}, history, 10)
        size1 = len(json.dumps(r1))
        size10 = len(json.dumps(r10))
        self.assertLess(size10, size1 * 1.2)
        self.assertEqual(len(r10["thesis_change_history"]), 4)
        self.assertEqual(len(r10["canonical_analysis_context"]["evidence_index"]), 5)

    def test_cost_guard_distinguishes_minimum_round_completion(self):
        guard = CostGuard({"mode": "WARN", "soft_cost_limit_usd": 0.1,
                           "hard_cost_limit_usd": 0.2})
        self.assertEqual(guard.evaluate(0.2, False).action, "STOP_INCOMPLETE")
        self.assertEqual(guard.evaluate(0.2, True).action, "STOP_COMPLETE")
        self.assertEqual(guard.evaluate(0.1, False).action, "WARN")


if __name__ == "__main__":
    unittest.main()
