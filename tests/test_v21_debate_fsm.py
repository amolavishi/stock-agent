import unittest
from types import SimpleNamespace

from stock_agent.debate import DebateEngine
from stock_agent.schemas import UserRequest, now_iso


def request(maximum=8):
    return UserRequest(
        "Q", "M", "U", now_iso(), "INOD 분석", "ANALYZE", ["INOD"],
        analysis_intensity="NORMAL", min_debate_rounds=2, max_debate_rounds=maximum,
        intensity_explicit=True,
    )


def agent(role, decision, issues=None, requests=None, ready=True):
    values = dict(
        current_decision=decision, confidence=60, issue_updates=issues or [],
        accepted_points=[], consensus_ready=ready, evidence_requests=requests or [],
        critical_flaws=[],
    )
    values["suggested_decision" if role == "research" else "critic_decision"] = decision
    return SimpleNamespace(**values)


class DebateFsmTests(unittest.TestCase):
    def test_no_progress_stops_after_two_rounds(self):
        result = DebateEngine().run(
            "R", request(), {"ticker": "INOD"},
            lambda *_: agent("research", "BUY"),
            lambda *_: agent("critic", "WAIT"), lambda *_: None,
        )
        self.assertEqual(result[2].status, "DEADLOCK")
        self.assertEqual(result[2].round_no, 2)
        self.assertEqual(result[2].deadlock_reason, "NO_MATERIAL_PROGRESS")

    def test_semantic_issue_variants_merge(self):
        variants = [
            "ATM remaining capacity is unknown",
            "$300M at-the-market utilization has not been verified",
        ]

        def research_call(round_no, *_):
            return agent("research", "WAIT", [{
                "topic": variants[round_no - 1], "severity": "CRITICAL", "status": "OPEN"
            }])

        result = DebateEngine().run(
            "R", request(2), {"ticker": "INOD"}, research_call,
            lambda *_: agent("critic", "WAIT"), lambda *_: None,
        )
        self.assertEqual(len(result[2].issue_ledger), 1)
        issue = result[2].issue_ledger[0]
        self.assertEqual(issue.semantic_issue_key, "CAPITAL_STRUCTURE_ATM")
        self.assertTrue(issue.issue_instance_id.startswith("ISSUE_INSTANCE_"))

    def test_unresolved_must_answer_prevents_consensus(self):
        result = DebateEngine().run(
            "R", request(2), {"ticker": "INOD"},
            lambda *_: agent("research", "WAIT"),
            lambda *_: agent("critic", "WAIT"), lambda *_: None,
            must_answer_check=lambda: 1,
        )
        self.assertEqual(result[2].status, "DEADLOCK")
        self.assertEqual(result[2].unresolved_must_answer_count, 1)


if __name__ == "__main__":
    unittest.main()
