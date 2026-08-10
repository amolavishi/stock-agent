import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stock_agent.agents import MockCriticAgent, MockResearchAgent
from stock_agent.database import Database
from stock_agent.edgar_documents import DocumentCache, EvidenceClassifier, RelevantSectionExtractor
from stock_agent.evidence import MockEvidenceCollector
from stock_agent.knowledge import ObsidianKnowledgeManager
from stock_agent.market import MockMarketDataProvider
from stock_agent.orchestrator import Orchestrator
from stock_agent.providers import MockDiscordNotifier, MockLLMProvider
from stock_agent.reports import render_report
from stock_agent.risk import RiskEngine
from stock_agent.schemas import EvidenceItem
from stock_agent.sec import EdgarError, EdgarMetadataCollector
from stock_agent.trade_plan import build_heuristic_trade_plan
from stock_agent.validation import AnalysisIncompleteError, InvalidTickerError, UnsupportedMockTickerError, validate_ticker
from build_zip import create_distribution_zip


RULES = {
    "minimum_price_usd": 3.0,
    "minimum_market_cap_usd": 300_000_000,
    "minimum_avg_volume_usd": 10_000_000,
    "minimum_reward_risk": 2.0,
    "stage_3_action": "WAIT",
    "max_data_age_days": 3,
    "high_volatility_atr_pct": 12.0,
}


def make_config(root: str, min_evidence: int = 3) -> dict:
    return {
        "mode": "PAPER",
        "database_path": str(Path(root) / "agent.db"),
        "vault_path": str(Path(root) / "vault"),
        "edgar_mode": "mock",
        "analysis": {"min_evidence": min_evidence, "max_evidence_age_days": 30},
        "risk_rules": RULES.copy(),
    }


def analysis_parts(ticker="IONQ"):
    provider = MockMarketDataProvider()
    market = provider.snapshot(ticker)
    state = provider.company_state(ticker)
    evidence = MockEvidenceCollector().collect(ticker)
    research = MockResearchAgent().run(state, market, evidence)
    critic = MockCriticAgent().run(research, state, market)
    plan = build_heuristic_trade_plan(market)
    return market, state, evidence, research, critic, plan


class SafetyTests(unittest.TestCase):
    def test_unknown_mock_ticker_fails(self):
        with self.assertRaises(UnsupportedMockTickerError):
            MockMarketDataProvider().snapshot("AAPL")

    def test_unknown_company_state_fails(self):
        with self.assertRaises(UnsupportedMockTickerError):
            MockMarketDataProvider().company_state("AAPL")

    def test_ticker_path_traversal_rejected(self):
        for value in ("../../X", "..\\X", "A A", "A/B", "A\nB"):
            with self.assertRaises(InvalidTickerError):
                validate_ticker(value)

    def test_valid_ticker_normalized(self):
        self.assertEqual(validate_ticker("brk.b"), "BRK.B")

    def test_report_stays_inside_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Orchestrator(make_config(tmp))
            result = app.analyze("IONQ")
            self.assertIn(Path(tmp, "vault").resolve(), result["report_path"].resolve().parents)

    def test_mock_evidence_marked_mock(self):
        items = MockEvidenceCollector().collect("IONQ")
        self.assertTrue(all(item.is_mock for item in items))
        self.assertTrue(all(item.source_type.startswith("MOCK_") for item in items))
        self.assertTrue(all(item.source_url.startswith("mock://") for item in items))

    def test_mock_report_has_strong_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(Orchestrator(make_config(tmp)).analyze("IONQ")["report_path"]).read_text(encoding="utf-8")
            self.assertIn("⚠ MOCK DATA — 실제 투자판단 금지", report)


