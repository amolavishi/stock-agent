from __future__ import annotations

import json
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .command_parser import CommandInterpreter
from .paper_execution import CanonicalPaperValidator
from .position_sizing import PositionSizingEngine
from .reports import write_run_report
from .schemas import InvestmentDecision, PositionSize, RequestStatus, TradePlan, UserRequest, now_iso


class TriggerPolicy:
    def __init__(self, guild_id: str, channel_id: str, allowed_user_ids: set[str]):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.allowed_user_ids = allowed_user_ids

    def evaluate(self, guild_id: str, channel_id: str, user_id: str,
                 is_bot: bool, content: str) -> tuple[bool, str]:
        if is_bot:
            return False, "BOT_MESSAGE"
        if guild_id != self.guild_id:
            return False, "WRONG_GUILD"
        if channel_id != self.channel_id:
            return False, "WRONG_CHANNEL"
        if user_id not in self.allowed_user_ids:
            return False, "USER_NOT_ALLOWED"
        if not content.strip():
            return False, "EMPTY_MESSAGE"
        return True, "ACCEPTED"


class ClarificationManager:
    def __init__(self, database, timeout_minutes: int = 20):
        self.db = database
        self.timeout_minutes = timeout_minutes

    def create(self, request: UserRequest, channel_id: str) -> None:
        expires = datetime.now(timezone.utc) + timedelta(minutes=self.timeout_minutes)
        request.status = RequestStatus.WAITING_CLARIFICATION.value
        self.db.save_user_request(request)
        self.db.save_pending_clarification(request, channel_id, expires.isoformat())

    def prior_text(self, user_id: str, channel_id: str) -> tuple[str, str]:
        row = self.db.get_pending_clarification(
            user_id, channel_id, datetime.now(timezone.utc).isoformat())
        return (row["pending_request_id"], row["original_text"]) if row else ("", "")

    def resolve(self, pending_request_id: str) -> None:
        if pending_request_id:
            self.db.resolve_pending_clarification(pending_request_id)


