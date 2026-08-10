import json
import os
import tempfile
import unittest
from pathlib import Path

from stock_agent.claim_validation import validate_claim_evidence
from stock_agent.database import Database
from stock_agent.discord_runtime import should_process_user
from stock_agent.hermes import HermesError, extract_json, extract_role_json
from stock_agent.hermes_agents import _normalize_critic_collections, _normalize_scores
from stock_agent.market import MockMarketDataProvider
from stock_agent.market_regime import MarketRegimeEngine
from stock_agent.paper import PaperPortfolio
from stock_agent.position_sizing import PositionSizingEngine, PositionSizingError
from stock_agent.schemas import EvidenceItem, MarketRegime
from stock_agent.security import redact_secrets
from stock_agent.toss import TossClient, TossMarketDataProvider
from stock_agent.trade_plan import build_heuristic_trade_plan
from stock_agent.validation import AnalysisIncompleteError


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class FakeTossOpener:
    def __init__(self):
        self.token_calls = 0

    def __call__(self, request, timeout=0):
        if request.full_url.endswith("/oauth2/token"):
            self.token_calls += 1
            return FakeResponse({"access_token": "hidden", "expires_in": 86400})
        if "/prices?" in request.full_url:
            return FakeResponse({"result": [{"symbol": "AAPL", "lastPrice": 160, "timestamp": "2026-08-09T12:00:00+00:00"}]})
        candles = []
        for i in range(80):
            close = 100 + i * 0.75
            candles.append({"timestamp": f"2026-01-{(i % 28)+1:02d}T00:00:00Z",
                            "openPrice": close - 1, "highPrice": close + 2,
                            "lowPrice": close - 2, "closePrice": close, "volume": 1000 + i})
        return FakeResponse({"result": {"candles": candles}})


class HermesAndSecurityTests(unittest.TestCase):
    def test_extract_plain_json(self):
        self.assertEqual(extract_json('{"decision":"WAIT"}')["decision"], "WAIT")

    def test_extract_fenced_json(self):
        self.assertEqual(extract_json('text\n```json\n{"x":1}\n```')["x"], 1)

    def test_extract_embedded_json(self):
        self.assertEqual(extract_json('answer: {"x":2} trailing')["x"], 2)

    def test_invalid_json_fails(self):
        with self.assertRaises(HermesError):
            extract_json("not json")

    def test_role_json_skips_cli_envelope(self):
        text = ('{"event":"turn.completed","status":"ok"}\n'
                '{"verdict":"CHALLENGE","critical_flaws":[],"failure_scenarios":[], '
                '"evidence_conflicts":[],"critic_decision":"WAIT","confidence":65}')
        value = extract_role_json(text, "critic")
        self.assertEqual(value["critic_decision"], "WAIT")

    def test_fractional_confidence_is_normalized(self):
        self.assertEqual(_normalize_scores({"confidence": 0.65})["confidence"], 65)

    def test_structured_evidence_conflicts_are_normalized(self):
        value = _normalize_critic_collections({"evidence_conflicts": [{"issue": "x"}]})
        self.assertIsInstance(value["evidence_conflicts"][0], str)

    def test_secret_redaction(self):
        old = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = "super-secret-value"
        try:
            self.assertNotIn("super-secret-value", redact_secrets("bad super-secret-value"))
        finally:
            if old is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old


class TossAndDecisionTests(unittest.TestCase):
    def test_toss_token_is_cached(self):
        opener = FakeTossOpener()
        client = TossClient("id", "secret", opener=opener)
        self.assertEqual(client.access_token(), client.access_token())
        self.assertEqual(opener.token_calls, 1)

    def test_toss_snapshot_computes_metrics(self):
        provider = TossMarketDataProvider(TossClient("id", "secret", opener=FakeTossOpener()))
        snapshot = provider.snapshot("AAPL")
        self.assertFalse(snapshot.is_mock)
        self.assertGreater(snapshot.ma50, 0)
        self.assertGreater(snapshot.atr_14, 0)
        self.assertEqual(snapshot.source, "TOSS_OPEN_API")

    def test_claim_without_evidence_fails(self):
        evidence = [EvidenceItem("E1", "AAPL", "SEC", "8-K", "2026-08-09", "t", "u", "U", "x", "s")]
        with self.assertRaises(AnalysisIncompleteError):
            validate_claim_evidence([{"claim": "unsupported", "evidence_ids": []}], evidence)

    def test_claim_unknown_evidence_fails(self):
        with self.assertRaises(AnalysisIncompleteError):
            validate_claim_evidence([{"claim": "x", "evidence_ids": ["NOPE"]}], [])

    def test_bot_messages_never_trigger(self):
        self.assertFalse(should_process_user(True))
        self.assertTrue(should_process_user(False))


class RiskPaperAuditTests(unittest.TestCase):
    def test_position_size_uses_loss_budget(self):
        plan = build_heuristic_trade_plan(MockMarketDataProvider().snapshot("IONQ"))
        size = PositionSizingEngine(100, 1).calculate(plan, 100_000, 100_000)
        self.assertLessEqual(size.quantity * plan.risk_per_share, 1000)

    def test_position_size_rejects_bad_stop(self):
        plan = build_heuristic_trade_plan(MockMarketDataProvider().snapshot("IONQ"))
        plan.stop_price = plan.entry_price
        with self.assertRaises(PositionSizingError):
            PositionSizingEngine().calculate(plan, 100_000, 100_000)

    def test_trade_plan_stop_remains_below_entry_when_ma50_is_high(self):
        market = MockMarketDataProvider().snapshot("IONQ")
        market.ma50 = market.current * 1.5
        plan = build_heuristic_trade_plan(market)
        self.assertLess(plan.stop_price, plan.entry_price)
        self.assertGreater(plan.expected_risk, 0)

    def test_market_regime_risk_on(self):
        source = MockMarketDataProvider().snapshot("IONQ")
        snapshots = {}
        for ticker in ("QQQ", "IWM", "SOXX"):
            row = source.__dict__.copy()
            row.update(ticker=ticker, current=120, ma20=100, return_20d_pct=5, snapshot_id="")
            snapshots[ticker] = type(source)(**row)
        self.assertEqual(MarketRegimeEngine().evaluate(snapshots), MarketRegime.RISK_ON)

    def test_market_regime_unknown_when_missing(self):
        self.assertEqual(MarketRegimeEngine().evaluate({}), MarketRegime.UNKNOWN)

    def test_paper_measurement_alpha_and_excursions(self):
        result = PaperPortfolio.measure(100, [102, 105, 104], [104, 108, 106], [98, 101, 103],
                                        95, 107, 115, {"QQQ": 1, "IWM": 2, "SECTOR": 3})
        self.assertEqual(result.return_pct, 4)
        self.assertEqual(result.qqq_alpha, 3)
        self.assertEqual(result.mfe, 8)
        self.assertEqual(result.mae, -2)
        self.assertTrue(result.target1_hit)

    def test_v03_audit_tables_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "db.sqlite"))
            db.init()
            with db.connect() as c:
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            expected = {"run_manifests", "market_regimes", "evidence_items", "chairman_outputs",
                        "final_decisions", "paper_transactions", "paper_performance"}
            self.assertTrue(expected <= tables)


if __name__ == "__main__":
    unittest.main()
