from __future__ import annotations

import unittest

from stock_agent.discovery_recall_stop_bridge_v15 import _high_value_near_miss_count


class DummyAgent:
    def __init__(self, state):
        self._discovery_recall_state = state


class DiscoveryRecallStopNearMissV16Tests(unittest.TestCase):
    def test_nonfatal_high_value_near_miss_is_open_search_debt(self):
        agent = DummyAgent({"run-1": {"near_miss": [
            {"security_id": "A", "research_value": "HIGH", "fatal_fail": False, "research_route_allowed": True},
            {"security_id": "B", "research_value": "MEDIUM", "fatal_fail": False, "research_route_allowed": True},
        ]}})
        self.assertEqual(_high_value_near_miss_count(agent, "run-1"), 1)

    def test_archived_structural_fail_is_not_open_search_debt(self):
        agent = DummyAgent({"run-1": {"near_miss": [
            {"security_id": "TOXIC", "research_value": "HIGH", "fatal_fail": True, "research_route_allowed": False},
        ]}})
        self.assertEqual(_high_value_near_miss_count(agent, "run-1"), 0)


if __name__ == "__main__":
    unittest.main()
