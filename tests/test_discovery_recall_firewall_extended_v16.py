from __future__ import annotations

import json
import subprocess
import sys
import unittest


class DiscoveryRecallFirewallExtendedV16Tests(unittest.TestCase):
    def test_all_secondary_and_failure_guard_metadata_is_scrubbed(self):
        code = r'''
import json
from stock_agent.discovery_recall_firewall_v15 import install_discovery_recall_firewall_v15
from stock_agent import v8_primary
install_discovery_recall_firewall_v15()
value = {
  "ticker":"FW16",
  "facts":{"revenue":100},
  "research_value":"HIGH",
  "signal_strength":"MODERATE",
  "scanner_id":"07",
  "secondary_status":"OPEN",
  "secondary_queue":{"x":1},
  "near_miss":True,
  "rejection_sentinel":{"finding":"OK"},
  "recommended_discovery_action":"DEEP_DIVE_SECONDARY",
  "verification_path":"SEC+IR",
  "recheck_trigger":"new evidence",
  "expected_resolution":"this cycle",
  "expiry":"next event",
  "secondary_is_pre_a":False,
  "research_value_is_research_grade":False,
  "fatal_fail":False,
  "research_route_allowed":True,
  "why_not_deep_dive":"unknown catalyst",
  "queue_status":"OPEN"
}
print(json.dumps(v8_primary.v8_blind_packet(value), sort_keys=True))
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        blinded = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(blinded, {"ticker": "FW16", "facts": {"revenue": 100}})


if __name__ == "__main__":
    unittest.main()
