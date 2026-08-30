from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from stock_agent.adapters import _market_asset_observation, deterministic_market_context_from_payload
from stock_agent.gates import GateDecision, MarketContextGate
from stock_agent.models import EffectiveRuleSet
from stock_agent.shadow import DailyShadowRunner
from stock_agent.store import SQLiteStore


def _asset(symbol: str, observed_at: str):
    unit = {
        "SPY": "USD_PER_SHARE", "QQQ": "USD_PER_SHARE", "IWM": "USD_PER_SHARE",
        "SOXX": "USD_PER_SHARE", "WTI": "USD_PER_BARREL", "BTC": "USD_PER_COIN",
        "ETH": "USD_PER_COIN", "US10Y": "PERCENT",
    }.get(symbol, "INDEX_POINTS")
    currency = "USD" if unit.startswith("USD_") else None
    return _market_asset_observation(
        symbol=symbol,
        provider="fixture",
        source_identifier=f"https://example.test/{symbol}",
        payload={"observations": [{"timestamp": observed_at, "value": 1.0}, {"timestamp": observed_at, "value": 1.1}]},
        values=[1.0, 1.1],
        observed_at=observed_at,
        unit=unit,
        currency=currency,
    )


def _weekend_context():
    timestamps = {
        "SPY": "2026-08-28T20:00:00Z", "QQQ": "2026-08-28T20:00:00Z",
        "IWM": "2026-08-28T20:00:00Z", "SOXX": "2026-08-28T20:00:00Z",
        "VIX": "2026-08-27T00:00:00Z", "US10Y": "2026-08-27T00:00:00Z",
        "DXY": "2026-08-27T04:00:00Z", "WTI": "2026-08-25T00:00:00Z",
        "BTC": "2026-08-30T11:49:50Z", "ETH": "2026-08-30T11:49:40Z",
    }
    observations = [_asset(symbol, observed) for symbol, observed in timestamps.items()]
    context = deterministic_market_context_from_payload(observations)
    context["source"] = [{k: v for k, v in item.items() if k != "_raw_artifact"} for item in observations]
    groups = {
        "SPY": "exchange", "QQQ": "exchange", "IWM": "exchange", "SOXX": "exchange",
        "VIX": "daily", "US10Y": "daily", "WTI": "daily", "DXY": "fx",
        "BTC": "crypto", "ETH": "crypto",
    }
    for item in observations:
        context["assets"][item["symbol"]].update({k: v for k, v in item.items() if k not in {"payload", "_raw_artifact"}})
        context["assets"][item["symbol"]]["sync_group"] = groups[item["symbol"]]
    return context


class MarketContextShadowHotfixTests(unittest.TestCase):
    def test_dxy_one_business_day_lag_uses_fx_freshness_not_equity_session_date(self):
        receipt = MarketContextGate().evaluate(
            _weekend_context(),
            EffectiveRuleSet(),
            evaluation_time=datetime(2026, 8, 30, 11, 51, tzinfo=timezone.utc),
        )
        self.assertEqual(receipt.decision, GateDecision.PASS)

    def test_exchange_asset_still_requires_latest_completed_equity_session(self):
        context = _weekend_context()
        context["assets"]["SPY"]["observed_at"] = "2026-08-27T20:00:00Z"
        receipt = MarketContextGate().evaluate(
            context,
            EffectiveRuleSet(),
            evaluation_time=datetime(2026, 8, 30, 11, 51, tzinfo=timezone.utc),
        )
        self.assertEqual(receipt.decision, GateDecision.INSUFFICIENT_EVIDENCE)

    def test_pre_discovery_market_block_is_degraded_not_clean_success(self):
        store = SQLiteStore(":memory:")

        class BlockedAgent:
            def __init__(self, target_store):
                self.store = target_store

            def run(self, mode, data):
                run = self.store.create_run(mode, EffectiveRuleSet(), "market-block-context", self.store.current_evidence_epoch())
                self.store.record_funnel(run.run_id, "MARKET_CONTEXT_GATE", 0, {"core_input_complete": False})
                self.store.finish_run(run.run_id, "NO_QUALIFIED_CANDIDATE")
                return SimpleNamespace(
                    run_id=run.run_id,
                    outcome="NO_QUALIFIED_CANDIDATE",
                    blocked_reason="MARKET_CONTEXT_GATE",
                )

        metadata = {
            "code_git_sha": "a" * 40,
            "branch": "test",
            "ruleset_hash": "rules",
            "prompt_library_hash": "prompt",
            "config_hash": "config",
            "model": "recorded",
            "provider": "recorded",
            "reasoning_effort": {"BALANCED": "medium"},
            "schema_version": "shadow-log-v1",
            "database_schema_version": "shadow-v1",
            "timezone": "Asia/Seoul",
            "broker_write_count": 0,
        }
        with tempfile.TemporaryDirectory() as temp:
            runner = DailyShadowRunner(BlockedAgent(store), temp, metadata, provider_health=lambda: {"status": "PASS"})
            result = runner.run({}, run_date="2026-08-30")
            self.assertEqual(result.status, "DEGRADED")
            log = json.loads(Path(result.artifact_paths["RUN_LOG"]).read_text(encoding="utf-8"))
            self.assertEqual(log["hunt_contract"]["status"], "BLOCKED_MARKET_CONTEXT")
            self.assertEqual(log["providers"]["market"]["status"], "DEGRADED")
            self.assertEqual(log["providers"]["sec"]["status"], "NOT_RUN")
            self.assertEqual(log["providers"]["research"]["status"], "NOT_RUN")
            self.assertEqual(log["providers"]["portfolio"]["status"], "NOT_RUN")
            self.assertTrue(any(error.get("classification") == "PRE_DISCOVERY_BLOCK" for error in log["errors"]))
            self.assertEqual(log["broker_write_count"], 0)
        store.close()


if __name__ == "__main__":
    unittest.main()
