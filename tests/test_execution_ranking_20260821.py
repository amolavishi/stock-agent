from __future__ import annotations

import unittest

from stock_agent.runtime import ProductionStockAgent


class ExecutionRankingTests(unittest.TestCase):
    def test_caller_economic_assessments_cannot_reorder_python_reverse_valuation(self):
        candidates = [
            {"security_id": "LOW", "reverse_valuation": {"benchmark_implied_upside_pct": 0.35}},
            {"security_id": "HIGH", "reverse_valuation": {"benchmark_implied_upside_pct": 0.80}},
        ]
        caller = {
            "economic_assessments": {
                "LOW": {"bull_value": 10000, "base_value": 10000, "bear_value": 10000},
                "HIGH": {"bull_value": 1, "base_value": 1, "bear_value": 1},
            }
        }
        ranked = ProductionStockAgent._rank_execution_candidates(candidates, caller)
        self.assertEqual([row["security_id"] for row in ranked], ["HIGH", "LOW"])

    def test_tie_break_is_deterministic_security_id(self):
        candidates = [
            {"security_id": "BBB", "reverse_valuation": {"benchmark_implied_upside_pct": 0.5}},
            {"security_id": "AAA", "reverse_valuation": {"benchmark_implied_upside_pct": 0.5}},
        ]
        ranked = ProductionStockAgent._rank_execution_candidates(candidates, {})
        self.assertEqual([row["security_id"] for row in ranked], ["AAA", "BBB"])

    def test_missing_reverse_valuation_fails_closed(self):
        with self.assertRaises(Exception):
            ProductionStockAgent._rank_execution_candidates([{"security_id": "SEC1"}], {})


if __name__ == "__main__":
    unittest.main()