class DataIntegrityTests(unittest.TestCase):
    def test_stale_market_data_blocks_analysis(self):
        market, state, _, research, critic, plan = analysis_parts()
        market.observed_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        result = RiskEngine(RULES).evaluate(research, critic, state, market, plan)
        self.assertFalse(result.hard_filter_pass)
        self.assertTrue(any("STALE" in failure for failure in result.failures))

    def test_min_evidence_enforced(self):
        class TooFewEvidence:
            def collect(self, ticker):
                return MockEvidenceCollector().collect(ticker)[:2]

        with tempfile.TemporaryDirectory() as tmp:
            app = Orchestrator(make_config(tmp), evidence_collector=TooFewEvidence())
            with self.assertRaises(AnalysisIncompleteError):
                app.analyze("IONQ")

    def test_stage3_rule_enforced(self):
        market, state, _, research, critic, plan = analysis_parts("SOUN")
        result = RiskEngine(RULES).evaluate(research, critic, state, market, plan)
        self.assertEqual(result.risk_decision, "WAIT")
        self.assertTrue(any("Stage 3" in warning for warning in result.warnings))

    def test_reward_risk_matches_trade_plan(self):
        market, state, _, research, critic, plan = analysis_parts()
        result = RiskEngine(RULES).evaluate(research, critic, state, market, plan)
        expected = round((plan.target_1 - plan.entry_price) / (plan.entry_price - plan.stop_price), 2)
        self.assertEqual(plan.reward_risk, expected)
        self.assertIs(result.trade_plan, plan)

    def test_claim_evidence_id_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator(make_config(tmp)).analyze("IONQ")
            evidence_ids = {item.evidence_id for item in result["evidence"]}
            self.assertTrue(all(claim["evidence_id"] in evidence_ids for claim in result["research"].claims))

    def test_edgar_metadata_is_unclassified(self):
        items = FixtureEdgar().collect("TEST")
        self.assertTrue(items)
        self.assertTrue(all(item.evidence_grade == "UNCLASSIFIED" for item in items))

    def test_edgar_duplicate_accession_removed(self):
        items = FixtureEdgar(duplicate=True).collect("TEST")
        self.assertEqual(len(items), 1)

    def test_edgar_unsupported_ticker(self):
        class MissingTicker(EdgarMetadataCollector):
            def _get_json(self, url):
                return {"0": {"ticker": "OTHER", "cik_str": 1}}

        with self.assertRaises(EdgarError):
            MissingTicker().collect("TEST")

    def test_document_classifier_does_not_promote_plain_8k(self):
        item = FixtureEdgar().collect("TEST")[0]
        classified = EvidenceClassifier().classify(item, "The company filed a routine update.")
        self.assertEqual(classified.evidence_grade, "UNCLASSIFIED")


class PersistenceAndIntegrationTests(unittest.TestCase):
    def test_database_run_lifecycle_preserves_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite"))
            db.init()
            started = db.start_run("run-1", "IONQ", "PAPER")
            row = db.get_run("run-1")
            self.assertEqual(row["requested_at"], started)
            self.assertEqual(row["started_at"], started)
            db.fail_run("run-1", "test")
            row = db.get_run("run-1")
            self.assertEqual(row["requested_at"], started)
            self.assertEqual(row["status"], "SYSTEM_ERROR")
            self.assertIsNotNone(row["finished_at"])

    def test_orchestrator_end_to_end_mock(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator(make_config(tmp)).analyze("IONQ")
            self.assertEqual(result["decision"].decision, "WAIT")
            self.assertTrue(result["report_path"].exists())

    def test_orchestrator_failure_records_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Orchestrator(make_config(tmp))
            with self.assertRaises(UnsupportedMockTickerError):
                app.analyze("AAPL")
            with app.db.connect() as conn:
                row = conn.execute("SELECT * FROM analysis_runs WHERE ticker='AAPL'").fetchone()
            self.assertEqual(row["status"], "SYSTEM_ERROR")
            self.assertIsNotNone(row["finished_at"])

    def test_obsidian_company_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ObsidianKnowledgeManager(str(Path(tmp) / "vault"))
            state = MockMarketDataProvider().company_state("IONQ")
            manager.update_company_state(state)
            loaded = manager.load_company_state("IONQ")
            self.assertEqual(loaded.ticker, "IONQ")
            self.assertEqual(loaded.known_risks, state.known_risks)

    def test_notifier_interface_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            notifier = MockDiscordNotifier()
            Orchestrator(make_config(tmp), notifier=notifier).analyze("IONQ")
            self.assertEqual(len(notifier.messages), 1)
            self.assertIn("IONQ", notifier.messages[0])

    def test_required_persistence_tables_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite"))
            db.init()
            with db.connect() as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"company_states", "portfolio_positions", "transactions", "api_usage", "model_costs"} <= tables)

    def test_distribution_zip_uses_posix_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            (root / "stock_agent").mkdir(parents=True)
            (root / "stock_agent" / "demo.py").write_text("pass", encoding="utf-8")
            output = Path(tmp) / "package.zip"
            create_distribution_zip(root, output)
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
            self.assertEqual(names, ["stock_agent/demo.py"])
            self.assertTrue(all("\\" not in name for name in names))


class FixtureEdgar(EdgarMetadataCollector):
    def __init__(self, duplicate=False):
        super().__init__()
        self.duplicate = duplicate

    def _get_json(self, url):
        if "company_tickers" in url:
            return {"0": {"ticker": "TEST", "cik_str": 12345}}
        accessions = ["0000123456-26-000001"] * (2 if self.duplicate else 1)
        return {"filings": {"recent": {
            "form": ["8-K"] * len(accessions),
            "accessionNumber": accessions,
            "filingDate": [datetime.now(timezone.utc).date().isoformat()] * len(accessions),
            "primaryDocument": ["test-8k.htm"] * len(accessions),
        }}}


if __name__ == "__main__":
    unittest.main()
