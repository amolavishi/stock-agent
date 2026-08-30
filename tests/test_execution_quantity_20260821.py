from __future__ import annotations

import unittest

from stock_agent.execution_quantity import ExecutionQuantityError, transaction_shares
from stock_agent.models import ExecutionAction


class ExecutionQuantityTests(unittest.TestCase):
    def test_watch_and_no_trade_are_always_zero(self):
        for action in (ExecutionAction.WATCH, ExecutionAction.NO_TRADE):
            self.assertEqual(transaction_shares(action, position_shares=9, risk_target_shares=15, price=10, equity=1000), 0)

    def test_starter_is_new_position_risk_target(self):
        self.assertEqual(transaction_shares(ExecutionAction.STARTER, position_shares=0, risk_target_shares=4, price=10, equity=1000), 4)
        with self.assertRaises(ExecutionQuantityError):
            transaction_shares(ExecutionAction.STARTER, position_shares=1, risk_target_shares=4, price=10, equity=1000)

    def test_add_uses_planned_delta_not_total_risk_target(self):
        plan = {"planned_add_shares": 3, "planned_add_capital_pct": None, "resulting_position_cap": {"shares": 12}}
        self.assertEqual(transaction_shares(ExecutionAction.ADD, position_shares=5, risk_target_shares=10, price=10, equity=1000, add_plan=plan), 3)

    def test_add_is_capped_by_risk_and_resulting_position(self):
        plan = {"planned_add_shares": 8, "planned_add_capital_pct": None, "resulting_position_cap": {"shares": 9}}
        self.assertEqual(transaction_shares(ExecutionAction.ADD, position_shares=5, risk_target_shares=7, price=10, equity=1000, add_plan=plan), 2)

    def test_full_is_delta_to_python_target_and_may_be_zero(self):
        self.assertEqual(transaction_shares(ExecutionAction.FULL, position_shares=5, risk_target_shares=8, price=10, equity=1000), 3)
        self.assertEqual(transaction_shares(ExecutionAction.FULL, position_shares=8, risk_target_shares=8, price=10, equity=1000), 0)
        with self.assertRaises(ExecutionQuantityError):
            transaction_shares(ExecutionAction.FULL, position_shares=9, risk_target_shares=8, price=10, equity=1000)

    def test_trim_is_partial_delta_and_cannot_alias_exit(self):
        self.assertEqual(transaction_shares(ExecutionAction.TRIM, position_shares=10, risk_target_shares=6, price=10, equity=1000), 4)
        with self.assertRaises(ExecutionQuantityError):
            transaction_shares(ExecutionAction.TRIM, position_shares=10, risk_target_shares=0, price=10, equity=1000)
        with self.assertRaises(ExecutionQuantityError):
            transaction_shares(ExecutionAction.TRIM, position_shares=10, risk_target_shares=10, price=10, equity=1000)

    def test_exit_is_full_existing_position(self):
        self.assertEqual(transaction_shares(ExecutionAction.EXIT, position_shares=10, risk_target_shares=0, price=10, equity=1000), 10)


if __name__ == "__main__":
    unittest.main()

