from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from stock_agent.database import Database
from stock_agent.delta import build_fresh_delta
from stock_agent.dispatcher import RequestDispatcher
from stock_agent.knowledge import ObsidianKnowledgeManager, UnsafeVaultPathError
from stock_agent.paper import PaperPortfolio
from stock_agent.schemas import (CompanyState, EvidenceItem, InvestmentDecision,
                                 MarketSnapshot, PositionSize, TradePlan, now_iso)


def market(price=10):
    return MarketSnapshot("IONQ", now_iso(), price, 1, 2, 3, 1000, 900, 1_000_000_000,
                          9, 8, 1, sector_name="UNKNOWN", is_mock=False)


def plan(price=10):
    return TradePlan(price, price * .9, price, price * .8, price * 1.4, price * 1.8,
                     price * .4, price * .2, 2)


class PaperLifecycleTests(unittest.TestCase):
    def test_buy_then_sell_updates_cash_position_and_realized_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite")); db.init(); db.initialize_paper_account(10_000)
            paper = PaperPortfolio(db)
            buy = InvestmentDecision("IONQ", now_iso(), "BUY", 80, "READY", plan(10), [], [], "BUYRUN")
            buy_size = PositionSize(35, 350, 100, 10, "CAP")
            with db.connect() as c:
                db._apply_paper_effect(c, paper.plan_effect(buy, buy_size))
            sell = InvestmentDecision("IONQ", now_iso(), "SELL", 75, "NOT_READY", plan(12), [], [], "SELLRUN")
            zero = PositionSize(0, 0, 0, 0, "NOT_APPLICABLE")
            effect = paper.plan_effect(sell, zero)
            with db.connect() as c:
                db._apply_paper_effect(c, effect)
            account = db.paper_account_state()
            self.assertEqual(account["open_positions"], 0)
            self.assertEqual(account["cash"], 10_070)
            with db.connect() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM paper_transactions").fetchone()[0], 2)
                self.assertEqual(c.execute("SELECT realized_pnl FROM paper_accounts").fetchone()[0], 70)

    def test_wait_creates_prediction_without_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite")); db.init(); db.initialize_paper_account(10_000)
            decision = InvestmentDecision("IONQ", now_iso(), "WAIT", 60, "NOT_READY", plan(), [], [], "R")
            effect = PaperPortfolio(db).plan_effect(decision, PositionSize(0, 0, 0, 0, "NONE"))
            with db.connect() as c:
                db._apply_paper_effect(c, effect)
            with db.connect() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM paper_predictions").fetchone()[0], 1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0], 0)

    def test_conditional_order_fills_only_after_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite")); db.init(); db.initialize_paper_account(10_000)
            paper = PaperPortfolio(db)
            decision = InvestmentDecision("IONQ", now_iso(), "CONDITIONAL_BUY", 70, "READY",
                                          plan(10), [], [], "R")
            effect = paper.plan_effect(decision, PositionSize(35, 350, 100, 10, "CAP"))
            with db.connect() as c:
                db._apply_paper_effect(c, effect)
            self.assertEqual(paper.evaluate_pending_orders("IONQ", 11), [])
            self.assertEqual(len(paper.evaluate_pending_orders(
                "IONQ", 9, revalidate=lambda _order, _price: (True, "OK"))), 1)
            self.assertEqual(db.paper_account_state()["open_positions"], 1)


