from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_agent.pre_a_source_v2 import (
    _authoritative_certification_grade,
    build_pre_a_source_bundle,
)
from tests.test_pre_a_source_v2 import _make_db
from tests.test_pre_a_v8_next_compat import valid_b_plus


class PreAAuthorityHardeningTests(unittest.TestCase):
    def test_shadow_decision_b_plus_cannot_substitute_for_invalid_certification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "primary.db"
            _make_db(database, decision_grade="B+", certification_grade=None)
            bundle = build_pre_a_source_bundle(database, "SHADOW-1")
            candidate = bundle["candidates"][0]
            self.assertEqual(candidate["decision_grade_non_authoritative"], "B+")
            self.assertFalse(candidate["certification_valid"])
            self.assertEqual(candidate["certification_grade"], "UNKNOWN")
            self.assertEqual(candidate["source_grade"], "UNKNOWN")

    def test_failed_certification_stage_is_not_authoritative_even_with_b_plus_payload(self):
        cert_entry = {
            "status": "FAILED",
            "result": {
                "source_sha256": "26fddaa0b0ddec166427d89a50ad0f272d06ee6d43a6b91995f45fefaa039528",
                "grade_authority": "V8_STEP18_CANONICAL",
                "discovery_score_used": False,
                "research_grade": "B+",
            },
        }
        self.assertIsNone(_authoritative_certification_grade(cert_entry, {"V8_CERTIFICATION": cert_entry}))

    def test_v8_next_b_plus_requires_step20_pass(self):
        cert_entry = {"status": "SUCCEEDED", "result": valid_b_plus()}
        self.assertIsNone(_authoritative_certification_grade(cert_entry, {"V8_CERTIFICATION": cert_entry}))
        stages = {
            "V8_CERTIFICATION": cert_entry,
            "V8_RESEARCH_VALIDATOR": {
                "status": "SUCCEEDED",
                "result": {"status": "PASS", "route": "RETURN_TO_STEP17_5"},
            },
        }
        self.assertIsNone(_authoritative_certification_grade(cert_entry, stages))
        stages["V8_RESEARCH_VALIDATOR"] = {
            "status": "SUCCEEDED",
            "result": {"status": "PASS", "route": "PASS"},
        }
        self.assertEqual(_authoritative_certification_grade(cert_entry, stages), "B+")

    def test_shadow_decision_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "primary.db"
            _make_db(database, decision_grade="B+", certification_grade="B+")
            connection = sqlite3.connect(database)
            try:
                tampered = {"decision_id": "DEC-1", "ticker": "ABC", "grade": "A", "not_evaluated": False}
                connection.execute(
                    "UPDATE shadow_decisions SET decision_json=? WHERE shadow_run_id='SHADOW-1' AND ticker='ABC'",
                    (json.dumps(tampered),),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(Exception, "ShadowDecision hash mismatch"):
                build_pre_a_source_bundle(database, "SHADOW-1")


if __name__ == "__main__":
    unittest.main()
