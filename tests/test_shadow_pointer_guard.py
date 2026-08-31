from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_agent.models import EffectiveRuleSet, RunMode
from stock_agent.store import SQLiteStore


def _create_legacy_shadow_table(database: Path, *, expression_default: bool = False) -> None:
    connection = sqlite3.connect(database)
    try:
        default = "('un' || 'started')" if expression_default else "'unstarted'"
        connection.execute(
            f"""
            CREATE TABLE shadow_runs (
                shadow_run_id TEXT PRIMARY KEY,
                shadow_version TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                metadata_json TEXT NOT NULL,
                checkpoint TEXT NOT NULL DEFAULT 'CREATED',
                hunt_run_id TEXT NOT NULL DEFAULT {default},
                execution_run_id TEXT NOT NULL DEFAULT {default},
                error_json TEXT NOT NULL DEFAULT '[]',
                warning_json TEXT NOT NULL DEFAULT '[]',
                health_json TEXT NOT NULL DEFAULT '{{}}',
                broker_write_count INTEGER NOT NULL DEFAULT 0,
                original_shadow_run_id TEXT,
                idempotency_key TEXT UNIQUE
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


class ShadowPointerGuardTests(unittest.TestCase):
    def test_new_legacy_row_cannot_escape_as_unstarted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "legacy.db"
            _create_legacy_shadow_table(database)
            store = SQLiteStore(database)
            try:
                shadow = store.reserve_shadow_run("2026-08-31", "SHADOW_TEST", {})
                self.assertFalse(shadow["hunt_run_id"])
                self.assertFalse(shadow["execution_run_id"])

                persisted = store.connection.execute(
                    "SELECT hunt_run_id,execution_run_id FROM shadow_runs WHERE shadow_run_id=?",
                    (shadow["shadow_run_id"],),
                ).fetchone()
                self.assertEqual(tuple(persisted), ("", ""))

                # Reproduce the exact control flow that previously crashed at
                # DailyShadowRunner -> get_run(hunt_run_id).
                hunt_run_id = shadow.get("hunt_run_id")
                if not hunt_run_id:
                    hunt = store.create_run(RunMode.HUNT_ONLY, EffectiveRuleSet(), "ctx", 0)
                    hunt_run_id = hunt.run_id
                    store.finish_run(hunt_run_id, "NO_QUALIFIED_CANDIDATE")
                hunt_state = store.get_run(hunt_run_id)
                self.assertEqual(hunt_state.run_id, hunt_run_id)
                self.assertEqual(hunt_state.outcome, "NO_QUALIFIED_CANDIDATE")
            finally:
                store.close()

    def test_expression_default_cannot_escape_as_unstarted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "expression-default.db"
            _create_legacy_shadow_table(database, expression_default=True)
            store = SQLiteStore(database)
            try:
                shadow = store.reserve_shadow_run("2026-08-31", "SHADOW_TEST", {})
                self.assertFalse(shadow["hunt_run_id"])
                self.assertFalse(shadow["execution_run_id"])
            finally:
                store.close()

    def test_resume_repairs_persisted_sentinel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "resume.db"
            _create_legacy_shadow_table(database)
            store = SQLiteStore(database)
            try:
                shadow = store.reserve_shadow_run("2026-08-31", "SHADOW_TEST", {})
                store.connection.execute(
                    "UPDATE shadow_runs SET hunt_run_id='unstarted', execution_run_id='not_started', status='FAILED' WHERE shadow_run_id=?",
                    (shadow["shadow_run_id"],),
                )
                resumed = store.reserve_shadow_run(
                    "2026-08-31", "SHADOW_TEST", {}, resume_run_id=shadow["shadow_run_id"]
                )
                self.assertFalse(resumed["hunt_run_id"])
                self.assertFalse(resumed["execution_run_id"])
            finally:
                store.close()

    def test_current_nullable_schema_remains_null(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "current.db"
            store = SQLiteStore(database)
            try:
                shadow = store.reserve_shadow_run("2026-08-31", "SHADOW_TEST", {})
                self.assertIsNone(shadow["hunt_run_id"])
                self.assertIsNone(shadow["execution_run_id"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
