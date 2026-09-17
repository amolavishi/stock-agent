from __future__ import annotations

import unittest
from types import SimpleNamespace

from stock_agent.v8_main_recall_conservation import _record_debt


class Store:
    def __init__(self):
        self.rows = []
    def dependency_hash(self, ids, rule_hash, context_hash):
        return "dep"
    def record_stage_result(self, run_id, work_item_id, stage, subject_id, payload, dependency_ids, dependency_hash, evidence_epoch, status="SUCCEEDED"):
        self.rows.append({"stage": stage, "subject_id": subject_id, "payload": payload, "status": status})
        return "R1"


class Agent:
    def __init__(self):
        self.store = Store()


class MainRecallConservationTests(unittest.TestCase):
    def test_missing_technical_is_evidence_debt_not_reject(self):
        agent = Agent()
        run = SimpleNamespace(run_id="RUN", rule_set=SimpleNamespace(rule_set_hash="rules"), context_manifest_hash="ctx")
        _record_debt(agent, run, "ABC", "DEEP_DIVE_NOW")
        row = agent.store.rows[0]
        self.assertEqual(row["stage"], "DISCOVERY_TECHNICAL_EVIDENCE_DEBT")
        self.assertEqual(row["payload"]["status"], "EVIDENCE_DEBT")
        self.assertEqual(row["payload"]["discovery_action_after_debt"], "DEEP_DIVE_SECONDARY")
        self.assertFalse(row["payload"]["research_grade_authority"])
        self.assertFalse(row["payload"]["execution_authority"])
        self.assertNotIn("EXCLUDE", row["payload"].values())


if __name__ == "__main__":
    unittest.main()
