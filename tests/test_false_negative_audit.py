import json
import sqlite3
import unittest

from stock_agent.false_negative_audit import audit_false_negatives


class FalseNegativeAuditTests(unittest.TestCase):
    def make_db(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE shadow_decisions (
                decision_id TEXT PRIMARY KEY,
                shadow_run_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                decision_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE shadow_outcomes (
                outcome_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                horizon TEXT NOT NULL,
                as_of TEXT NOT NULL,
                outcome_json TEXT NOT NULL,
                outcome_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(decision_id,horizon,as_of)
            );
            """
        )
        return connection

    def add_decision(self, connection, decision_id, ticker, *, rejected=False, not_evaluated=False, stage="CATALYST_GATE", sector="TECH"):
        decision = {
            "decision_id": decision_id,
            "ticker": ticker,
            "decision": "REJECTED" if rejected else "NOT_EVALUATED_RESEARCH_PROVIDER",
            "rejected": rejected,
            "not_evaluated": not_evaluated,
            "rejected_stage": stage if rejected else None,
            "not_evaluated_stage": stage if not_evaluated else None,
            "sector": sector,
        }
        connection.execute(
            "INSERT INTO shadow_decisions VALUES(?,?,?,?,?,?)",
            (decision_id, "S1", ticker, json.dumps(decision), "h", "2026-09-01T00:00:00Z"),
        )

    def add_outcome(self, connection, decision_id, horizon, mfe, mae=-0.1, forward_return=0.0):
        payload = {"mfe": mfe, "mae": mae, "forward_return": forward_return}
        connection.execute(
            "INSERT INTO shadow_outcomes VALUES(?,?,?,?,?,?,?)",
            (f"O-{decision_id}-{horizon}", decision_id, horizon, "2026-10-30T00:00:00Z", json.dumps(payload), "h", "2026-10-30T00:00:00Z"),
        )

    def test_repeated_gate_that_kills_two_30pct_winners_is_flagged(self):
        connection = self.make_db()
        self.add_decision(connection, "D1", "AAA", rejected=True, stage="CATALYST_GATE")
        self.add_decision(connection, "D2", "BBB", rejected=True, stage="CATALYST_GATE")
        self.add_outcome(connection, "D1", "20D", 0.35, forward_return=0.31)
        self.add_outcome(connection, "D2", "40D", 0.62, forward_return=0.50)
        result = audit_false_negatives(connection)
        self.assertEqual(result["winner_30_count"], 2)
        self.assertEqual(result["winner_50_count"], 1)
        self.assertEqual(result["repeated_winner_killers"][0]["reason"], "CATALYST_GATE")
        self.assertEqual(result["repeated_winner_killers"][0]["winner_30_count"], 2)

    def test_provider_failure_winner_is_separate_from_investment_rejection(self):
        connection = self.make_db()
        self.add_decision(connection, "D3", "CCC", not_evaluated=True, stage="RESEARCH_PROVIDER_FAILURE")
        self.add_outcome(connection, "D3", "10D", 0.55, forward_return=0.42)
        result = audit_false_negatives(connection)
        self.assertEqual(len(result["operational_failure_winners"]), 1)
        incident = result["operational_failure_winners"][0]
        self.assertEqual(incident["ticker"], "CCC")
        self.assertTrue(incident["operational_failure"])

    def test_missing_outcomes_are_reported_not_treated_as_zero_return(self):
        connection = self.make_db()
        self.add_decision(connection, "D4", "DDD", rejected=True, stage="EXPECTATION_GAP_GATE")
        result = audit_false_negatives(connection)
        self.assertEqual(result["decisions_with_outcomes"], 0)
        self.assertEqual(result["decisions_missing_outcomes"], 1)
        self.assertEqual(result["winner_30_count"], 0)


if __name__ == "__main__":
    unittest.main()
