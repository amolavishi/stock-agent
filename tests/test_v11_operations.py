from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stock_agent.cancellation import CancellationToken, RunCancelledError
from stock_agent.database import Database
from stock_agent.discord_runtime import DiscordPresenters
from stock_agent.health import local_health
from stock_agent.schemas import UserRequest, now_iso


class OperationsV11Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.db"))
        self.db.init()

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, ticker="IONQ"):
        return UserRequest("REQ1", "MSG1", "USER1", now_iso(), f"{ticker} 분석",
                           "ANALYZE", [ticker], analysis_intensity="MINIMUM",
                           min_debate_rounds=2, max_debate_rounds=3)

    def test_durable_queue_survives_database_reopen(self):
        request = self.request()
        self.db.save_user_request(request)
        job_id = self.db.enqueue_job(request)
        reopened = Database(str(self.db.path))
        reopened.init()
        rows = reopened.recoverable_jobs()
        self.assertEqual(rows[0]["job_id"], job_id)
        self.assertEqual(rows[0]["payload"]["tickers"], ["IONQ"])

    def test_running_job_requeues_once_then_aborts(self):
        request = self.request()
        self.db.save_user_request(request)
        job_id = self.db.enqueue_job(request)
        self.db.start_job(job_id)
        with self.db.connect() as connection:
            connection.execute("UPDATE job_queue SET lease_until=? WHERE job_id=?",
                               ("2000-01-01T00:00:00+00:00", job_id))
        self.assertEqual(len(self.db.recoverable_jobs(max_attempts=2)), 1)
        self.db.start_job(job_id)
        with self.db.connect() as connection:
            connection.execute("UPDATE job_queue SET lease_until=? WHERE job_id=?",
                               ("2000-01-01T00:00:00+00:00", job_id))
        self.assertEqual(self.db.recoverable_jobs(max_attempts=2), [])

    def test_running_job_with_live_lease_is_not_recovered(self):
        request = self.request()
        self.db.save_user_request(request)
        job_id = self.db.enqueue_job(request)
        self.db.start_job(job_id)
        self.assertEqual(self.db.recoverable_jobs(), [])

    def test_queued_ticker_cancellation_is_persistent(self):
        request = self.request()
        self.db.save_user_request(request)
        job_id = self.db.enqueue_job(request)
        self.db.request_cancellation_for_tickers(["ionq"])
        self.assertTrue(self.db.is_job_cancelled(job_id))
        self.assertEqual(self.db.recoverable_jobs(), [])

    def test_active_run_cancellation_uses_safe_point(self):
        request = self.request()
        self.db.save_user_request(request)
        self.db.start_run("RUN1", "IONQ", "PAPER", request.request_id, "MINIMUM")
        self.db.request_cancellation("RUN1")
        with self.assertRaises(RunCancelledError):
            CancellationToken(self.db, "RUN1").check("BEFORE_RESEARCH")
        self.assertEqual(self.db.get_run("RUN1")["status"], "CANCELLED")

    def test_local_health_never_returns_secret_values(self):
        root = Path(self.tmp.name)
        config = {"mode": "PAPER", "report_dir": str(root / "reports"),
                  "vault_path": str(root / "vault"), "credentials": {
                      "toss_app_key": "secret", "toss_app_secret": "secret2",
                      "deepseek_api_key": "secret3", "sec_user_agent": "name email",
                      "discord_research_token": "a", "discord_critic_token": "b",
                      "discord_chairman_token": "c"}}
        result = local_health(config, self.db)
        self.assertTrue(result["healthy"])
        self.assertNotIn("secret", str(result))

    def test_report_delivery_updates_both_delivery_records(self):
        request = self.request()
        self.db.save_user_request(request)
        self.db.start_run("RUN_DELIVERY", "IONQ", "PAPER", request.request_id, "MINIMUM")
        with self.db.connect() as connection:
            connection.execute("""INSERT INTO report_artifacts
                (run_id,ticker_label,markdown_path,publish_status,publish_attempts,last_error,created_at)
                VALUES(?,?,?,?,?,?,?)""", ("RUN_DELIVERY", "IONQ", "report.md", "PENDING", 0, "", now_iso()))
        presenter = DiscordPresenters(None, None, None, self.db)
        presenter._mark_published("RUN_DELIVERY", "PUBLISHED", "")
        with self.db.connect() as connection:
            run = connection.execute("SELECT delivery_status,delivered_at FROM analysis_runs WHERE run_id=?",
                                     ("RUN_DELIVERY",)).fetchone()
            artifact = connection.execute("SELECT publish_status,delivered_at FROM report_artifacts WHERE run_id=?",
                                          ("RUN_DELIVERY",)).fetchone()
        self.assertEqual(run["delivery_status"], "PUBLISHED")
        self.assertTrue(run["delivered_at"])
        self.assertEqual(artifact["publish_status"], "PUBLISHED")
        self.assertTrue(artifact["delivered_at"])


if __name__ == "__main__":
    unittest.main()
