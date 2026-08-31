import unittest
from pathlib import Path

from stock_agent.daily_with_pre_a import (
    DailyPreAChainError,
    _option_value,
    _prepare_primary_args,
    _select_changed_report,
)


class DailyWithPreATests(unittest.TestCase):
    def test_option_value_supports_split_and_equals_forms(self):
        self.assertEqual(_option_value(["--shadow-output", "runs"], "--shadow-output", "shadow_runs"), "runs")
        self.assertEqual(_option_value(["--shadow-output=runs2"], "--shadow-output", "shadow_runs"), "runs2")
        self.assertEqual(_option_value([], "--shadow-output", "shadow_runs"), "shadow_runs")

    def test_prepare_primary_args_inserts_daily_shadow_once(self):
        prepared = _prepare_primary_args(["--strict", "--llm-provider", "luna"])
        self.assertEqual(prepared.count("--daily-shadow-run"), 1)
        prepared_existing = _prepare_primary_args(["--daily-shadow-run", "--strict"])
        self.assertEqual(prepared_existing.count("--daily-shadow-run"), 1)

    def test_prepare_primary_args_rejects_v8_mode(self):
        with self.assertRaises(DailyPreAChainError):
            _prepare_primary_args(["--daily-shadow-with-v8"])

    def test_select_changed_report_accepts_one_new_report(self):
        old = Path("old/DAILY_REPORT.md").resolve()
        new = Path("new/DAILY_REPORT.md").resolve()
        selected = _select_changed_report({old: 10}, {old: 10, new: 20})
        self.assertEqual(selected, new)

    def test_select_changed_report_accepts_one_updated_resume_report(self):
        report = Path("run/DAILY_REPORT.md").resolve()
        selected = _select_changed_report({report: 10}, {report: 11})
        self.assertEqual(selected, report)

    def test_select_changed_report_fails_closed_on_ambiguous_reports(self):
        a = Path("a/DAILY_REPORT.md").resolve()
        b = Path("b/DAILY_REPORT.md").resolve()
        with self.assertRaises(DailyPreAChainError):
            _select_changed_report({}, {a: 1, b: 1})
        with self.assertRaises(DailyPreAChainError):
            _select_changed_report({}, {})


if __name__ == "__main__":
    unittest.main()
