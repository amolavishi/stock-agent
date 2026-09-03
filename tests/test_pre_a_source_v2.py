import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_agent.models import canonical_hash
from stock_agent.pre_a_source_v2 import STEP18_SOURCE_SHA256, PreASourceError, build_pre_a_source_bundle


def _make_db(path: Path, *, decision_grade=None, certification_grade="B+") -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE shadow_runs (
                shadow_run_id TEXT PRIMARY KEY,
                shadow_version TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                hunt_run_id TEXT,
                execution_run_id TEXT,
                broker_write_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE shadow_decisions (
                decision_id TEXT PRIMARY KEY,
                shadow_run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                decision_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE stage_results (
                result_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                work_item_id TEXT,
                stage TEXT NOT NULL,
                subject_id TEXT,
                result_json TEXT NOT NULL,
                dependency_ids_json TEXT NOT NULL DEFAULT '[]',
                dependency_hash TEXT NOT NULL,
                evidence_epoch INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO shadow_runs VALUES(?,?,?,?,?,?,?,?)",
            ("SHADOW-1", "SHADOW_V1.3", "SUCCEEDED", "2026-09-01T00:00:00Z", "2026-09-01T01:00:00Z", "HUNT-1", None, 0),
        )
        decision = {"decision_id": "DEC-1", "ticker": "ABC", "grade": decision_grade, "not_evaluated": False}
        connection.execute(
            "INSERT INTO shadow_decisions VALUES(?,?,?,?,?,?)",
            ("DEC-1", "SHADOW-1", "ABC", json.dumps(decision), canonical_hash(decision), "2026-09-01T01:00:00Z"),
        )
        cert = {
            "source_sha256": STEP18_SOURCE_SHA256,
            "grade_authority": "V8_STEP18_CANONICAL",
            "discovery_score_used": False,
            "research_grade": certification_grade,
        }
        connection.execute(
            "INSERT INTO stage_results VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("R1", "HUNT-1", None, "V8_CERTIFICATION", "ABC", json.dumps(cert), "[]", "h", 1, "SUCCEEDED", "2026-09-01T00:50:00Z"),
        )
        connection.execute(
            "INSERT INTO stage_results VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("R2", "HUNT-1", None, "CAPITAL_PRESCREEN_GATE", "ABC", json.dumps({"decision": "PASS"}), "[]", "h2", 1, "SUCCEEDED", "2026-09-01T00:40:00Z"),
        )
        connection.commit()
    finally:
        connection.close()


class PreASourceV2Tests(unittest.TestCase):
    def test_bundle_uses_structured_step18_grade_and_does_not_modify_db(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "primary.db"
            _make_db(database, decision_grade=None, certification_grade="B+")
            before = database.read_bytes()
            bundle = build_pre_a_source_bundle(database, "SHADOW-1")
            after = database.read_bytes()
            self.assertEqual(before, after)
            self.assertFalse(bundle["primary_mutation"])
            self.assertEqual(bundle["broker_write_count"], 0)
            self.assertEqual(bundle["candidates"][0]["source_grade"], "B+")
            self.assertIn("V8_CERTIFICATION", bundle["candidates"][0]["stages"])

    def test_non_authoritative_decision_conflict_does_not_erase_certification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "primary.db"
            _make_db(database, decision_grade="A-", certification_grade="B+")
            candidate = build_pre_a_source_bundle(database, "SHADOW-1")["candidates"][0]
            self.assertTrue(candidate["grade_conflict"])
            self.assertEqual(candidate["certification_grade"], "B+")
            self.assertEqual(candidate["source_grade"], "B+")
            self.assertEqual(candidate["decision_grade_non_authoritative"], "A-")

    def test_authoritative_exclude_is_not_weakened_to_unknown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "primary.db"
            _make_db(database, decision_grade=None, certification_grade="EXCLUDE")
            candidate = build_pre_a_source_bundle(database, "SHADOW-1")["candidates"][0]
            self.assertTrue(candidate["certification_valid"])
            self.assertEqual(candidate["source_grade"], "EXCLUDE")

    def test_nonzero_broker_write_count_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "primary.db"
            _make_db(database)
            connection = sqlite3.connect(database)
            try:
                connection.execute("UPDATE shadow_runs SET broker_write_count=1")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(PreASourceError, "broker_write=0"):
                build_pre_a_source_bundle(database, "SHADOW-1")


if __name__ == "__main__":
    unittest.main()
