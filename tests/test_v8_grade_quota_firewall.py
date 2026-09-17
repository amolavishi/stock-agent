from __future__ import annotations

import unittest

from stock_agent import v8_primary
from stock_agent.v8_grade_quota_firewall import (
    _FORBIDDEN_QUOTA_KEYS,
    build_quota_free_v8_discovery_contract,
    install_v8_grade_quota_firewall,
)


class V8GradeQuotaFirewallTests(unittest.TestCase):
    def setUp(self):
        install_v8_grade_quota_firewall()

    def test_active_discovery_contract_contains_no_grade_supply_target(self):
        packet = build_quota_free_v8_discovery_contract(2)
        self.assertFalse(_FORBIDDEN_QUOTA_KEYS.intersection(packet))
        self.assertTrue(packet["grade_quota_forbidden"])
        self.assertTrue(packet["a_count_is_output_not_target"])
        self.assertTrue(packet["candidate_shortage_may_only_expand_search"])
        self.assertTrue(packet["candidate_shortage_may_never_relax_certification"])

    def test_quota_keys_are_scrubbed_from_blind_packet(self):
        source = {
            "security_id": "ABC",
            "target_verified_a_minus_or_better": 5,
            "nested": {"remaining_a_needed": 3, "fact": "keep"},
        }
        blind = v8_primary.v8_blind_packet(source)
        self.assertEqual(blind, {"security_id": "ABC", "nested": {"fact": "keep"}})


if __name__ == "__main__":
    unittest.main()
