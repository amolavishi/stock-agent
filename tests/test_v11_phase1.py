from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_agent.claim_validation import validate_claim_evidence
from stock_agent.database import Database
from stock_agent.hermes import HermesResponse
from stock_agent.hermes_agents import HermesChairmanAgent
from stock_agent.paper import PaperPortfolio
from stock_agent.position_sizing import PositionSizingEngine
from stock_agent.schemas import (CompanyState, EvidenceItem, InvestmentDecision,
                                 PositionSize, TradePlan, now_iso)
from stock_agent.validation import AnalysisIncompleteError


class FakeChairmanAdapter:
    def __init__(self):
        self.calls = 0

    def invoke_json(self, prompt, role):
        self.calls += 1
        if self.calls == 1:
            return HermesResponse({"decision": "BUY", "confidence": 101,
                                   "rationale": [], "risk_acknowledgements": []},
                                  "fake", "fake")
        return HermesResponse({"decision": "WAIT", "confidence": 70,
                               "rationale": ["Risk WAIT 준수"],
                               "risk_acknowledgements": ["추격 위험"]}, "fake", "fake")


class RiskStub:
    hard_filter_pass = True
    risk_decision = "WAIT"

    def __init__(self):
        self.__dict__.update(hard_filter_pass=True, risk_decision="WAIT", warnings=[], failures=[])


class Phase1DatabasePaperTests(unittest.TestCase):
    def test_additive_migration_preserves_legacy_rows_and_enables_pragmas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            legacy = sqlite3.connect(path)
            try:
                c = legacy
                c.execute("CREATE TABLE analysis_runs(run_id TEXT PRIMARY KEY,ticker TEXT,requested_at TEXT,status TEXT,mode TEXT,final_decision TEXT,final_confidence INTEGER,error_message TEXT)")
                c.execute("INSERT INTO analysis_runs VALUES('LEGACY','IONQ','t','SUCCESS','PAPER','WAIT',50,NULL)")
                c.commit()
            finally:
                legacy.close()
            db = Database(str(path))
            db.init()
            with db.connect() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM analysis_runs WHERE run_id='LEGACY'").fetchone()[0], 1)
                self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertIsNotNone(c.execute("SELECT 1 FROM schema_migrations WHERE version=12").fetchone())
                columns = {row[1] for row in c.execute("PRAGMA table_info(analysis_runs)")}
                self.assertIn("delivered_at", columns)

    def test_paper_account_is_initialized_once_and_sizing_uses_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite"))
            db.init()
            db.initialize_paper_account(50_000)
            db.initialize_paper_account(99_000)
            account = db.paper_account_state()
            self.assertEqual(account["cash"], 50_000)
            plan = TradePlan(100, 95, 100, 90, 120, 140, 20, 10, 2)
            size = PositionSizingEngine(10, 1, 20, 15).calculate_for_account(plan, account, "UNKNOWN")
            self.assertLessEqual(size.notional_usd, 5_000)
            self.assertEqual(size.available_cash_usd, 50_000)

    def test_conditional_buy_creates_order_not_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite"))
            db.init()
            db.initialize_paper_account(50_000)
            plan = TradePlan(10, 9, 10, 8, 14, 18, 4, 2, 2)
            decision = InvestmentDecision("IONQ", now_iso(), "CONDITIONAL_BUY", 70,
                "READY", plan, [], [], "RUN1")
            size = PositionSize(100, 1000, 200, 2, "LOSS_BUDGET")
            effect = PaperPortfolio(db).plan_effect(decision, size, "UNKNOWN", "1M")
            with db.connect() as c:
                db._apply_paper_effect(c, effect)
            with db.connect() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM paper_orders WHERE status='PENDING'").fetchone()[0], 1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0], 0)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM paper_predictions").fetchone()[0], 1)

    def test_invalid_paper_effect_rolls_back_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite"))
            db.init()
            db.initialize_paper_account(100)
            effect = {
                "account_id": "PAPER_DEFAULT", "run_id": "R", "ticker": "IONQ",
                "timestamp": now_iso(), "action": "BUY", "quantity": 100, "price": 10,
                "notional_usd": 1000, "sector": "UNKNOWN",
                "prediction": {"prediction_id": "P", "run_id": "R", "ticker": "IONQ",
                               "decision": "BUY", "confidence": 80,
                               "reference_price": 10, "horizon": "1M"},
            }
            with self.assertRaises(ValueError), db.connect() as c:
                db._apply_paper_effect(c, effect)
            with db.connect() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM paper_predictions").fetchone()[0], 0)


class Phase1ValidationTests(unittest.TestCase):
    def test_claim_minimum_is_enforced(self):
        item = EvidenceItem("E1", "IONQ", "SEC", "8-K", now_iso(), "x", "u", "B", "C", "s")
        with self.assertRaises(AnalysisIncompleteError):
            validate_claim_evidence([{"claim": "x", "evidence_ids": ["E1"]}], [item], min_claims=3)

    def test_chairman_invalid_response_gets_one_repair_and_obeys_risk(self):
        adapter = FakeChairmanAdapter()
        agent = HermesChairmanAgent(adapter)
        research = type("Research", (), {"__dict__": {}, "confidence": 70})()
        critic = type("Critic", (), {"__dict__": {}, "confidence": 70})()
        result = agent.run(research, critic, RiskStub())
        self.assertEqual(adapter.calls, 2)
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["confidence"], 70)


if __name__ == "__main__":
    unittest.main()
