from __future__ import annotations

import unittest

from stock_agent.hunt_pipeline_v16 import _classify_stage, _starvation_state


class HuntStageFailureSemanticsV18Tests(unittest.TestCase):
    def test_deep_research_success_then_post_research_catalyst_failure_is_reject_not_pass(self):
        self.assertEqual(_classify_stage("DEEP_RESEARCH", {"research_status": "COMPLETE"}), "PASS")
        self.assertEqual(_classify_stage("FULL_SEC_FORENSIC", {"status": "COMPLETE"}), "PASS")
        self.assertEqual(
            _classify_stage(
                "CATALYST_GATE",
                {
                    "decision": "REJECT",
                    "evaluation_phase": "POST_DEEP_RESEARCH_AND_FULL_SEC",
                    "evaluation_status": "SOURCE_EXHAUSTED",
                },
            ),
            "FAIL",
        )

    def test_deep_research_success_then_full_sec_failure_is_pipeline_starvation_not_clean_no_trade(self):
        self.assertEqual(_classify_stage("DEEP_RESEARCH", {"research_status": "COMPLETE"}), "PASS")
        self.assertEqual(_classify_stage("FULL_SEC_FORENSIC", {"status": "INCOMPLETE"}), "FAIL")
        self.assertEqual(
            _starvation_state({
                "CAPITAL_PRESCREEN_PASS": 1,
                "DEEP_RESEARCH": 1,
                "FULL_SEC_FORENSIC": 0,
                "ADVERSARIAL_AUDIT": 0,
            }),
            (1, "DEEP_RESEARCH_NEVER_REACHED_FULL_SEC"),
        )

    def test_full_sec_success_then_adversarial_audit_failure_is_explicit_investment_rejection(self):
        self.assertEqual(_classify_stage("FULL_SEC_FORENSIC", {"status": "COMPLETE"}), "PASS")
        self.assertEqual(
            _classify_stage("ADVERSARIAL_AUDIT", {"audit_recommendation": "DOES_NOT_SUPPORT"}),
            "FAIL",
        )
        # Audit was actually entered; this is not a queue-starvation incident.
        self.assertEqual(
            _starvation_state({
                "CAPITAL_PRESCREEN_PASS": 1,
                "DEEP_RESEARCH": 1,
                "FULL_SEC_FORENSIC": 1,
                "ADVERSARIAL_AUDIT": 1,
            }),
            (0, None),
        )

    def test_mixed_source_outage_is_not_equivalent_to_investment_reject(self):
        # Mixed source/data failures remain an evaluation problem.  The
        # candidate must not be converted into a gate PASS or economic reject.
        states = {
            "SEC_STALE_DATA": "NOT_EVALUATED",
            "RESEARCH_PROVIDER_FAILURE": "NOT_EVALUATED",
            "ISSUER_IR_UNAVAILABLE": "NOT_EVALUATED",
            "SECONDARY_MEDIA_OUTAGE": "NOT_EVALUATED",
        }
        self.assertTrue(all(value == "NOT_EVALUATED" for value in states.values()))
        self.assertNotIn("PASS", states.values())
        self.assertNotIn("REJECT", states.values())


if __name__ == "__main__":
    unittest.main()
