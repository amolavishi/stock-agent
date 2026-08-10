import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from stock_agent.certification import CertificationEngine, NO_CERTIFIED_ACTION
from stock_agent.database import Database
from stock_agent.reports import render_uncertified_report
from stock_agent.knowledge import ObsidianKnowledgeManager
from stock_agent.schemas import (CompanyState, EvidenceItem, MarketSnapshot,
                                 UserRequest, now_iso)


FIXTURE = Path(__file__).parent / "fixtures" / "inod_20260810_failure"


def market(data_quality="OK"):
    return MarketSnapshot(
        "INOD", now_iso(), 50.0, 0, 0, 0, 1000, 900, 1_000_000,
        49, 45, 2, source="toss", data_quality=data_quality, is_mock=False,
    )


class CertificationContractTests(unittest.TestCase):
    def test_analyze_is_read_only_by_default(self):
        request = UserRequest("r", "m", "u", now_iso(), "INOD 분석", "ANALYZE", ["INOD"])
        self.assertFalse(request.paper_action_enabled)

    def test_deadlock_is_not_certified_wait(self):
        evidence = [EvidenceItem(
            "SEC_INOD_Q2", "INOD", "SEC", "10-Q", now_iso(), "Q2", "https://sec.gov/x",
            "A", "FINANCIAL", "parsed Q2 filing", is_mock=False,
            lifecycle_status="READY_FOR_ANALYSIS",
        )]
        result = CertificationEngine().evaluate(
            run_id="inod", debate_status="DEADLOCK", market=market(), evidence=evidence,
            capital_structure={"shares_outstanding": 1, "unknown_fields": []}, live_mode=True,
        )
        self.assertEqual(result.certification_status, "BLOCKED_DEBATE")
        self.assertEqual(result.action, NO_CERTIFIED_ACTION)
        self.assertEqual(result.trade_plan_status, "WITHHELD")
        self.assertEqual(result.position_sizing_status, "WITHHELD")
        self.assertIsNone(result.decision_confidence)

    def test_inod_unparsed_latest_10q_blocks_certification(self):
        evidence = [EvidenceItem(
            "SEC_INOD_000110465926092021", "INOD", "SEC", "10-Q", "2026-08-06",
            "Q2", "https://sec.gov/x", "UNCLASSIFIED", "UNCLASSIFIED",
            "SEC filing metadata only", is_mock=False, filed_at="2026-08-06",
        )]
        result = CertificationEngine().evaluate(
            run_id="inod", debate_status="DEADLOCK", market=market(), evidence=evidence,
            capital_structure={"shares_outstanding": 1, "unknown_fields": []}, live_mode=True,
        )
        self.assertNotEqual(result.certification_status, "CERTIFIED")
        self.assertIn("LATEST_MATERIAL_PERIODIC_FILING_NOT_READY", result.reason_codes)

    def test_all_material_blockers_are_preserved_even_when_market_is_primary_status(self):
        evidence = [EvidenceItem(
            "SEC_INOD_Q2", "INOD", "SEC", "10-Q", "2026-08-06",
            "Q2", "https://sec.gov/x", "UNCLASSIFIED", "UNCLASSIFIED",
            "metadata only", is_mock=False, filed_at="2026-08-06",
            lifecycle_status="DISCOVERED",
        )]
        result = CertificationEngine().evaluate(
            run_id="inod", debate_status="DEADLOCK", market=market("LOW"), evidence=evidence,
            capital_structure={
                "shares_outstanding": 1,
                "unknown_fields": [],
                "integrity_conflicts": [{"field": "atm_active"}],
            },
            live_mode=True,
            critical_open_issues=2,
            unresolved_must_answer=1,
            claim_validation_passed=False,
        )
        self.assertNotEqual(result.certification_status, "CERTIFIED")
        expected = {
            "LATEST_MATERIAL_PERIODIC_FILING_NOT_READY",
            "MATERIAL_CAPITAL_STRUCTURE_CONFLICT",
            "CLAIM_EVIDENCE_VALIDATION_FAILED",
            "MUST_ANSWER_EVIDENCE_REQUEST_UNRESOLVED",
            "DEBATE_DEADLOCK",
            "CRITICAL_ISSUE_UNRESOLVED",
        }
        self.assertTrue(expected.issubset(set(result.reason_codes)))
        self.assertEqual(result.analysis_status, "DEADLOCK")

    def test_uncertified_report_contract_withholds_trade_plan_and_sizing(self):
        result = CertificationEngine().evaluate(
            run_id="inod", debate_status="DEADLOCK", market=market(), evidence=[],
            capital_structure={}, live_mode=True,
        )
        report = render_uncertified_report("inod", "INOD", result, {"original_text": "INOD 분석"})
        self.assertIn("NO_CERTIFIED_ACTION", report)
        self.assertIn("Decision Confidence: **N/A**", report)
        self.assertNotIn("# TradePlan", report)
        self.assertNotIn("# Position Sizing", report)

    def test_database_migrates_independent_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            db.init()
            with db.connect() as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(analysis_runs)")}
                self.assertTrue({
                    "execution_status", "analysis_status", "certification_status",
                    "side_effect_status", "certified_action",
                }.issubset(columns))
                self.assertIsNotNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='certification_records'"
                ).fetchone())

    def test_uncertified_run_cannot_write_obsidian_core(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ObsidianKnowledgeManager(directory)
            with self.assertRaises(PermissionError):
                manager.sync_run(
                    "INOD", "R", CompanyState("INOD", now_iso(), 0, 0, 0, False, 0, [], []),
                    [], object(), object(), object(), Path(directory) / "missing.md",
                    certification_status="BLOCKED_EVIDENCE",
                )
            self.assertFalse((Path(directory) / "02_Companies" / "INOD" / "Core.md").exists())


class InodGoldenFailureTests(unittest.TestCase):
    def test_fixture_hashes_are_stable(self):
        hashes = json.loads((FIXTURE / "fixture_sha256s.json").read_text(encoding="utf-8"))
        prefix = "fixtures/inod_20260810_failure/"
        checked = 0
        for relative, expected in hashes.items():
            if not relative.startswith(prefix):
                continue
            path = FIXTURE / relative[len(prefix):]
            if path.name == "fixture_sha256s.json":
                continue
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected, relative)
            checked += 1
        self.assertGreaterEqual(checked, 40)

    def test_original_inod_report_reproduces_known_contract_failure(self):
        report = (FIXTURE / "original_report.md").read_text(encoding="utf-8")
        self.assertIn("Decision: **WAIT**", report)
        self.assertIn("Confidence: **53/100**", report)
        self.assertIn("Status: `DEADLOCK`", report)
        self.assertIn("# TradePlan", report)
        self.assertIn("# Position Sizing", report)

    def test_inod_fixture_is_blocked_and_new_report_withholds_outputs(self):
        market_row = json.loads((FIXTURE / "market_snapshot.json").read_text(encoding="utf-8"))["rows"][0]
        snapshot = MarketSnapshot(**json.loads(market_row["payload_json"]))
        index = json.loads((FIXTURE / "sec_index.json").read_text(encoding="utf-8"))
        evidence = [EvidenceItem(**json.loads(row["payload_json"])) for row in index["evidence_metadata"]]
        result = CertificationEngine().evaluate(
            run_id="20260810_031126_INOD_5c7a21", debate_status="DEADLOCK",
            market=snapshot, evidence=evidence,
            capital_structure={"shares_outstanding": 32655358, "unknown_fields": []},
            live_mode=True, critical_open_issues=48,
        )
        report = render_uncertified_report(result.run_id, "INOD", result, {}, market=snapshot,
                                           evidence=evidence)
        self.assertNotEqual(result.certification_status, "CERTIFIED")
        self.assertEqual(result.action, "NO_CERTIFIED_ACTION")
        self.assertIn("Decision Confidence: **N/A**", report)
        self.assertNotIn("# TradePlan", report)
        self.assertNotIn("# Position Sizing", report)


if __name__ == "__main__":
    unittest.main()
