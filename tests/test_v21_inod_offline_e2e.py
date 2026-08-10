from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_agent.agents import MockCriticAgent, MockResearchAgent
from stock_agent.market_quality import assess_market_quality
from stock_agent.orchestrator import Orchestrator
from stock_agent.schemas import CompanyState, EvidenceItem, MarketSnapshot


FIXTURE = Path(__file__).parent / "fixtures" / "inod_20260810_failure"


class InodFixtureMarketProvider:
    def snapshot(self, _ticker: str) -> MarketSnapshot:
        container = json.loads((FIXTURE / "market_snapshot.json").read_text(encoding="utf-8"))
        payload = json.loads(container["rows"][0]["payload_json"])
        snapshot = MarketSnapshot(**payload)
        quality = assess_market_quality(
            api_received_at=snapshot.ingested_at,
            provider_observed_at=snapshot.observed_at,
            bar_end_at=snapshot.candle_as_of,
            volume=snapshot.volume, average_volume=snapshot.avg_20d_volume)
        for name, value in quality.__dict__.items():
            setattr(snapshot, name, value)
        snapshot.relative_volume_certified = False
        snapshot.stage = "UNCERTIFIED"
        return snapshot

    def company_state(self, _ticker: str) -> CompanyState:
        container = json.loads((FIXTURE / "company_state_before.json").read_text(encoding="utf-8"))
        payload = json.loads(container["current_rows_for_diagnostics_only"][0]["payload_json"])
        return CompanyState(**payload)


class InodFixtureEvidenceCollector:
    def __init__(self, reverse: bool = False):
        self.reverse = reverse

    def collect(self, _ticker: str) -> list[EvidenceItem]:
        container = json.loads((FIXTURE / "sec_index.json").read_text(encoding="utf-8"))
        rows = [EvidenceItem(**json.loads(row["payload_json"]))
                for row in container["evidence_metadata"]]
        return list(reversed(rows)) if self.reverse else rows


def config(root: str) -> dict:
    return {
        "mode": "PAPER", "database_path": str(Path(root) / "agent.sqlite"),
        "vault_path": str(Path(root) / "vault"), "report_dir": str(Path(root) / "reports"),
        "edgar_mode": "mock", "analysis": {"min_evidence": 3, "max_evidence_age_days": 9999},
        "risk_rules": {"minimum_price_usd": 3, "minimum_market_cap_usd": 0,
            "minimum_avg_volume_usd": 0, "minimum_reward_risk": 1,
            "stage_3_action": "WAIT", "max_data_age_days": 9999,
            "high_volatility_atr_pct": 20},
        "paper": {"initial_cash_usd": 100_000},
    }


class OfflineGoldenE2ETests(unittest.TestCase):
    def _run(self, reverse: bool = False):
        temp = tempfile.TemporaryDirectory()
        app = Orchestrator(config(temp.name), market_provider=InodFixtureMarketProvider(),
            evidence_collector=InodFixtureEvidenceCollector(reverse),
            researcher=MockResearchAgent(), critic=MockCriticAgent())
        result = app.analyze("INOD")
        return temp, app, result

    def test_inod_failure_runs_to_diagnostic_but_never_exports_fake_normal_decision(self):
        temp, app, result = self._run()
        try:
            certification = result["certification"]
            report = Path(result["report_path"]).read_text(encoding="utf-8")
            self.assertEqual(certification.execution_status, "SUCCESS")
            self.assertEqual(certification.certification_status, "BLOCKED_MARKET_DATA")
            self.assertEqual(certification.action, "NO_CERTIFIED_ACTION")
            self.assertIn("Decision Confidence: **N/A**", report)
            self.assertNotIn("# TradePlan", report)
            self.assertNotIn("# Position Sizing", report)
            with app.db.connect() as connection:
                run = connection.execute("SELECT * FROM analysis_runs").fetchone()
                positions = connection.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0]
            self.assertEqual(run["execution_status"], "SUCCESS")
            self.assertEqual(run["certified_action"], "NO_CERTIFIED_ACTION")
            self.assertEqual(positions, 0)
            self.assertFalse((Path(temp.name) / "vault" / "02_Companies" / "INOD" / "Core.md").exists())
        finally:
            temp.cleanup()

    def test_evidence_order_does_not_change_blocking_certification(self):
        first_temp, _, first = self._run(False)
        second_temp, _, second = self._run(True)
        try:
            self.assertEqual(first["certification"].certification_status,
                             second["certification"].certification_status)
            self.assertEqual(first["certification"].action, second["certification"].action)
        finally:
            first_temp.cleanup(); second_temp.cleanup()


if __name__ == "__main__":
    unittest.main()
