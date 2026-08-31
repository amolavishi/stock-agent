import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_agent.daily_with_pre_a import (
    DailyPreAChainError,
    _legacy_default_is_sentinel,
    _option_value,
    _prepare_primary_args,
    _repair_legacy_shadow_schema,
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

    def test_legacy_default_parser_accepts_parenthesized_sqlite_constant(self):
        self.assertTrue(_legacy_default_is_sentinel("('unstarted')"))
        self.assertTrue(_legacy_default_is_sentinel('("UNSTARTED")'))
        self.assertFalse(_legacy_default_is_sentinel("'real-run-id'"))

    def test_legacy_unstarted_shadow_defaults_are_normalized_for_existing_and_future_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "legacy-shadow.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE shadow_runs (
                        shadow_run_id TEXT PRIMARY KEY,
                        hunt_run_id TEXT NOT NULL DEFAULT 'unstarted',
                        execution_run_id TEXT NOT NULL DEFAULT 'unstarted'
                    )
                    """
                )
                connection.execute("INSERT INTO shadow_runs(shadow_run_id) VALUES('RUN-OLD')")
                connection.commit()
            finally:
                connection.close()

            self.assertTrue(_repair_legacy_shadow_schema(database))

            connection = sqlite3.connect(database)
            try:
                old_row = connection.execute(
                    "SELECT hunt_run_id,execution_run_id FROM shadow_runs WHERE shadow_run_id='RUN-OLD'"
                ).fetchone()
                self.assertEqual(old_row, ("", ""))

                connection.execute("INSERT INTO shadow_runs(shadow_run_id) VALUES('RUN-NEW')")
                connection.commit()
                new_row = connection.execute(
                    "SELECT hunt_run_id,execution_run_id FROM shadow_runs WHERE shadow_run_id='RUN-NEW'"
                ).fetchone()
                self.assertEqual(new_row, ("", ""))
            finally:
                connection.close()

    def test_persisted_sentinel_is_repaired_even_when_nullable_schema_has_no_legacy_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "persisted-sentinel.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE shadow_runs (
                        shadow_run_id TEXT PRIMARY KEY,
                        hunt_run_id TEXT,
                        execution_run_id TEXT
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO shadow_runs(shadow_run_id,hunt_run_id,execution_run_id) VALUES('RUN-BAD','unstarted','not_started')"
                )
                connection.commit()
            finally:
                connection.close()

            self.assertTrue(_repair_legacy_shadow_schema(database))
            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    "SELECT hunt_run_id,execution_run_id FROM shadow_runs WHERE shadow_run_id='RUN-BAD'"
                ).fetchone()
                self.assertEqual(row, (None, None))
                trigger = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name='shadow_runs_legacy_sentinel_normalizer'"
                ).fetchone()
                self.assertIsNone(trigger)
            finally:
                connection.close()

    def test_current_nullable_shadow_schema_needs_no_compatibility_trigger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "current-shadow.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE shadow_runs (
                        shadow_run_id TEXT PRIMARY KEY,
                        hunt_run_id TEXT,
                        execution_run_id TEXT
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            self.assertFalse(_repair_legacy_shadow_schema(database))
            connection = sqlite3.connect(database)
            try:
                trigger = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name='shadow_runs_legacy_sentinel_normalizer'"
                ).fetchone()
                self.assertIsNone(trigger)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
