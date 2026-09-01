from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_agent.daily_with_pre_a import _primary_is_pre_a_evaluable, _snapshot_reports
from stock_agent.shadow_non_evaluable_guard import classify_hunt_conclusion
from stock_agent.v8_next_terminal_lineage import _is_upstream_failure


class LiveShadowFailClosedV14Tests(unittest.TestCase):
    def test_run003_terminal_cannot_be_clean_no_trade(self):
        conclusion, clean = classify_hunt_conclusion("NOT_EVALUABLE_DISCOVERY_COVERAGE", [])
        self.assertEqual(conclusion, "NOT_EVALUABLE_DISCOVERY_COVERAGE")
        self.assertFalse(clean)

    def test_pre_discovery_block_cannot_be_clean_no_trade(self):
        conclusion, clean = classify_hunt_conclusion(
            "NO_QUALIFIED_CANDIDATE",
            [{"classification": "PRE_DISCOVERY_BLOCK", "component": "HUNT", "error": "UNIVERSE_PROVIDER"}],
        )
        self.assertEqual(conclusion, "NOT_EVALUABLE_PIPELINE_FAILURE")
        self.assertFalse(clean)

    def test_evaluable_no_candidate_can_remain_clean_no_trade(self):
        conclusion, clean = classify_hunt_conclusion("NO_QUALIFIED_CANDIDATE", [])
        self.assertEqual(conclusion, "NO_TRADE")
        self.assertTrue(clean)

    def test_pre_a_finds_canonical_two_level_shadow_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "2026-09-01" / "RUN-20260901-003" / "DAILY_REPORT.md"
            report.parent.mkdir(parents=True)
            report.write_text("report", encoding="utf-8")
            snapshot = _snapshot_reports(root)
            self.assertIn(report.resolve(), snapshot)

    def test_pre_a_skips_non_evaluable_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "2026-09-01" / "RUN-20260901-003" / "DAILY_REPORT.md"
            report.parent.mkdir(parents=True)
            report.write_text("report", encoding="utf-8")
            (report.parent / "RUN_LOG.json").write_text(json.dumps({
                "status": "DEGRADED",
                "investment_conclusion": "NOT_EVALUABLE_DISCOVERY_COVERAGE",
                "hunt_contract": {"status": "FAILED", "result": "NOT_EVALUABLE_DISCOVERY_COVERAGE"},
            }), encoding="utf-8")
            evaluable, reason = _primary_is_pre_a_evaluable(report)
            self.assertFalse(evaluable)
            self.assertIn("DEGRADED", reason)

    def test_upstream_failure_classification_is_preserved(self):
        self.assertTrue(_is_upstream_failure("BLOCKED_BY_EVIDENCE_GAP"))
        self.assertTrue(_is_upstream_failure("NOT_EVALUABLE_PIPELINE_FAILURE"))
        self.assertFalse(_is_upstream_failure("NO_QUALIFIED_CANDIDATE"))

    def test_composed_report_never_renders_no_trade_for_non_evaluable(self):
        import stock_agent.production  # noqa: F401
        from stock_agent import shadow

        log = {
            "started_at": "2026-09-01T00:00:00Z",
            "run_id": "RUN-X",
            "shadow_version": "SHADOW_V1.3",
            "code_git_sha": "x",
            "git_diff_hash": "x",
            "source_tree_hash": "x",
            "git_dirty": False,
            "strategy_cohort_hash": "x",
            "providers": {},
            "market_context": {"analysis": {}},
            "universe": {"raw": 0},
            "investment_conclusion": "NOT_EVALUABLE_DISCOVERY_COVERAGE",
            "investment_conclusion_is_clean_no_trade": False,
        }
        text = shadow.DailyShadowRunner._report(log, [])
        self.assertIn("NOT_EVALUABLE_DISCOVERY_COVERAGE", text)
        self.assertNotIn("- NO_TRADE", text)


if __name__ == "__main__":
    unittest.main()