class RequestDispatcher:
    def __init__(self, orchestrator, presenters=None):
        self.orchestrator = orchestrator
        self.presenters = presenters

    def execute(self, request: UserRequest) -> dict[str, Any]:
        intent = request.intent
        if intent in {"DISCOVER_MARKET", "DISCOVER_SECTOR"}:
            result = self.orchestrator.discover_request(request)
            text = (f"[Discovery] `{result.run_id}`\n"
                    f"status=`{result.status}` certification=`{result.certification_status}`\n"
                    f"coverage=`{result.coverage.market_coverage_pct:.2f}%`\n"
                    f"shortlist=`{len(result.candidates)}`")
            if result.error_code:
                text += f"\nreason=`{result.error_code}`"
            if self.presenters and hasattr(self.presenters, "publish_discovery"):
                self.presenters.publish_discovery(result)
            return {"kind": "DISCOVERY", "result": result, "text": text}
        if intent == "DISCOVERY_REPORT":
            result = self.orchestrator.discovery.latest_any()
            if not result:
                return {"kind": "TEXT", "text": "Discovery 보고서가 없습니다."}
            return {"kind": "TEXT", "text": (f"Discovery `{result['run_id']}` "
                    f"status=`{result['status']}` certification=`{result['certification_status']}`")}
        if intent == "DISCOVERY_STATUS":
            result = self.orchestrator.discovery.latest_any()
            return {"kind": "TEXT", "text": "Discovery 실행 기록이 없습니다." if not result else
                    f"Discovery `{result['run_id']}`: `{result['status']}"}
        if intent == "DISCOVERY_CANCEL":
            return {"kind": "TEXT", "text": "Discovery 취소 요청은 기록되지만, 현재 실행 중인 Discovery가 없습니다."}
        if intent in {"PAPER_BUY", "PAPER_SELL", "PAPER_TRIM"}:
            return self._execute_paper_command(request)
        if intent in {"ANALYZE", "REANALYZE"}:
            result = self.orchestrator.analyze_request(request, progress=self._progress)
            if self.presenters:
                self.presenters.publish_final(result)
            return {"kind": "ANALYSIS", "result": result}
        if intent == "COMPARE":
            parent_run = f"COMPARE_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
            label = "_vs_".join(request.tickers)
            self.orchestrator.db.start_run(parent_run, label, self.orchestrator.config["mode"],
                                           request.request_id, request.analysis_intensity)
            self.orchestrator.db.update_request_status(request.request_id, "RUNNING", parent_run)
            try:
                results = []
                for index, ticker in enumerate(request.tickers):
                    child = replace(request, request_id=str(uuid.uuid4()),
                                    discord_message_id=f"{request.discord_message_id}:{index}",
                                    intent="ANALYZE", tickers=[ticker], paper_action_enabled=False)
                    results.append(self.orchestrator.analyze_request(child, progress=self._progress))
                matrix = [self._comparison_row(item) for item in results]
                eligible = [row for row in matrix if row["eligible"] and row["composite_score"] >= 60]
                ranked = sorted(eligible, key=lambda row: row["composite_score"], reverse=True)
                preference = ranked[0]["ticker"] if ranked else "NONE"
                second = ranked[1]["ticker"] if len(ranked) > 1 else "NONE"
                lines = ["# 비교 분석 최종 결과", "", f"- WINNER: **{preference}**",
                         f"- SECOND: **{second}**", f"- Analysis Intensity: `{request.analysis_intensity}`", "",
                         "|Ticker|Decision|Signal|Catalyst|Expectation Gap|Surge Elasticity|Entry Readiness|Capital Risk|RR|Data Quality|Confidence|Composite|",
                         "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|"]
                for row in matrix:
                    lines.append("|{ticker}|{decision}|{signal_strength}|{catalyst_quality}|"
                        "{expectation_gap}|{surge_elasticity}|{entry_readiness}|"
                        "{capital_structure_risk}|{reward_risk:.2f}|{data_quality}|"
                        "{confidence}|{composite_score:.2f}|".format(**row))
                report_path = write_run_report(self.orchestrator.config["report_dir"], "\n".join(lines),
                                               label, parent_run)
                with self.orchestrator.db.connect() as connection:
                    connection.execute("""INSERT INTO report_artifacts
                        (run_id,ticker_label,markdown_path,publish_status,publish_attempts,last_error,created_at)
                        VALUES(?,?,?,?,?,?,?)""", (parent_run, label, str(report_path), "PENDING", 0, "",
                                                    datetime.now(timezone.utc).isoformat()))
                    connection.execute("""UPDATE analysis_runs SET status='SUCCESS',finished_at=?,
                        final_decision=?,final_confidence=?,delivery_status='PENDING' WHERE run_id=?""",
                        (datetime.now(timezone.utc).isoformat(), preference,
                         round(ranked[0]["composite_score"]) if ranked else 0, parent_run))
                    connection.execute("UPDATE user_requests SET status='COMPLETED',run_id=?,updated_at=? WHERE request_id=?",
                        (parent_run, datetime.now(timezone.utc).isoformat(), request.request_id))
                comparison = {"run_id": parent_run, "preference": preference, "second": second,
                              "matrix": matrix, "results": results,
                              "report_path": report_path, "request": request}
                if self.presenters:
                    self.presenters.publish_comparison(comparison)
                return {"kind": "COMPARE", "result": comparison}
            except Exception as exc:
                self.orchestrator.db.fail_run(parent_run, str(exc), "SYSTEM_ERROR")
                self.orchestrator.db.update_request_status(request.request_id, "FAILED", parent_run)
                raise
        if intent == "PRICE":
            snapshot = self.orchestrator.market_provider.snapshot(request.tickers[0])
            return {"kind": "TEXT", "text": (
                f"{snapshot.ticker}: **${snapshot.current:.2f}** "
                f"({snapshot.change_1d_pct:+.2f}%) · {snapshot.source} · {snapshot.observed_at}")}
        if intent == "PORTFOLIO":
            rows = self.orchestrator.db.portfolio_positions()
            text = "PAPER 포트폴리오에 포지션이 없습니다." if not rows else "\n".join(
                f"{row['ticker']}: {row['quantity']}주 @ ${row['average_price']:.2f}"
                for row in rows)
            return {"kind": "TEXT", "text": text}
        if intent == "REPORT":
            row = self.orchestrator.db.latest_decision(request.tickers[0])
            if not row:
                return {"kind": "TEXT", "text": "저장된 보고서가 없습니다."}
            with self.orchestrator.db.connect() as connection:
                artifact = connection.execute(
                    "SELECT markdown_path FROM report_artifacts WHERE run_id=?", (row["run_id"],)).fetchone()
            if artifact and artifact[0] and self.presenters:
                self.presenters.chairman.send_file(
                    artifact[0], f"**[저장 보고서 재게시 | {request.tickers[0]} | {row['run_id']}]**")
            return {"kind": "TEXT", "text": (
                f"{request.tickers[0]} 최신 판단을 보고서제출 채널에 재게시했습니다: "
                f"`{row['decision']}` ({row['confidence']}/100)")}
        if intent == "STATUS":
            rows = self.orchestrator.db.active_runs()
            queued = self.orchestrator.db.queued_requests()
            running_text = [f"{row['run_id']} · {row['ticker']} · {row['status']} · "
                            f"강도 {row['analysis_intensity']} · 토론 {row['debate_status']} "
                            f"{row['round_count']}R" for row in rows]
            queue_text = [f"Queue: {row['intent']} {row['tickers_json']}" for row in queued]
            text = "실행 또는 대기 중인 분석이 없습니다." if not (rows or queued) else "\n".join(
                running_text + queue_text)
            return {"kind": "TEXT", "text": text}
        if intent == "COST":
            with self.orchestrator.db.connect() as connection:
                row = connection.execute("""SELECT COUNT(*) calls,
                    COALESCE(SUM(input_tokens),0) input_tokens,
                    COALESCE(SUM(output_tokens),0) output_tokens,
                    COALESCE(SUM(reasoning_tokens),0) reasoning_tokens,
                    COALESCE(SUM(cache_read_tokens+cache_write_tokens),0) cache_tokens,
                    COALESCE(SUM(estimated_cost_usd),0) cost,
                    COALESCE(SUM(latency_ms),0) latency
                    FROM llm_calls WHERE started_at >= datetime('now','start of day')""").fetchone()
            return {"kind": "TEXT", "text": (
                "오늘 LLM 사용량\n"
                f"호출 `{row['calls']}`회 · 입력 `{row['input_tokens']}` · 출력 `{row['output_tokens']}` · "
                f"추론 `{row['reasoning_tokens']}` · 캐시 `{row['cache_tokens']}` 토큰\n"
                f"예상 비용 `${row['cost']:.6f}` · 누적 지연 `{row['latency']}ms`")}
        if intent == "CANCEL":
            return {"kind": "CANCEL", "tickers": request.tickers,
                    "text": "대기 중 요청은 취소하고, 실행 중 요청은 다음 안전 종료 지점에서 중단합니다."}
        return {"kind": "TEXT", "text": (
            "지원 요청: 분석, 비교, 재분석, 가격, PAPER 포트폴리오, 보고서, 상태, 비용, 취소, 도움말")}

    def _execute_paper_command(self, request: UserRequest) -> dict[str, Any]:
        """Apply an explicit PAPER command only from a previously certified decision."""
        ticker = request.tickers[0]
        row = self.orchestrator.db.latest_certified_decision(ticker)
        if not row:
            return {"kind": "TEXT", "text": (
                f"{ticker}: 실행 가능한 CERTIFIED 분석이 없어 PAPER 작업을 차단했습니다.")}
        payload = json.loads(row["payload_json"])
        certified_action = str(row["certified_action"])
        requested_action = {"PAPER_BUY": "BUY", "PAPER_SELL": "SELL",
                            "PAPER_TRIM": "TRIM"}[request.intent]
        compatible = ({"BUY", "CONDITIONAL_BUY"} if requested_action == "BUY"
                      else {"SELL", "TRIM", "HOLD"})
        if certified_action not in compatible:
            return {"kind": "TEXT", "text": (
                f"{ticker}: 인증된 판단 `{certified_action}`과 PAPER `{requested_action}`가 "
                "호환되지 않아 차단했습니다.")}

        validator = CanonicalPaperValidator(self.orchestrator.db)
        action_check = validator.canonicalize_action(requested_action, ticker)
        if not action_check.valid:
            return {"kind": "TEXT", "text": f"{ticker}: {','.join(action_check.reason_codes)}"}
        snapshot = self.orchestrator.market_provider.snapshot(ticker)
        if snapshot.is_mock or snapshot.data_quality != "OK" or snapshot.current <= 0:
            return {"kind": "TEXT", "text": (
                f"{ticker}: 최신 실데이터 가격이 인증되지 않아 PAPER 작업을 차단했습니다.")}
        old_plan = TradePlan(**payload["trade_plan"])
        entry = float(snapshot.current)
        if entry <= old_plan.stop_price:
            return {"kind": "TEXT", "text": f"{ticker}: 현재가가 무효화 가격 이하입니다."}
        reward = max(0.0, old_plan.target_1 - entry)
        risk = entry - old_plan.stop_price
        plan = TradePlan(entry, old_plan.preferred_price_min, old_plan.preferred_price_max,
            old_plan.stop_price, old_plan.target_1, old_plan.target_2, reward, risk,
            round(reward / risk, 2), old_plan.heuristic)
        self.orchestrator.db.update_position_mark(
            ticker, snapshot.current, snapshot.source,
            snapshot.observed_at or snapshot.timestamp, "PAPER_DEFAULT")
        account = self.orchestrator.db.paper_account_state("PAPER_DEFAULT")
        size = (PositionSizingEngine().calculate_for_account(plan, account, snapshot.sector_name)
                if requested_action == "BUY" else PositionSize(0, 0, 0, 0, "NOT_APPLICABLE"))
        decision = InvestmentDecision(ticker, now_iso(), requested_action,
            int(payload.get("confidence", 0)), "READY", plan,
            ["Explicit PAPER command backed by a certified analysis"], [],
            f"PAPER_{request.request_id}")
        effect = self.orchestrator.paper.plan_effect(decision, size, snapshot.sector_name,
                                                     request.time_horizon)
        effect["financial_operation_key"] = (
            f"discord:{request.discord_message_id}:{request.intent}:{ticker}")
        with self.orchestrator.db.connect() as connection:
            applied = self.orchestrator.db._apply_paper_effect(connection, effect)
        outcome = "적용" if applied else "이미 적용되어 중복 차단"
        return {"kind": "TEXT", "text": (
            f"{ticker} PAPER {requested_action}: {outcome} (operation key 보호됨)")}

    def _progress(self, stage: str, run_id: str, ticker: str, payload: Any) -> None:
        if self.presenters:
            self.presenters.publish_progress(stage, run_id, ticker, payload)

    @staticmethod
    def _comparison_row(item: dict[str, Any]) -> dict[str, Any]:
        research, decision, risk, market = item["research"], item["decision"], item["risk"], item["market"]
        data_score = {"OK": 100, "PARTIAL": 65, "LOW": 40}.get(market.data_quality, 0)
        rr_score = min(100.0, max(0.0, risk.reward_risk_ratio / 3 * 100))
        capital_safety = 100 - research.capital_structure_risk
        composite = (
            research.signal_strength * 0.12 + research.catalyst_quality * 0.15 +
            research.expectation_gap * 0.12 + research.surge_elasticity * 0.08 +
            research.entry_readiness * 0.12 + capital_safety * 0.12 + rr_score * 0.12 +
            data_score * 0.07 + decision.confidence * 0.10)
        return {"ticker": decision.ticker, "decision": decision.decision,
                "signal_strength": research.signal_strength,
                "catalyst_quality": research.catalyst_quality,
                "expectation_gap": research.expectation_gap,
                "surge_elasticity": research.surge_elasticity,
                "entry_readiness": research.entry_readiness,
                "capital_structure_risk": research.capital_structure_risk,
                "reward_risk": risk.reward_risk_ratio, "data_quality": market.data_quality,
                "confidence": decision.confidence, "composite_score": round(composite, 4),
                "eligible": bool(risk.hard_filter_pass and
                    decision.decision in {"BUY", "CONDITIONAL_BUY"} and data_score >= 65)}
