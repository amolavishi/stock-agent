from __future__ import annotations

import sqlite3
import unittest

from stock_agent.hunt_integrity_v182 import _selected_candidate_has_unresolved_failure


class _Store:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            "CREATE TABLE stage_results (run_id TEXT, subject_id TEXT, stage TEXT)"
        )

    def close(self):
        self.connection.close()


class AllocationIsolationV182Tests(unittest.TestCase):
    def test_other_candidate_failure_does_not_poison_selected_candidate(self):
        store = _Store()
        try:
            store.connection.execute(
                "INSERT INTO stage_results VALUES(?,?,?)",
                ("RUN-1", "A", "CANDIDATE_ENGINEERING_FAILURE"),
            )
            self.assertTrue(_selected_candidate_has_unresolved_failure(store, "RUN-1", "A"))
            self.assertFalse(_selected_candidate_has_unresolved_failure(store, "RUN-1", "B"))
        finally:
            store.close()

    def test_selected_candidate_provider_failure_remains_hard_block(self):
        store = _Store()
        try:
            store.connection.execute(
                "INSERT INTO stage_results VALUES(?,?,?)",
                ("RUN-1", "B", "SEC_PROVIDER_FAILURE"),
            )
            self.assertTrue(_selected_candidate_has_unresolved_failure(store, "RUN-1", "B"))
        finally:
            store.close()

    def test_missing_subject_fails_closed(self):
        store = _Store()
        try:
            self.assertTrue(_selected_candidate_has_unresolved_failure(store, "RUN-1", ""))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
