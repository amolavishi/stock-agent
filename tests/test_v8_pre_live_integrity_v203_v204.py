from __future__ import annotations

import json
import sqlite3
import unittest

from stock_agent import v8_main_discovery_integrity as integrity
from stock_agent.v8_pre_live_integrity_v201 import default_scanner_v201
from stock_agent.v8_pre_live_integrity_v203 import origin_map_for_evidence_v203
from stock_agent.v8_pre_live_integrity_v204 import (
    contract_complete_v204,
    sentinel_exact_coverage_v204,
    sentinel_sample_v204,
)


class OriginStore:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE raw_artifacts (artifact_id TEXT PRIMARY KEY, artifact_type TEXT, provider TEXT, payload_hash TEXT, payload_json TEXT)"
        )
        self.connection.execute(
            "CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, raw_artifact_id TEXT, status TEXT)"
        )

    def add_raw(self, artifact_id, payload, artifact_type="RESEARCH_EVIDENCE", provider="research", payload_hash=None):
        self.connection.execute(
            "INSERT INTO raw_artifacts VALUES(?,?,?,?,?)",
            (artifact_id, artifact_type, provider, payload_hash or artifact_id + "-hash", json.dumps(payload, sort_keys=True)),
        )

    def add_evidence(self, evidence_id, artifact_id):
        self.connection.execute("INSERT INTO evidence VALUES(?,?,'ACTIVE')", (evidence_id, artifact_id))


class V8PreLiveIntegrityV203V204Tests(unittest.TestCase):
    @staticmethod
    def coverage_row(sid, *, disposition="NO_SIGNAL", failure="NONE", cheap="PASS", evidence=None):
        return {
            "security_id": sid,
            "disposition": disposition,
            "failure_class": failure,
            "signal_strength": "NONE" if disposition == "NO_SIGNAL" else "UNKNOWN",
            "research_value": "LOW",
            "cheap_hard_gate_status": cheap,
            "evidence_ids": list(evidence or []),
            "rationale": "fixture",
        }

    def test_parent_research_envelope_is_not_second_origin_when_children_exist(self):
        store = OriginStore()
        store.add_raw("PARENT", {"content": "aggregate"})
        store.add_evidence("E-PARENT", "PARENT")
        store.add_raw("CHILD", {"parent_research_artifact_id": "PARENT", "content": "source"}, artifact_type="RESEARCH_SOURCE_EVIDENCE")
        store.add_evidence("E-CHILD", "CHILD")
        child_origin = "ORIGIN-" + "a" * 24
        mapping = origin_map_for_evidence_v203(
            store, ["E-PARENT", "E-CHILD"], {"E-CHILD": child_origin}
        )
        self.assertEqual(mapping["E-CHILD"], child_origin)
        self.assertNotIn("E-PARENT", mapping)

    def test_exclude_without_hard_fail_and_evidence_is_invalid(self):
        value = default_scanner_v201("02", 1)
        value["coverage_ledger"] = [self.coverage_row("AAA", disposition="EXCLUDE", failure="NONE", evidence=[])]
        complete, failures = contract_complete_v204("02", value, 1)
        self.assertFalse(complete)
        self.assertIn("EXCLUDE_WITHOUT_HARD_FAIL:AAA", failures)
        self.assertIn("EXCLUDE_WITHOUT_EVIDENCE:AAA", failures)

    def test_structural_fail_requires_verified_cheap_gate_fail_and_evidence(self):
        value = default_scanner_v201("10", 1)
        value["coverage_ledger"] = [
            self.coverage_row("TOX", disposition="EXCLUDE", failure="STRUCTURAL_HARD_FAIL", cheap="UNKNOWN", evidence=[])
        ]
        complete, failures = contract_complete_v204("10", value, 1)
        self.assertFalse(complete)
        self.assertIn("STRUCTURAL_FAIL_WITHOUT_CHEAP_GATE_FAIL:TOX", failures)
        self.assertIn("STRUCTURAL_FAIL_WITHOUT_EVIDENCE:TOX", failures)

    def test_retained_coverage_row_requires_actual_candidate(self):
        value = default_scanner_v201("02", 1)
        value["coverage_ledger"] = [self.coverage_row("AAA", disposition="RETAINED")]
        complete, failures = contract_complete_v204("02", value, 1)
        self.assertFalse(complete)
        self.assertIn("RETAINED_LEDGER_ROW_MISSING_CANDIDATE:AAA", failures)

    def test_source_exhaustion_cannot_coexist_with_open_expansion_questions(self):
        value = default_scanner_v201("02", 1)
        value["coverage_ledger"] = [self.coverage_row("AAA")]
        value["source_exhaustion"] = True
        value["source_exhaustion_reason"] = "ALL_CURRENT_SEARCH_PATHS_CHECKED"
        value["search_expansion_questions"] = ["search another cohort"]
        complete, failures = contract_complete_v204("02", value, 1)
        self.assertFalse(complete)
        self.assertIn("SOURCE_EXHAUSTED_WITH_OPEN_EXPANSION_QUESTIONS", failures)

    def test_source_exhaustion_true_requires_reason(self):
        value = default_scanner_v201("02", 1)
        value["coverage_ledger"] = [self.coverage_row("AAA")]
        value["source_exhaustion"] = True
        value["source_exhaustion_reason"] = "NOT_PROVEN"
        complete, failures = contract_complete_v204("02", value, 1)
        self.assertFalse(complete)
        self.assertIn("SOURCE_EXHAUSTED_WITHOUT_REASON", failures)

    def test_sentinel_sample_represents_all_scanners_with_rejected_rows(self):
        results = []
        for scanner_id in integrity.SCANNER_REQUIRED_DIMENSIONS:
            value = default_scanner_v201(scanner_id, 1)
            value["coverage_ledger"] = [self.coverage_row(f"S{scanner_id}", disposition="NO_SIGNAL")]
            results.append(value)
        sample = sentinel_sample_v204(results, 30)
        self.assertEqual(
            {item["scanner_id"] for item in sample},
            set(integrity.SCANNER_REQUIRED_DIMENSIONS),
        )

    def test_sentinel_complete_with_missing_audit_row_is_not_complete(self):
        expected = [
            {"security_id": "AAA", "scanner_id": "02"},
            {"security_id": "BBB", "scanner_id": "03"},
        ]
        sentinel = {
            "status": "COMPLETE",
            "grade_authority": False,
            "audits": [{"security_id": "AAA", "scanner_id": "02", "finding": "OK", "rationale": "checked"}],
        }
        exact, details = sentinel_exact_coverage_v204(expected, sentinel)
        self.assertFalse(exact)
        self.assertEqual(details["missing_pairs"], [("BBB", "03")])

    def test_sentinel_duplicate_rows_are_not_exact_completion(self):
        expected = [{"security_id": "AAA", "scanner_id": "02"}]
        sentinel = {
            "status": "COMPLETE",
            "grade_authority": False,
            "audits": [
                {"security_id": "AAA", "scanner_id": "02"},
                {"security_id": "AAA", "scanner_id": "02"},
            ],
        }
        exact, details = sentinel_exact_coverage_v204(expected, sentinel)
        self.assertFalse(exact)
        self.assertEqual(details["duplicate_audit_rows"], 1)


if __name__ == "__main__":
    unittest.main()
