from __future__ import annotations

import unittest

from stock_agent.adapters import RecordedPortfolioProvider
from stock_agent.models import RunMode, canonical_hash, utc_now
from tests.test_production_adapters import ProductionAdapterTests


class ExistingPositionWatchTests(unittest.TestCase):
    @staticmethod
    def _scenario() -> dict:
        scenario = {
            "bull_value": 14.0,
            "base_value": 10.5,
            "bear_value": 7.0,
            "bull_probability": 0.3,
            "base_probability": 0.5,
            "bear_probability": 0.2,
            "opportunity_cost_score": 0.1,
            "current_price": 10.0,
            "evidence_ids": ["E1"],
            "source_stage_lineage": ["DEEP_RESEARCH", "FULL_SEC_FORENSIC", "ADVERSARIAL_AUDIT", "PORTFOLIO_REVIEW"],
        }
        scenario["scenario_value_hash"] = canonical_hash({
            "security_id": "SEC1",
            "evidence_ids": ["E1"],
            "bull_value": 14.0,
            "base_value": 10.5,
            "bear_value": 7.0,
            "bull_probability": 0.3,
            "base_probability": 0.5,
            "bear_probability": 0.2,
            "opportunity_cost_score": 0.1,
            "source_stage_lineage": ["ADVERSARIAL_AUDIT", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "PORTFOLIO_REVIEW"],
        })
        return scenario

    def test_existing_position_watch_commits_zero_allocation_with_same_run_receipt(self):
        agent = ProductionAdapterTests().make()
        stamp = utc_now()
        agent.config.portfolio_provider = RecordedPortfolioProvider({
            "as_of": stamp,
            "cash": 1000.0,
            "total_equity": 1050.0,
            "positions": [{"subject_id": "SEC1", "shares": 5, "average_cost": 9.5, "as_of": stamp}],
        })
        outcome = agent.run(
            RunMode.HUNT_AND_EXECUTION_REVIEW,
            {"requested_action": "WATCH"},
        )
        self.assertEqual(outcome.outcome, "FINAL_ACTION_COMMITTED", outcome.blocked_reason)
        self.assertEqual(outcome.authoritative_action.value, "WATCH")
        self.assertEqual(outcome.allocation["shares"], 0)
        self.assertEqual(outcome.allocation["capital_pct"], 0.0)
        receipt = outcome.allocation.get("position_snapshot_receipt")
        self.assertIsInstance(receipt, dict)
        self.assertEqual(receipt.get("receipt_type"), "PositionSnapshotReceiptV2")
        self.assertEqual(receipt.get("subject_id"), "SEC1")
        self.assertIs(receipt.get("position_exists"), True)
        stage = agent.store.get_stage_result(outcome.run_id, "POSITION_SNAPSHOT_RECEIPT", "SEC1")
        self.assertIsNotNone(stage)


if __name__ == "__main__":
    unittest.main()

