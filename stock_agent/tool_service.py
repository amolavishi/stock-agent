from __future__ import annotations

import json
import uuid
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import load_config
from .database import Database
from .discord_runtime import DiscordRESTBot
from .evidence import LiveEdgarEvidenceCollector
from .guard import FinalGuard
from .market_regime import MarketRegimeEngine
from .paper import PaperPortfolio
from .position_sizing import PositionSizingEngine
from .risk import RiskEngine
from .schemas import (CompanyState, CriticReview, EvidenceItem, InvestmentDecision,
                      MarketSnapshot, ResearchAnalysis, RiskResult, TradePlan, now_iso)
from .sec import SECCompanyFactsProvider
from .security import redact_secrets
from .toss import TossClient, TossMarketDataProvider
from .validation import validate_ticker


TOOL_SERVER_VERSION = "stock-agent-mcp-v001"


def _construct(cls, payload: dict[str, Any]):
    allowed = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in payload.items() if key in allowed})


def _result(data: Any = None, *, error: str | None = None,
            code: str | None = None) -> dict[str, Any]:
    if error:
        return {"ok": False, "data": None,
                "error": {"code": code or "TOOL_ERROR", "message": redact_secrets(error)}}
    return {"ok": True, "data": data, "error": None}


class StockAgentToolService:
    """Hermes-callable deterministic service. It never invokes Hermes or an LLM."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_config()
        credentials = self.config["credentials"]
        self.db = Database(self.config["database_path"])
        self.db.init()
        self.market = TossMarketDataProvider(TossClient(
            credentials["toss_app_key"], credentials["toss_app_secret"]))
        self.sec_user_agent = credentials["sec_user_agent"]
        cache = Path(self.config["vault_path"]) / "99_Cache" / "edgar"
        self.evidence = LiveEdgarEvidenceCollector(str(cache), self.sec_user_agent)
        self.company_facts = SECCompanyFactsProvider(self.sec_user_agent)
        self.risk = RiskEngine(self.config["risk_rules"])
        self.sizing = PositionSizingEngine(
            self.config["risk_rules"].get("max_position_pct", 10),
            self.config["risk_rules"].get("max_loss_pct", 0.75))
        self.guard = FinalGuard()
        self.paper = PaperPortfolio(self.db)

    def call(self, operation: Callable[[], Any]) -> dict[str, Any]:
        try:
            return _result(operation())
        except Exception as exc:
            return _result(error=str(exc), code=type(exc).__name__.upper())

    def market_get_snapshot(self, ticker: str) -> dict[str, Any]:
        return self.call(lambda: asdict(self.market.snapshot(validate_ticker(ticker))))

    def market_get_benchmark_snapshots(self) -> dict[str, Any]:
        return self.call(lambda: {ticker: asdict(self.market.snapshot(ticker))
                                  for ticker in ("QQQ", "IWM", "SOXX")})

    def market_get_regime(self) -> dict[str, Any]:
        def operation():
            snapshots = {ticker: self.market.snapshot(ticker) for ticker in ("QQQ", "IWM", "SOXX")}
            return {"regime": MarketRegimeEngine().evaluate(snapshots).value,
                    "snapshot_ids": {ticker: value.snapshot_id for ticker, value in snapshots.items()}}
        return self.call(operation)

    def sec_get_filing_evidence(self, ticker: str, as_of: str = "", limit: int = 8) -> dict[str, Any]:
        def operation():
            items = self.evidence.collect(validate_ticker(ticker))[:max(1, min(limit, 20))]
            if as_of:
                items = [item for item in items if item.published_at <= as_of[:10]]
            return [asdict(item) for item in items]
        return self.call(operation)

    def sec_get_company_facts(self, ticker: str, as_of: str = "") -> dict[str, Any]:
        return self.call(lambda: self.company_facts.facts(validate_ticker(ticker)))

    def sec_request_additional_evidence(self, ticker: str, request: dict[str, Any]) -> dict[str, Any]:
        limit = int(request.get("limit", 8))
        return self.sec_get_filing_evidence(ticker, str(request.get("as_of", "")), limit)

    def state_get_company_state(self, ticker: str) -> dict[str, Any]:
        def operation():
            ticker_value = validate_ticker(ticker)
            with self.db.connect() as connection:
                row = connection.execute("SELECT payload_json FROM company_states WHERE ticker=?", (ticker_value,)).fetchone()
            return json.loads(row[0]) if row else None
        return self.call(operation)

    def state_get_latest_thesis(self, ticker: str) -> dict[str, Any]:
        def operation():
            with self.db.connect() as connection:
                row = connection.execute("""SELECT r.payload_json FROM research_outputs r
                    JOIN analysis_runs a ON a.run_id=r.run_id WHERE a.ticker=?
                    ORDER BY a.requested_at DESC LIMIT 1""", (validate_ticker(ticker),)).fetchone()
            return json.loads(row[0]) if row else None
        return self.call(operation)

    def state_get_decision_history(self, ticker: str, limit: int = 10) -> dict[str, Any]:
        def operation():
            with self.db.connect() as connection:
                rows = connection.execute("""SELECT run_id,timestamp,decision,confidence
                    FROM final_decisions WHERE ticker=? ORDER BY timestamp DESC LIMIT ?""",
                    (validate_ticker(ticker), max(1, min(limit, 100)))).fetchall()
            return [dict(row) for row in rows]
        return self.call(operation)

    def state_get_portfolio_state(self) -> dict[str, Any]:
        return self.call(lambda: [dict(row) for row in self.db.portfolio_positions()])

    def risk_evaluate(self, research: dict[str, Any], critic: dict[str, Any],
                      company_state: dict[str, Any], market_snapshot: dict[str, Any],
                      trade_plan: dict[str, Any]) -> dict[str, Any]:
        def operation():
            value = self.risk.evaluate(_construct(ResearchAnalysis, research),
                _construct(CriticReview, critic), _construct(CompanyState, company_state),
                _construct(MarketSnapshot, market_snapshot), _construct(TradePlan, trade_plan))
            return asdict(value)
        return self.call(operation)

    def sizing_calculate(self, trade_plan: dict[str, Any], equity_usd: float,
                         cash_usd: float) -> dict[str, Any]:
        return self.call(lambda: asdict(self.sizing.calculate(
            _construct(TradePlan, trade_plan), float(equity_usd), float(cash_usd))))

    def guard_validate_claims(self, claims: list[dict[str, Any]],
                              evidence: list[dict[str, Any]]) -> dict[str, Any]:
        return self.call(lambda: self.guard.validate_claims(
            claims, [_construct(EvidenceItem, item) for item in evidence]))

    def guard_validate_trade_plan(self, trade_plan: dict[str, Any]) -> dict[str, Any]:
        return self.call(lambda: self.guard.validate_trade_plan(_construct(TradePlan, trade_plan)))

    def guard_validate_final(self, chairman_output: dict[str, Any], risk_output: dict[str, Any],
                             claims_valid: bool, trade_plan_valid: bool) -> dict[str, Any]:
        def operation():
            risk_data = dict(risk_output)
            risk_data["trade_plan"] = _construct(TradePlan, risk_data["trade_plan"])
            return self.guard.validate_final(chairman_output, _construct(RiskResult, risk_data),
                                             bool(claims_valid), bool(trade_plan_valid))
        return self.call(operation)

    def audit_start_run(self, ticker: str, request_id: str = "") -> dict[str, Any]:
        def operation():
            ticker_value = validate_ticker(ticker)
            run_id = request_id or f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{ticker_value}_{uuid.uuid4().hex[:6]}"
            started = self.db.start_run(run_id, ticker_value, "PAPER")
            return {"run_id": run_id, "requested_at": started, "tool_server_version": TOOL_SERVER_VERSION}
        return self.call(operation)

    def audit_save_stage_output(self, run_id: str, ticker: str, stage: str,
                                payload: dict[str, Any]) -> dict[str, Any]:
        table = {"research": "research_outputs", "critic": "critic_outputs",
                 "risk": "risk_outputs", "chairman": "chairman_outputs"}.get(stage.lower())
        if not table:
            return _result(error="unsupported stage", code="INVALID_STAGE")
        return self.call(lambda: (self.db.save_output(table, run_id, validate_ticker(ticker), payload)
                                  or {"saved": True, "stage": stage}))

    def audit_complete_run(self, run_id: str, ticker: str, final_decision: str,
                           confidence: int, manifest: dict[str, Any]) -> dict[str, Any]:
        def operation():
            decision = str(final_decision)
            from .schemas import Decision
            if decision not in {item.value for item in Decision}:
                raise ValueError("unsupported final decision")
            with self.db.connect() as connection:
                connection.execute("""UPDATE analysis_runs SET finished_at=?,status='SUCCESS',
                    final_decision=?,final_confidence=? WHERE run_id=?""",
                    (now_iso(), decision, int(confidence), run_id))
                connection.execute("""INSERT INTO run_manifests VALUES(?,?,?,?)
                    ON CONFLICT(run_id) DO UPDATE SET ticker=excluded.ticker,
                    payload_json=excluded.payload_json,created_at=excluded.created_at""",
                    (run_id, validate_ticker(ticker), json.dumps(manifest, ensure_ascii=False), now_iso()))
            return {"run_id": run_id, "run_status": "SUCCESS", "final_decision": decision}
        return self.call(operation)

    def audit_fail_run(self, run_id: str, run_status: str, error: str) -> dict[str, Any]:
        allowed = {"ANALYSIS_INCOMPLETE", "DATA_STALE", "DATA_INSUFFICIENT", "SYSTEM_ERROR"}
        if run_status not in allowed:
            return _result(error="unsupported run status", code="INVALID_RUN_STATUS")
        return self.call(lambda: (self.db.fail_run(run_id, redact_secrets(error), run_status)
                                  or {"run_id": run_id, "run_status": run_status}))

    def paper_record_prediction(self, decision: dict[str, Any],
                                position_size: dict[str, Any]) -> dict[str, Any]:
        def operation():
            decision_data = dict(decision)
            decision_data["trade_plan"] = _construct(TradePlan, decision_data["trade_plan"])
            from .schemas import PositionSize
            recorded = self.paper.enter(_construct(InvestmentDecision, decision_data),
                                        _construct(PositionSize, position_size))
            return {"recorded": recorded}
        return self.call(operation)

    def paper_update_performance(self, run_id: str, entry_price: float,
                                 closes: list[float], highs: list[float], lows: list[float],
                                 stop_price: float, target_1: float, target_2: float,
                                 benchmark_returns: dict[str, float] | None = None) -> dict[str, Any]:
        """Persist a deterministic PAPER performance measurement from supplied prices."""
        def operation():
            with self.db.connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM analysis_runs WHERE run_id=?", (run_id,)
                ).fetchone()
            if not exists:
                raise ValueError("unknown run_id")
            measurement = self.paper.measure(
                float(entry_price), [float(value) for value in closes],
                [float(value) for value in highs], [float(value) for value in lows],
                float(stop_price), float(target_1), float(target_2), benchmark_returns,
            )
            self.paper.save_measurement(run_id, measurement)
            return asdict(measurement)
        return self.call(operation)

    def audit_tool_invocation(self, tool_name: str, arguments: dict[str, Any],
                              result: dict[str, Any]) -> None:
        """Best-effort audit record. Arguments are reduced and secret-redacted."""
        run_id = str(arguments.get("run_id") or arguments.get("request_id") or "UNSCOPED")
        safe_arguments: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in {"content", "payload", "research", "critic", "company_state",
                       "market_snapshot", "trade_plan", "chairman_output", "risk_output",
                       "decision", "position_size", "claims", "evidence"}:
                safe_arguments[key] = {"type": type(value).__name__, "size": len(value)}
            else:
                safe_arguments[key] = value
        try:
            self.db.log(run_id, "INFO" if result.get("ok") else "ERROR", "mcp_tool_call", {
                "tool": tool_name,
                "arguments": json.loads(redact_secrets(json.dumps(safe_arguments, default=str))),
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
                "tool_server_version": TOOL_SERVER_VERSION,
            })
        except Exception:
            pass

    def discord_publish(self, role: str, content: str) -> dict[str, Any]:
        role_name = role.lower()
        credentials = self.config["credentials"]
        legacy = credentials.get("discord_channel_id", "")
        debate = credentials.get("discord_debate_channel_id", "") or legacy
        report = credentials.get("discord_report_channel_id", "") or legacy
        mapping = {
            "research": (credentials["discord_research_token"], debate),
            "critic": (credentials["discord_critic_token"], debate),
            "chairman": (credentials["discord_chairman_token"], report),
            "error": (credentials["discord_chairman_token"], report),
        }
        if role_name not in mapping:
            return _result(error="unsupported Discord role", code="INVALID_ROLE")
        token, channel = mapping[role_name]
        return self.call(lambda: (DiscordRESTBot(token, channel).send(content)
                                  or {"published": True, "role": role_name}))
