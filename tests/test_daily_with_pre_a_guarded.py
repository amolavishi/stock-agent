import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_agent.daily_with_pre_a_guarded import install_shadow_pointer_guard


class DailyWithPreAGuardedTests(unittest.TestCase):
    def test_guard_normalizes_existing_and_future_legacy_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "legacy.db"
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

            self.assertTrue(install_shadow_pointer_guard(database))

            connection = sqlite3.connect(database)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT hunt_run_id,execution_run_id FROM shadow_runs WHERE shadow_run_id='RUN-OLD'"
                    ).fetchone(),
                    ("", ""),
                )
                connection.execute("INSERT INTO shadow_runs(shadow_run_id) VALUES('RUN-NEW')")
                connection.commit()
                self.assertEqual(
                    connection.execute(
                        "SELECT hunt_run_id,execution_run_id FROM shadow_runs WHERE shadow_run_id='RUN-NEW'"
                    ).fetchone(),
                    ("", ""),
                )
            finally:
                connection.close()

    def test_guard_is_noop_for_normal_null_insert(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "current.db"
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

            self.assertTrue(install_shadow_pointer_guard(database))

            connection = sqlite3.connect(database)
            try:
                connection.execute("INSERT INTO shadow_runs(shadow_run_id) VALUES('RUN-CLEAN')")
                connection.commit()
                self.assertEqual(
                    connection.execute(
                        "SELECT hunt_run_id,execution_run_id FROM shadow_runs WHERE shadow_run_id='RUN-CLEAN'"
                    ).fetchone(),
                    (None, None),
                )
            finally:
                connection.close()

    def test_guard_catches_sentinel_even_when_default_is_expression(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "expression-default.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE shadow_runs (
                        shadow_run_id TEXT PRIMARY KEY,
                        hunt_run_id TEXT NOT NULL DEFAULT ('un' || 'started'),
                        execution_run_id TEXT NOT NULL DEFAULT ('not_' || 'started')
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            self.assertTrue(install_shadow_pointer_guard(database))

            connection = sqlite3.connect(database)
            try:
                connection.execute("INSERT INTO shadow_runs(shadow_run_id) VALUES('RUN-EXPR')")
                connection.commit()
                self.assertEqual(
                    connection.execute(
                        "SELECT hunt_run_id,execution_run_id FROM shadow_runs WHERE shadow_run_id='RUN-EXPR'"
                    ).fetchone(),
                    ("", ""),
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
