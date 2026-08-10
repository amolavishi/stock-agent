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

    def test_same_decision_ready_false_never_consensus(self):
        result = DebateEngine().run(
            "R", request(2), {"ticker": "INOD"},
            lambda *_: agent("research", "WAIT", ready=False),
            lambda *_: agent("critic", "WAIT", ready=False),
            lambda *_: None,
        )
        self.assertEqual(result[2].status, "DEADLOCK")
        self.assertFalse(result[3]["consensus"])
        self.assertIn("AGENTS_NOT_READY", result[3]["reasons"])

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

    def test_critical_insufficient_data_blocks_consensus(self):
        issue = [{
            "topic": "Q2 revenue evidence is incomplete",
            "severity": "CRITICAL",
            "status": "INSUFFICIENT_DATA",
            "materiality": "MATERIAL",
            "research_position": "cannot verify",
            "critic_position": "cannot verify",
        }]
        result = DebateEngine().run(
            "R", request(2), {"ticker": "INOD"},
            lambda *_: agent("research", "WAIT", issues=issue, ready=True),
            lambda *_: agent("critic", "WAIT", ready=True),
            lambda *_: None,
        )
        self.assertEqual(result[2].status, "DEADLOCK")
        self.assertEqual(result[2].critical_open_issue_count, 1)
        self.assertIn("CRITICAL_ISSUE_UNRESOLVED", result[3]["reasons"])

    def test_unresolved_must_answer_prevents_consensus(self):
        result = DebateEngine().run(
            "R", request(2), {"ticker": "INOD"},
            lambda *_: agent("research", "WAIT"),
            lambda *_: agent("critic", "WAIT"), lambda *_: None,
            must_answer_check=lambda: 1,
        )
        self.assertEqual(result[2].status, "DEADLOCK")
        self.assertEqual(result[2].unresolved_must_answer_count, 1)

    def test_refresh_is_reviewed_then_consensus_can_be_reached(self):
        calls = {"refresh": 0}

        def research_call(round_no, *_):
            return agent("research", "WAIT", ready=True)

        def critic_call(round_no, *_):
            requests = ([{"question": "verify Q2", "must_answer": False}]
                        if round_no == 1 else [])
            return agent("critic", "WAIT", requests=requests, ready=True)

        def refresh_call(*_):
            calls["refresh"] += 1
            return {"ticker": "INOD", "evidence_index": [{"evidence_id": "NEW"}]}

        result = DebateEngine().run(
            "R", request(3), {"ticker": "INOD", "evidence_index": []},
            research_call, critic_call, refresh_call,
        )
        self.assertEqual(calls["refresh"], 1)
        self.assertEqual(result[2].status, "FINAL_CONSENSUS")
        self.assertTrue(result[2].final_consensus)
        self.assertFalse(result[2].material_evidence_review_required)

    def test_split_brain_decision_fields_block_consensus(self):
        def research_call(*_):
            return SimpleNamespace(
                current_decision="WAIT", suggested_decision="BUY", confidence=60,
                issue_updates=[], accepted_points=[], consensus_ready=True,
                evidence_requests=[], critical_flaws=[],
            )

        result = DebateEngine().run(
            "R", request(2), {"ticker": "INOD"}, research_call,
            lambda *_: agent("critic", "WAIT", ready=True), lambda *_: None,
        )
        self.assertEqual(result[2].status, "DEADLOCK")
        self.assertIn("RESEARCH_DECISION_FIELDS_CONFLICT", result[3]["reasons"])


if __name__ == "__main__":
    unittest.main()
