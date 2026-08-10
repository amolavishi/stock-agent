import tempfile
import threading
import unittest
from pathlib import Path

from stock_agent.database import Database
from stock_agent.schemas import UserRequest, now_iso


def request(message_id="discord-1"):
    return UserRequest(
        "request-1", message_id, "user", now_iso(), "INOD 분석", "ANALYZE", ["INOD"]
    )


class QueueIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp.name) / "db.sqlite"))
        self.db.init()

    def tearDown(self):
        self.temp.cleanup()

    def test_stable_inbound_identity_returns_same_job(self):
        first = self.db.enqueue_job(request())
        second = self.db.enqueue_job(request())
        self.assertEqual(first, second)

    def test_two_workers_only_one_claims_job(self):
        job_id = self.db.enqueue_job(request())
        barrier = threading.Barrier(2)
        results = []

        def claim(owner):
            barrier.wait()
            results.append(self.db.start_job(job_id, lease_owner=owner))

        threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), [False, True])


class FinancialIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp.name) / "db.sqlite"))
        self.db.init()
        self.db.initialize_paper_account(1000)

    def tearDown(self):
        self.temp.cleanup()

    def test_same_financial_operation_is_applied_at_most_once(self):
        effect = self.buy_effect()
        with self.db.connect() as connection:
            self.assertTrue(self.db._apply_paper_effect(connection, effect))
        with self.db.connect() as connection:
            self.assertFalse(self.db._apply_paper_effect(connection, effect))
            account = connection.execute(
                "SELECT cash FROM paper_accounts WHERE account_id='PAPER_DEFAULT'"
            ).fetchone()
            operations = connection.execute(
                "SELECT COUNT(*) FROM financial_operations WHERE operation_key=?",
                (effect["financial_operation_key"],),
            ).fetchone()[0]
            transactions = connection.execute(
                "SELECT COUNT(*) FROM paper_transactions WHERE financial_operation_key=?",
                (effect["financial_operation_key"],),
            ).fetchone()[0]
            outbox = connection.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=?",
                (effect["financial_operation_key"],),
            ).fetchone()[0]
        self.assertEqual(account[0], 800.0)
        self.assertEqual(operations, 1)
        self.assertEqual(transactions, 1)
        self.assertEqual(outbox, 1)
        self.assertTrue(all(value for key, value in self.db.financial_invariants().items()
                            if key.endswith("_matches")))

    @staticmethod
    def buy_effect():
        return {
            "financial_operation_key": "op:buy:INOD:1",
            "account_id": "PAPER_DEFAULT", "run_id": "R", "ticker": "INOD",
            "timestamp": now_iso(), "sector": "Technology", "quantity": 2,
            "price": 100.0, "notional_usd": 200.0, "action": "BUY",
            "prediction": {"prediction_id": "P", "run_id": "R", "ticker": "INOD",
                           "decision": "BUY", "confidence": 80,
                           "reference_price": 100.0, "horizon": "1-2M"},
        }

    def test_fault_boundaries_roll_back_then_retry_exactly_once(self):
        points = (
            "AFTER_OPERATION_CLAIM", "AFTER_CASH_UPDATE", "AFTER_POSITION_UPDATE",
            "AFTER_OUTBOX_WRITE", "BEFORE_OPERATION_COMMIT",
        )
        for index, point in enumerate(points):
            with self.subTest(point=point):
                effect = self.buy_effect()
                effect["financial_operation_key"] = f"op:fault:{index}"
                effect["prediction"]["prediction_id"] = f"P{index}"
                effect["fault_at"] = point
                with self.assertRaises(RuntimeError):
                    with self.db.connect() as connection:
                        self.db._apply_paper_effect(connection, effect)
                effect.pop("fault_at")
                with self.db.connect() as connection:
                    self.assertTrue(self.db._apply_paper_effect(connection, effect))
                    self.assertFalse(self.db._apply_paper_effect(connection, effect))
                with self.db.connect() as connection:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM paper_transactions WHERE financial_operation_key=?",
                        (effect["financial_operation_key"],),
                    ).fetchone()[0]
                self.assertEqual(count, 1)

    def test_crash_immediately_after_commit_never_reapplies_financial_effect(self):
        effect = self.buy_effect()
        effect["financial_operation_key"] = "op:after-commit"
        effect["prediction"]["prediction_id"] = "P_AFTER_COMMIT"
        effect["fault_at"] = "AFTER_COMMIT"
        with self.assertRaises(RuntimeError):
            self.db.apply_paper_effect(effect)
        effect.pop("fault_at")
        self.assertFalse(self.db.apply_paper_effect(effect))
        with self.db.connect() as connection:
            count = connection.execute("""SELECT COUNT(*) FROM paper_transactions
                WHERE financial_operation_key='op:after-commit'""").fetchone()[0]
        self.assertEqual(count, 1)

    def test_cancel_before_commit_blocks_effect_and_after_commit_preserves_history(self):
        before = self.buy_effect()
        before["financial_operation_key"] = "op:cancel-before"
        before["prediction"]["prediction_id"] = "P_CANCEL_BEFORE"
        self.assertEqual(self.db.request_financial_cancellation("op:cancel-before"),
                         "CANCELLED_BEFORE_COMMIT")
        self.assertFalse(self.db.apply_paper_effect(before))

        after = self.buy_effect()
        after["financial_operation_key"] = "op:cancel-after"
        after["prediction"]["prediction_id"] = "P_CANCEL_AFTER"
        self.assertTrue(self.db.apply_paper_effect(after))
        self.assertEqual(self.db.request_financial_cancellation("op:cancel-after"),
                         "COMMITTED_BEFORE_CANCEL_REQUEST")
        self.assertFalse(self.db.apply_paper_effect(after))
        with self.db.connect() as connection:
            count = connection.execute("""SELECT COUNT(*) FROM paper_transactions
                WHERE financial_operation_key='op:cancel-after'""").fetchone()[0]
        self.assertEqual(count, 1)

    def test_same_ticker_isolated_by_paper_account_composite_identity(self):
        self.db.initialize_paper_account(1000, account_id="SECOND")
        first = self.buy_effect()
        second = self.buy_effect()
        second["account_id"] = "SECOND"
        second["financial_operation_key"] = "op:second-account"
        second["prediction"]["prediction_id"] = "P_SECOND"
        self.assertTrue(self.db.apply_paper_effect(first))
        self.assertTrue(self.db.apply_paper_effect(second))
        with self.db.connect() as connection:
            rows = connection.execute("""SELECT account_id,quantity FROM portfolio_positions
                WHERE ticker='INOD' ORDER BY account_id""").fetchall()
        self.assertEqual([(row["account_id"], row["quantity"]) for row in rows],
                         [("PAPER_DEFAULT", 2.0), ("SECOND", 2.0)])

    def test_discord_publish_boundaries_never_reapply_financial_effect(self):
        effect = self.buy_effect()
        effect["financial_operation_key"] = "op:publish-boundary"
        effect["prediction"]["prediction_id"] = "P_PUBLISH_BOUNDARY"
        self.assertTrue(self.db.apply_paper_effect(effect))
        pending = self.db.pending_outbox_events()
        self.assertEqual(len([row for row in pending
                              if row["aggregate_id"] == "op:publish-boundary"]), 1)
        self.assertFalse(self.db.apply_paper_effect(effect))
        self.db.mark_outbox_event("op:publish-boundary", "PAPER_BUY_COMMITTED", True)
        self.assertFalse(self.db.apply_paper_effect(effect))
        with self.db.connect() as connection:
            count = connection.execute("""SELECT COUNT(*) FROM paper_transactions
                WHERE financial_operation_key='op:publish-boundary'""").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
