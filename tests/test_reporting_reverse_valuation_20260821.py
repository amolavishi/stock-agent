from __future__ import annotations

import unittest

from stock_agent.models import RunMode
from stock_agent.reporting import AuthoritativeHuntReportRenderer, ReportContractError
from tests.test_authoritative_reporting_20260820 import _agent, _fixture


class ReverseValuationReportingTests(unittest.TestCase):
    def test_authoritative_report_projects_reverse_valuation_and_edge_gates(self):
        agent = _agent(_fixture())
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        self.assertEqual(outcome.outcome, "QUALIFIED_CANDIDATE_POOL")
        report = AuthoritativeHuntReportRenderer(agent.store).render(outcome.run_id)
        self.assertIn("REVERSE_VALUATION", report)
        self.assertIn("CATALYST_GATE", report)
        self.assertIn("EXPECTATION_GAP_GATE", report)
        self.assertIn("reverse valuation / expectation gap", report)

    def test_missing_reverse_valuation_artifact_fails_closed(self):
        agent = _agent(_fixture())
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        row = agent.store.connection.execute(
            "SELECT artifact_id FROM raw_artifacts WHERE artifact_type='REVERSE_VALUATION' AND subject_id='SEC1' LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row)
        agent.store.connection.execute("DELETE FROM raw_artifacts WHERE artifact_id=?", (row[0],))
        agent.store.connection.commit()
        with self.assertRaises(ReportContractError):
            AuthoritativeHuntReportRenderer(agent.store).render(outcome.run_id)

    def test_missing_expectation_gap_gate_cannot_render_claimed_pool(self):
        agent = _agent(_fixture())
        outcome = agent.run(RunMode.HUNT_ONLY, {})
        agent.store.connection.execute(
            "DELETE FROM stage_results WHERE run_id=? AND subject_id='SEC1' AND stage='EXPECTATION_GAP_GATE'",
            (outcome.run_id,),
        )
        agent.store.connection.commit()
        with self.assertRaises(ReportContractError):
            AuthoritativeHuntReportRenderer(agent.store).render(outcome.run_id)


if __name__ == "__main__":
    unittest.main()

