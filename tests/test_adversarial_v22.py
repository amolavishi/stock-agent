from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_agent.claim_validation import validate_claim_evidence
from stock_agent.database import Database
from stock_agent.paper import PaperPortfolio
from stock_agent.paper_execution import CanonicalPaperValidator
from stock_agent.schemas import EvidenceItem, InvestmentDecision, PositionSize, TradePlan, now_iso
from stock_agent.sec import SECCompanyFactsProvider
from stock_agent.validation import AnalysisIncompleteError


def plan(entry: float = 10.0, stop: float = 8.0) -> TradePlan:
    return TradePlan(entry, entry * 0.9, entry, stop, entry * 1.4, entry * 1.8,
                     entry * 0.4, entry - stop, 2.0)


class AdversarialClaimGroundingTests(unittest.TestCase):
    def test_capital_claim_requires_claim_specific_semantics_not_just_same_domain(self):
        evidence = [EvidenceItem(
            "SEC_WARRANT", "INOD", "SEC", "S-3", "2026-08-06", "Warrant offering", "u", "B",
            "CAPITAL", "The company may offer warrants under this offering.",
            normalized_fact="The company may offer warrants under this offering.",
        )]
        claim = {
            "claim": "The company has an active $300 million ATM program.",
            "materiality": "MATERIAL",
            "domain": "CAPITAL_STRUCTURE",
            "claim_type": "CAPITAL",
            "minimum_evidence_grade": "B",
            "evidence_ids": ["SEC_WARRANT"],
        }
        with self.assertRaises(AnalysisIncompleteError):
            validate_claim_evidence([claim], evidence, min_claims=1)


class AdversarialFinancialBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp.name) / "db.sqlite"))
        self.db.init()
        self.db.initialize_paper_account(10_000)
        self.paper = PaperPortfolio(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _decision(self, ticker: str, run_id: str, action: str = "BUY") -> InvestmentDecision:
        return InvestmentDecision(ticker, now_iso(), action, 80, "READY",
                                  plan(), [], [], run_id)

    def test_commit_boundary_rejects_buy_that_exceeds_portfolio_risk_budget(self):
        # Account risk budget is 0.75% of $10,000 = $75. This intent carries $200 entry-to-stop risk.
        effect = self.paper.plan_effect(
            self._decision("INOD", "RUN-OVER-RISK"),
            PositionSize(100, 1000, 75, 10, "TEST"),
            sector="Technology",
        )
        with self.assertRaises(ValueError):
            with self.db.connect() as connection:
                self.db._apply_paper_effect(connection, effect)
        self.assertEqual(self.db.paper_account_state()["open_positions"], 0)

    def test_commit_boundary_rechecks_sector_cap_for_stale_conditional_intents(self):
        # Both intents are planned against the same empty account snapshot. Each $1,500 order is
        # individually below the $2,500 sector cap, but together they exceed it. Commit-time
        # validation must reject the second stale intent rather than trusting plan-time state.
        first = self.paper.plan_effect(
            self._decision("INOD", "RUN-SECTOR-A", "CONDITIONAL_BUY"),
            PositionSize(150, 1500, 75, 15, "TEST"), sector="Technology")
        second = self.paper.plan_effect(
            self._decision("IONQ", "RUN-SECTOR-B", "CONDITIONAL_BUY"),
            PositionSize(150, 1500, 75, 15, "TEST"), sector="Technology")
        self.assertEqual(first["action"], "CONDITIONAL_ORDER")
        self.assertEqual(second["action"], "CONDITIONAL_ORDER")
        with self.db.connect() as connection:
            self.assertTrue(self.db._apply_paper_effect(connection, first))
        with self.assertRaises(ValueError):
            with self.db.connect() as connection:
                self.db._apply_paper_effect(connection, second)
        state = self.db.paper_account_state()
        self.assertLessEqual(state["pending_sector_committed_exposure"]["Technology"], 2500.0)

    def test_conditional_revalidation_rejects_portfolio_risk_overflow(self):
        # Open risk = $70. Pending order adds $10 risk, exceeding the $75 account risk budget.
        open_effect = self.paper.plan_effect(
            self._decision("INOD", "RUN-RISK-OPEN"),
            PositionSize(35, 350, 75, 3.5, "TEST"), sector="Technology")
        with self.db.connect() as connection:
            self.assertTrue(self.db._apply_paper_effect(connection, open_effect))

        pending_effect = self.paper.plan_effect(
            self._decision("IONQ", "RUN-RISK-PENDING", "CONDITIONAL_BUY"),
            PositionSize(5, 50, 75, 0.5, "TEST"), sector="Technology")
        with self.db.connect() as connection:
            self.assertTrue(self.db._apply_paper_effect(connection, pending_effect))
            order = connection.execute(
                "SELECT * FROM paper_orders WHERE order_id='ORDER_RUN-RISK-PENDING'"
            ).fetchone()
        result = CanonicalPaperValidator(self.db).validate_conditional(
            order, 9.0, certification_status="CERTIFIED", price_status="FRESH")
        self.assertFalse(result.valid)
        self.assertIn("PORTFOLIO_RISK_LIMIT", result.reason_codes)


class AdversarialCompanyFactsTests(unittest.TestCase):
    @staticmethod
    def _payload() -> dict:
        return {"facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [{
                        "start": "2026-01-01", "end": "2026-06-30", "val": 300,
                        "form": "10-Q", "filed": "2026-08-06", "fy": 2026,
                        "fp": "Q2", "accn": "YTD-USD",
                    }],
                    "EUR": [{
                        "start": "2026-01-01", "end": "2026-03-31", "val": 100,
                        "form": "10-Q", "filed": "2026-05-01", "fy": 2026,
                        "fp": "Q1", "accn": "Q1-EUR",
                    }],
                }
            }
        }}}

    def test_production_companyfacts_does_not_publish_incomparable_ytd_as_q2(self):
        provider = SECCompanyFactsProvider("Agent test@example.com")
        with patch.object(provider, "_find_cik", return_value="0000000001"), \
                patch.object(provider, "_get_json", return_value=self._payload()):
            result = provider.facts("INOD")
        self.assertIsNone(result["revenue"]["value"])
        self.assertTrue(result["revenue"]["derived"])
        self.assertEqual(result["revenue"]["provenance"]["comparability"], "FAILED")
        self.assertIn("UNIT_MISMATCH", result["revenue"]["provenance"]["rejection_reasons"])


if __name__ == "__main__":
    unittest.main()