class DeltaAndKnowledgeTests(unittest.TestCase):
    def test_first_touch_full_then_prior_run_delta(self):
        evidence = [EvidenceItem("E2", "IONQ", "SEC", "8-K", "2026-08-10", "x", "u", "B", "C", "s")]
        full = build_fresh_delta(None, market(), evidence, {"regime": "RISK_ON"})
        self.assertEqual(full["research_mode"], "FULL_RESEARCH")
        prior = {"run": {"run_id": "OLD", "finished_at": "2026-08-01"},
                 "certification": {"certification_status": "CERTIFIED"},
                 "decision": {"decision": "WAIT", "confidence": 60},
                 "market": {"current": 8, "return_20d_pct": -1, "relative_volume": 1,
                            "atr_pct": 5, "ma20": 8, "ma50": 7},
                 "manifest": {"evidence_ids": ["E1"]}}
        delta = build_fresh_delta(prior, market(), evidence, {"regime": "RISK_ON"})
        self.assertEqual(delta["research_mode"], "DELTA_RESEARCH")
        self.assertEqual(delta["new_evidence_ids"], ["E2"])
        self.assertEqual(delta["prior_decision"], "WAIT")

    def test_uncertified_prior_run_is_diagnostic_not_delta_baseline(self):
        prior = {"run": {"run_id": "BAD", "finished_at": "2026-08-01"},
                 "certification": {"certification_status": "BLOCKED_EVIDENCE"},
                 "decision": {"decision": "WAIT"}, "market": {}, "manifest": {}}
        delta = build_fresh_delta(prior, market(), [], {"regime": "RISK_ON"})
        self.assertEqual(delta["research_mode"], "FULL_RESEARCH")
        self.assertEqual(delta["diagnostic_prior_run_id"], "BAD")
        self.assertEqual(delta["prior_run_id"], "")

    def test_obsidian_managed_projection_preserves_user_notes_and_ignores_reports_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ObsidianKnowledgeManager(tmp)
            company = manager.company_dir("IONQ"); company.mkdir(parents=True)
            core = company / "Core.md"; core.write_text("# User Core\n\n사용자 메모 보존", encoding="utf-8")
            obsidian = Path(tmp) / ".obsidian"; obsidian.mkdir(); sentinel = obsidian / "config";
            sentinel.write_text("unchanged", encoding="utf-8")
            report = Path(tmp) / "source.md"; report.write_text("FULL REPORT SECRET_REPORT", encoding="utf-8")
            state = CompanyState("IONQ", "2026-08-10", 0, 0, 0, False, 0, [], [],
                                 sector="UNKNOWN", sic="7372")
            evidence = [EvidenceItem("E1", "IONQ", "SEC", "8-K", "2026-08-01", "x", "u",
                                     "B", "C", "historical event")]
            research = SimpleNamespace(suggested_decision="WAIT")
            decision = SimpleNamespace(timestamp=now_iso(), decision="WAIT", confidence=60)
            debate = SimpleNamespace(status="DEADLOCK")
            manager.sync_run("IONQ", "R", state, evidence, research, decision, debate, report,
                             certification_status="CERTIFIED")
            self.assertIn("사용자 메모 보존", core.read_text(encoding="utf-8"))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            context = manager.load_context("IONQ")
            self.assertNotIn("SECRET_REPORT", str(context))
            self.assertNotIn("$", core.read_text(encoding="utf-8"))

    def test_obsidian_custom_dirs_and_verified_evidence_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ObsidianKnowledgeManager(tmp, companies_dir="Companies",
                reports_dir="Reports", decision_log_dir="Decisions")
            report = Path(tmp) / "source.md"; report.write_text("report", encoding="utf-8")
            state = CompanyState("IONQ", "2026-08-10", 0, 0, 0, False, 0, [], [])
            verified = EvidenceItem("E_OK", "IONQ", "SEC", "8-K", "2026-08-01",
                                    "x", "u", "B", "C", "verified")
            rejected = EvidenceItem("E_NO", "IONQ", "SEC", "8-K", "2026-08-01",
                                    "x", "u", "UNCLASSIFIED", "C", "unverified")
            research = SimpleNamespace(suggested_decision="BUY")
            decision = SimpleNamespace(timestamp=now_iso(), decision="WAIT", confidence=60)
            debate = SimpleNamespace(status="DEADLOCK")
            manager.sync_run("IONQ", "R", state, [verified, rejected], research,
                             decision, debate, report, certification_status="CERTIFIED")
            index = (Path(tmp) / "Companies/IONQ/Evidence_Index.md").read_text(encoding="utf-8")
            self.assertIn("E_OK", index)
            self.assertNotIn("E_NO", index)
            self.assertTrue((Path(tmp) / "Reports/IONQ_R.md").is_file())
            self.assertTrue((Path(tmp) / "Decisions/IONQ.md").is_file())

    def test_obsidian_blocks_dot_directory_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ObsidianKnowledgeManager(tmp)
            with self.assertRaises(UnsafeVaultPathError):
                manager._inside_root(Path(tmp) / ".obsidian/plugins/x")
            with self.assertRaises(UnsafeVaultPathError):
                ObsidianKnowledgeManager(tmp, companies_dir="../outside")

    def test_disabled_obsidian_does_not_create_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "disabled"
            manager = ObsidianKnowledgeManager(str(root), enabled=False)
            self.assertEqual(manager.load_context("IONQ"), {})
            self.assertFalse(root.exists())


class CompareMatrixTests(unittest.TestCase):
    @staticmethod
    def item(ticker, confidence, score):
        research = SimpleNamespace(signal_strength=score, catalyst_quality=score,
            expectation_gap=score, surge_elasticity=score, entry_readiness=score,
            capital_structure_risk=100-score)
        decision = SimpleNamespace(ticker=ticker, decision="BUY", confidence=confidence)
        risk = SimpleNamespace(reward_risk_ratio=2.5, hard_filter_pass=True)
        return {"research": research, "decision": decision, "risk": risk,
                "market": SimpleNamespace(data_quality="OK")}

    def test_compare_is_not_confidence_only(self):
        high_conf_weak = RequestDispatcher._comparison_row(self.item("A", 95, 20))
        lower_conf_strong = RequestDispatcher._comparison_row(self.item("B", 70, 90))
        self.assertGreater(lower_conf_strong["composite_score"], high_conf_weak["composite_score"])


if __name__ == "__main__":
    unittest.main()
