from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stock_agent.database import Database
from stock_agent.command_parser import CommandInterpreter
from stock_agent.paper import PaperPortfolio
from stock_agent.paper_execution import CanonicalPaperValidator
from stock_agent.position_sizing import PositionSizingEngine, PositionSizingError
from stock_agent.guard import FinalGuard
from stock_agent.schemas import InvestmentDecision, PositionSize, RiskResult, TradePlan, now_iso


def plan(entry: float = 10.0, stop: float = 8.0) -> TradePlan:
    return TradePlan(entry, entry * 0.9, entry, stop, entry * 1.4, entry * 1.8,
                     entry * 0.4, entry - stop, 2.0)


class PaperPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp.name) / "db.sqlite"))
        self.db.init()
        self.db.initialize_paper_account(10_000)
        self.paper = PaperPortfolio(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _conditional(self) -> None:
        decision = InvestmentDecision("INOD", now_iso(), "CONDITIONAL_BUY", 80, "READY",
                                      plan(), [], [], "RUN-COND")
        effect = self.paper.plan_effect(
            decision, PositionSize(100, 1000, 200, 10, "CAP"), sector="Technology")
        with self.db.connect() as connection:
            self.assertTrue(self.db._apply_paper_effect(connection, effect))

    def test_conditional_trigger_cannot_fill_without_canonical_revalidation(self):
        self._conditional()
        self.assertEqual(self.paper.evaluate_pending_orders("INOD", 9), [])
        with self.db.connect() as connection:
            status = connection.execute(
                "SELECT status FROM paper_orders WHERE ticker='INOD'"
            ).fetchone()[0]
        self.assertEqual(status, "REVALIDATING")
        self.assertEqual(self.db.paper_account_state()["open_positions"], 0)

    def test_failed_revalidation_invalidates_and_releases_reservation(self):
        self._conditional()
        filled = self.paper.evaluate_pending_orders(
            "INOD", 9, revalidate=lambda _order, _price: (False, "STALE_PRICE"))
        self.assertEqual(filled, [])
        with self.db.connect() as connection:
            order = connection.execute("SELECT status FROM paper_orders").fetchone()[0]
            reservation = connection.execute("SELECT status FROM paper_reservations").fetchone()[0]
        self.assertEqual(order, "INVALIDATED")
        self.assertEqual(reservation, "RELEASED")
        self.assertEqual(self.db.paper_account_state()["reserved_cash"], 0)
        self.assertTrue(self.db.financial_invariants()["reservation_matches"])

    def test_successful_revalidation_fills_exactly_once(self):
        self._conditional()
        validator = CanonicalPaperValidator(self.db)
        outcome = lambda order, price: validator.validate_conditional(
            order, price, certification_status="CERTIFIED", price_status="FRESH")
        self.assertEqual(self.paper.evaluate_pending_orders("INOD", 9, revalidate=outcome),
                         ["ORDER_RUN-COND"])
        self.assertEqual(self.paper.evaluate_pending_orders("INOD", 9, revalidate=outcome), [])
        with self.db.connect() as connection:
            tx_count = connection.execute(
                "SELECT COUNT(*) FROM paper_transactions WHERE ticker='INOD'"
            ).fetchone()[0]
        self.assertEqual(tx_count, 1)
        self.assertTrue(all(value for key, value in self.db.financial_invariants().items()
                            if key.endswith("_matches")))

    def test_hold_is_not_valid_without_existing_position(self):
        result = CanonicalPaperValidator(self.db).canonicalize_action(
            "HOLD", "INOD", account_id="PAPER_DEFAULT")
        self.assertEqual(result.action, "WAIT")
        self.assertFalse(result.valid)
        self.assertIn("HOLD_REQUIRES_OPEN_POSITION", result.reason_codes)

    def test_explicit_paper_command_is_distinct_from_analysis(self):
        paper = CommandInterpreter().parse("INOD PAPER 매수", message_id="M1", user_id="U1")
        analyze = CommandInterpreter().parse("INOD 분석 최대", message_id="M2", user_id="U1")
        self.assertEqual(paper.intent, "PAPER_BUY")
        self.assertTrue(paper.paper_action_enabled)
        self.assertEqual(analyze.intent, "ANALYZE")
        self.assertFalse(analyze.paper_action_enabled)

    def test_korean_company_alias_resolves_duol(self):
        request = CommandInterpreter().parse("듀오링고 분석 최대")
        self.assertEqual(request.tickers, ["DUOL"])


class RiskMetricTests(unittest.TestCase):
    def test_sizing_records_distinct_risk_metrics_and_policy_version(self):
        size = PositionSizingEngine().calculate_for_account(plan(), {
            "account_id": "PAPER_DEFAULT", "equity": 10_000, "available_cash": 10_000,
            "cash": 10_000, "reserved_cash": 0, "current_exposure": 0,
            "sector_exposure": {}, "risk_budget": 75, "risk_budget_used": 0,
            "pending_committed_risk": 0,
        }, sector="Technology")
        self.assertEqual(size.initial_capital_at_risk_usd,
                         round((10 - 8) * size.quantity, 2))
        self.assertEqual(size.current_mark_to_stop_risk_usd, 0)
        self.assertEqual(size.pending_committed_risk_usd, 0)
        self.assertEqual(size.risk_rule_version, "portfolio_heat_v1")

    def test_entry_at_or_below_invalidation_is_invalid(self):
        with self.assertRaises(PositionSizingError):
            PositionSizingEngine().calculate(plan(10, 10), 10_000, 10_000)

    def test_final_guard_converts_hold_without_position_to_wait(self):
        risk = RiskResult("INOD", True, [], [], plan(), "BUY")
        result = FinalGuard.validate_final(
            {"decision": "HOLD"}, risk, True, True, has_open_position=False)
        self.assertEqual(result["final_decision"], "WAIT")
        self.assertIn("HOLD_REQUIRES_OPEN_POSITION", result["errors"])


if __name__ == "__main__":
    unittest.main()
