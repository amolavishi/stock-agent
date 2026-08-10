from __future__ import annotations

import unittest
from types import SimpleNamespace

from stock_agent.command_parser import CommandInterpreter
from stock_agent.debate import ConsensusEvaluator, DebateEngine
from stock_agent.schemas import UserRequest, now_iso


def request(intensity="MINIMUM", minimum=2, maximum=3, stress=False):
    return UserRequest("Q", "M", "U", now_iso(), "IONQ 분석", "ANALYZE", ["IONQ"],
        analysis_intensity=intensity, min_debate_rounds=minimum, max_debate_rounds=maximum,
        intensity_explicit=True, consensus_stress_test_required=stress)


def research(decision="WAIT", confidence=70, **kwargs):
    return SimpleNamespace(suggested_decision=decision, current_decision=decision,
        confidence=confidence, issue_updates=kwargs.get("issue_updates", []),
        accepted_points=kwargs.get("accepted_points", ["evidence accepted"]),
        consensus_ready=kwargs.get("consensus_ready", True), evidence_requests=[])


def critic(decision="WAIT", confidence=70, **kwargs):
    return SimpleNamespace(critic_decision=decision, current_decision=decision,
        confidence=confidence, issue_updates=kwargs.get("issue_updates", []),
        accepted_points=kwargs.get("accepted_points", ["evidence accepted"]),
        consensus_ready=kwargs.get("consensus_ready", True), evidence_requests=[],
        critical_flaws=kwargs.get("critical_flaws", []))


class IntensityParserTests(unittest.TestCase):
    def test_missing_intensity_requires_clarification(self):
        value = CommandInterpreter().parse("IONQ 한 달 관점으로 분석해줘")
        self.assertEqual(value.status, "WAITING_CLARIFICATION")
        self.assertIn("analysis_intensity", value.missing_fields)

    def test_followup_maximum_is_applied_without_reasking(self):
        value = CommandInterpreter().parse("최대", prior_text="IONQ 한 달 관점으로 분석해줘")
        self.assertEqual(value.analysis_intensity, "MAXIMUM")
        self.assertEqual((value.min_debate_rounds, value.max_debate_rounds), (5, 10))
        self.assertNotIn("analysis_intensity", value.missing_fields)

    def test_explicit_maximum_in_first_message(self):
        value = CommandInterpreter().parse("IONQ 최대 강도로 분석해줘")
        self.assertTrue(value.intensity_explicit)
        self.assertEqual(value.analysis_intensity, "MAXIMUM")
        self.assertEqual(value.status, "PARSED")

    def test_duol_ticker_and_korean_company_name_are_deterministic(self):
        for text in ("DUOL 최소 강도로 분석해줘", "duol 최소 강도로 분석해줘",
                     "듀오링고 최소 강도로 분석해줘"):
            value = CommandInterpreter().parse(text)
            self.assertEqual(value.tickers, ["DUOL"])
            self.assertEqual(value.parser_type, "LIGHTWEIGHT")
            self.assertEqual(value.status, "PARSED")

    def test_explicit_unknown_uppercase_symbol_does_not_need_llm(self):
        value = CommandInterpreter().parse("PLTR 최소 강도로 분석해줘")
        self.assertEqual(value.tickers, ["PLTR"])
        self.assertEqual(value.parser_type, "LIGHTWEIGHT")


class DebateEngineTests(unittest.TestCase):
    def test_critic_context_contains_current_research_only_once(self):
        seen = []

        def critic_call(round_no, current_research, context, phase):
            seen.append(context)
            return critic()

        DebateEngine().run("R", request(minimum=2, maximum=2), {"ticker": "IONQ"},
            lambda *args: research(), critic_call, lambda *args: None)
        self.assertEqual(seen[0]["current_thesis"], {})
        self.assertEqual(seen[0]["opponent_previous_response"]["current_decision"], "WAIT")
    def test_minimum_rounds_prevent_early_consensus_and_pass_previous_response(self):
        seen = []
        engine = DebateEngine()

        def research_call(round_no, context, phase):
            if round_no == 2:
                seen.append(context["opponent_previous_response"]["current_decision"])
            return research()

        result = engine.run("R", request(minimum=2, maximum=3), {"ticker": "IONQ"},
            research_call, lambda *args: critic(), lambda *args: None)
        self.assertEqual(result[2].round_no, 2)
        self.assertEqual(result[2].status, "FINAL_CONSENSUS")
        self.assertEqual(seen, ["WAIT"])

    def test_critical_open_issue_blocks_same_decision_consensus(self):
        engine = DebateEngine()
        issue = {"issue_id": "I", "topic": "ATM", "severity": "CRITICAL",
                 "status": "OPEN", "research_position": "limited",
                 "critic_position": "material"}
        result = engine.run("R", request(minimum=2, maximum=2), {"ticker": "IONQ"},
            lambda *args: research(issue_updates=[issue]),
            lambda *args: critic(issue_updates=[issue]), lambda *args: None)
        self.assertEqual(result[2].status, "DEADLOCK")
        self.assertEqual(result[2].critical_open_issue_count, 1)

    def test_issue_can_move_open_to_resolved_by_evidence(self):
        engine = DebateEngine()

        def update(round_no):
            return {"issue_id": "I", "topic": "ATM", "severity": "CRITICAL",
                    "status": "OPEN" if round_no == 1 else "RESOLVED",
                    "research_position": "verified", "critic_position": "verified",
                    "resolution_basis": "SEC evidence E1" if round_no == 2 else ""}

        result = engine.run("R", request(minimum=2, maximum=3), {"ticker": "IONQ"},
            lambda round_no, *_: research(issue_updates=[update(round_no)]),
            lambda round_no, *_: critic(issue_updates=[update(round_no)]), lambda *args: None)
        self.assertEqual(result[2].status, "FINAL_CONSENSUS")
        self.assertEqual(result[2].resolved_issue_count, 1)

    def test_disagreement_reaches_normal_deadlock_at_max_round(self):
        result = DebateEngine().run("R", request(minimum=2, maximum=3), {"ticker": "IONQ"},
            lambda *args: research("BUY"), lambda *args: critic("WAIT"), lambda *args: None)
        self.assertEqual(result[2].round_no, 2)
        self.assertEqual(result[2].status, "DEADLOCK")
        self.assertEqual(result[2].deadlock_reason, "NO_MATERIAL_PROGRESS")

    def test_maximum_consensus_runs_separate_stress_round(self):
        phases = []

        def research_call(round_no, context, phase):
            phases.append((round_no, phase))
            return research()

        result = DebateEngine().run("R", request("MAXIMUM", 5, 7, True), {"ticker": "IONQ"},
            research_call, lambda *args: critic(), lambda *args: None)
        self.assertEqual(result[2].round_no, 6)
        self.assertEqual(result[2].status, "FINAL_CONSENSUS")
        self.assertTrue(result[2].stress_test_completed)
        self.assertIn((6, "CONSENSUS_STRESS_TEST"), phases)


if __name__ == "__main__":
    unittest.main()
