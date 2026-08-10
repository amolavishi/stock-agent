from __future__ import annotations

import uuid
import inspect
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone

from .agents import MockCriticAgent, MockResearchAgent
from .analysis_context import MarketRegimeContext, build_analysis_context
from .capital_structure import build_capital_structure, sector_from_sic
from .certification import CertificationEngine
from .cancellation import CancellationToken, RunCancelledError
from .claim_validation import validate_claim_evidence
from .cost_guard import CostGuard
from .database import Database
from .debate import DebateEngine
from .delta import build_fresh_delta
from .evidence import (LiveEdgarEvidenceCollector, MockEvidenceCollector,
                       detect_evidence_conflicts, normalize_evidence_request)
from .guard import FinalGuard
from .hermes import (HermesCLIAdapter, HermesHTTPAdapter, HermesCancelledError,
                     default_hermes_executable)
from .hermes_agents import (HermesChairmanAgent, HermesCriticAgent,
                            HermesResearchAgent, MockChairmanAgent)
from .knowledge import ObsidianKnowledgeManager
from .market import MockMarketDataProvider
from .market_regime import MarketRegimeEngine
from .paper import PaperPortfolio
from .position_sizing import PositionSizingEngine
from .providers import MockDiscordNotifier
from .reports import render_report, render_uncertified_report, write_run_report
from .risk import RiskEngine
from .schemas import (CompanyState, Decision, EvidenceItem, InvestmentDecision,
                      RunManifest, SideEffectStatus, UserRequest, now_iso)
from .sec import SECCompanyFactsProvider
from .security import redact_secrets
from .toss import TossClient, TossMarketDataProvider
from .trade_plan import build_heuristic_trade_plan
from .validation import AnalysisIncompleteError, validate_ticker


class Orchestrator:
    """Hybrid prototype owner: deterministic Python workflow + Hermes role calls."""

    def __init__(self, config: dict, market_provider=None, evidence_collector=None,
                 researcher=None, critic=None, notifier=None, database=None,
                 knowledge=None, chairman=None):
        self.config = config
        db_config = config.get("database", {})
        self.db = database or Database(config["database_path"],
            db_config.get("busy_timeout_ms", 5000), db_config.get("wal", True))
        obsidian = config.get("obsidian", {})
        self.knowledge = knowledge or ObsidianKnowledgeManager(
            obsidian.get("vault_path", config["vault_path"]),
            enabled=bool(obsidian.get("enabled", True)),
            companies_dir=str(obsidian.get("companies_dir", "02_Companies")),
            reports_dir=str(obsidian.get("reports_dir", "05_Reports")),
            decision_log_dir=str(obsidian.get("decision_log_dir", "06_Decision_Log")))
        self.market_provider = market_provider or self._build_market_provider()
        self.evidence_collector = evidence_collector or MockEvidenceCollector()
        self.researcher, self.critic, self.chairman = self._build_agents(researcher, critic, chairman)
        self.notifier = notifier or MockDiscordNotifier()
        self.risk = RiskEngine(config["risk_rules"])
        self.sizing = PositionSizingEngine(config["risk_rules"].get("max_position_pct", 10),
                                           config["risk_rules"].get("max_loss_pct", 0.75),
                                           config.get("paper", {}).get("max_total_exposure_pct", 60),
                                           config.get("paper", {}).get("max_sector_exposure_pct", 25))
        self.paper = PaperPortfolio(
            self.db, config.get("paper", {}).get("max_sector_exposure_pct", 25))
        self.guard = FinalGuard()
        self.cost_guard = CostGuard(config.get("cost_guard", {}))
        self.debate_engine = DebateEngine()
        self.certification = CertificationEngine()

    def _build_market_provider(self):
        provider = self.config.get("market_data_provider", self.config.get("provider", "mock"))
        if provider == "mock":
            return MockMarketDataProvider()
        if provider == "toss":
            credentials = self.config.get("credentials", {})
            return TossMarketDataProvider(TossClient(credentials.get("toss_app_key", ""),
                                                      credentials.get("toss_app_secret", "")))
        raise ValueError("unknown market data provider")

    def _build_agents(self, researcher, critic, chairman):
        if researcher or critic or chairman:
            return researcher or MockResearchAgent(), critic or MockCriticAgent(), chairman or MockChairmanAgent()
        if self.config.get("agent_provider", "mock") == "mock":
            return MockResearchAgent(), MockCriticAgent(), MockChairmanAgent()
        if self.config.get("agent_provider") != "hermes":
            raise ValueError("unknown agent provider")
        if self.config.get("hermes_transport") == "http":
            adapter = HermesHTTPAdapter(self.config["hermes_endpoint"], self.config["hermes_model"],
                                        timeout=self.config.get("hermes_timeout_seconds", 360),
                                        usage_recorder=self.db.record_llm_call)
        elif self.config.get("hermes_transport") == "cli":
            adapter = HermesCLIAdapter(default_hermes_executable(), self.config["hermes_model"],
                                       timeout=self.config.get("hermes_timeout_seconds", 360),
                                       usage_recorder=self.db.record_llm_call)
        else:
            raise ValueError("unknown Hermes transport")
        return HermesResearchAgent(adapter), HermesCriticAgent(adapter), HermesChairmanAgent(adapter)

    def init(self) -> None:
        self.db.init()
        paper = self.config.get("paper", {})
        self.db.initialize_paper_account(
            float(paper.get("initial_cash_usd", self.config.get("paper_equity_usd", 100_000))),
            str(paper.get("account_id", "PAPER_DEFAULT")),
            float(self.config["risk_rules"].get("max_loss_pct", 0.75)))

    def analyze(self, ticker: str, edgar_mode: str | None = None) -> dict:
        ticker = validate_ticker(ticker)
        request = UserRequest(str(uuid.uuid4()), "CLI", "CLI", now_iso(),
                              f"{ticker} 분석", "ANALYZE", [ticker],
                              analysis_intensity="MINIMUM", min_debate_rounds=2,
                              max_debate_rounds=2, intensity_explicit=True,
                              reasoning_profile="low", evidence_depth="CORE",
                              max_evidence_refreshes=1)
        return self.analyze_request(request, edgar_mode)

    def analyze_request(self, request: UserRequest, edgar_mode: str | None = None,
                        progress=None) -> dict:
        if len(request.tickers) != 1:
            raise ValueError("analyze_request requires exactly one ticker")
        ticker = validate_ticker(request.tickers[0])
        run_id = f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{ticker}_{uuid.uuid4().hex[:6]}"
        self.init()
        self.db.start_run(run_id, ticker, self.config["mode"], request.request_id,
                          request.analysis_intensity)
        self.db.save_user_request(request, run_id)
        self.db.update_request_status(request.request_id, "RUNNING", run_id)
        cancellation = CancellationToken(self.db, run_id)

        def emit(stage: str, payload=None):
            self.db.save_stage_event(run_id, stage, "COMPLETED", payload)
            self.db.log(run_id, "INFO", stage, payload)
            if progress:
                progress(stage, run_id, ticker, payload)

        try:
            cancellation.check("BEFORE_DATA_COLLECTION")
            emit("DATA_COLLECTION_STARTED", {"intent": request.intent})
            market = self.market_provider.snapshot(ticker)
            cancellation.check("AFTER_TOSS")
            state = self.market_provider.company_state(ticker)
            prior_state = self.db.load_company_state(ticker)
            if prior_state is not None:
                state.previous_decision = prior_state.previous_decision
            selected_edgar_mode = edgar_mode or self.config.get("edgar_mode", "mock")
            collector = (LiveEdgarEvidenceCollector(
                str(self.knowledge.root / "99_Cache" / "edgar"),
                self.config.get("credentials", {}).get("sec_user_agent", ""))
                if selected_edgar_mode == "live" else self.evidence_collector)
            evidence = collector.collect(ticker)
            cancellation.check("AFTER_SEC")
            capital_structure = None
            if selected_edgar_mode == "live":
                facts_provider = SECCompanyFactsProvider(
                    self.config.get("credentials", {}).get("sec_user_agent", ""),
                    max_rps=float(self.config.get("sec_max_rps", 4)))
                facts = facts_provider.facts(ticker)
                profile = facts_provider.company_profile(ticker)
                market.sector_name = sector_from_sic(profile.get("sic", ""))
                state.sector, state.sic = market.sector_name, profile.get("sic", "")
                state.market_cap_usd = state.market_cap_usd or self._market_cap_from_facts(facts, market.current)
                state.cash_usd = self._fact_value(facts, "cash")
                state.debt_usd = self._fact_value(facts, "debt")
                state.shares_outstanding = self._fact_value(facts, "shares_outstanding")
                state.cash_burn_usd = facts.get("derived", {}).get("cash_burn")
                state.runway_months = facts.get("derived", {}).get("estimated_runway_months")
                normalized_facts = facts.get("normalized_facts", [])
                state.companyfacts_as_of = max(
                    (str(row.get("filed") or "") for row in normalized_facts), default="")
                fact_accessions = {str(row.get("accn") or "").replace("-", "")
                                   for row in normalized_facts}
                for item in evidence:
                    if (item.document_type in {"10-Q", "10-K"} and item.raw_document_hash and
                            item.accession.replace("-", "") in fact_accessions):
                        item.lifecycle_status = "READY_FOR_ANALYSIS"
                        item.semantic_classification = "PERIODIC_FILING_XBRL_CROSS_VALIDATED"
                        item.validated_at = now_iso()
                        item.ready_for_analysis_at = item.validated_at
                        item.exhibits_resolved = True
                self.db.save_company_facts(ticker, normalized_facts)
                self.db.save_company_fact_bundle(run_id, ticker, facts)
                capital_structure = build_capital_structure(ticker, facts, evidence)
                external_atm_active = capital_structure.atm_active.value is True
                if prior_state is not None and prior_state.atm_active != external_atm_active:
                    capital_structure.integrity_conflicts.append({
                        "type": "STATE_STALENESS", "field": "atm_active",
                        "external_value": external_atm_active,
                        "internal_value": prior_state.atm_active,
                        "severity": "CRITICAL", "materiality": "MATERIAL",
                    })
                state.atm_active = external_atm_active
                self.db.save_capital_structure(run_id, ticker, capital_structure.to_dict())
            self._validate_evidence(evidence)
            if market.is_mock and selected_edgar_mode == "live":
                raise AnalysisIncompleteError("live PAPER run received mock market data")
            self.db.save_snapshot(run_id, market)
            self.db.save_evidence(evidence, run_id)
            self.db.save_evidence_conflicts(run_id, detect_evidence_conflicts(evidence))
            market_regime = self._market_regime(market)
            request_payload = asdict(request)
            prior_analysis = self.db.latest_certified_run(ticker)
            try:
                persistent_knowledge = self.knowledge.load_context(ticker, request.focus)
            except Exception as exc:
                persistent_knowledge = {}
                self.db.log(run_id, "WARNING", "KNOWLEDGE_CONTEXT_LOAD_FAILED",
                            {"error": redact_secrets(exc)})
            fresh_delta = build_fresh_delta(
                prior_analysis, market, evidence, asdict(market_regime), state.companyfacts_as_of)
            analysis_context = build_analysis_context(
                request, market, state, evidence, market_regime,
                persistent_knowledge=persistent_knowledge, fresh_delta=fresh_delta,
                prior_analysis=prior_analysis or {})

            context_box = {"value": analysis_context.to_dict()}

            def context_evidence_ids(round_context: dict) -> list[str]:
                canonical = round_context.get("canonical_analysis_context", {})
                return [str(item.get("evidence_id")) for item in canonical.get("evidence_index", [])
                        if item.get("evidence_id")]

            def research_call(round_no: int, round_context: dict, phase: str):
                cancellation.check("BEFORE_RESEARCH")
                emit("RESEARCH_STARTED", {"round": round_no, "phase": phase})
                self.db.mark_evidence_seen(run_id, context_evidence_ids(round_context),
                                           "RESEARCH", round_no)
                self._set_agent_context(self.researcher, run_id, request, ticker, round_no,
                                        f"{phase}_RESEARCH", cancellation.requested)
                output = self._run_research(state, market, evidence, request_payload,
                                            context_box["value"], round_context)
                validate_claim_evidence(output.claims, evidence, self._minimum_claims(request))
                self.db.save_output("research_outputs", run_id, ticker, output)
                self.db.save_stage_output(run_id, ticker, "research", round_no, phase, output)
                emit("RESEARCH_COMPLETED", {"round": round_no, "phase": phase,
                                             "output": asdict(output)})
                cancellation.check("AFTER_RESEARCH")
                return output

            def critic_call(round_no: int, research_output, round_context: dict, phase: str):
                cancellation.check("BEFORE_CRITIC")
                emit("CRITIC_STARTED", {"round": round_no, "phase": phase})
                self.db.mark_evidence_seen(run_id, context_evidence_ids(round_context),
                                           "CRITIC", round_no)
                self._set_agent_context(self.critic, run_id, request, ticker, round_no,
                                        f"{phase}_CRITIC", cancellation.requested)
                output = self._run_critic(research_output, state, market, evidence,
                                          request_payload, context_box["value"], round_context)
                self.db.save_output("critic_outputs", run_id, ticker, output)
                self.db.save_stage_output(run_id, ticker, "critic", round_no, phase, output)
                emit("CRITIC_COMPLETED", {"round": round_no, "phase": phase,
                                           "output": asdict(output)})
                cancellation.check("AFTER_CRITIC")
                return output

            def refresh_call(raw_requests: list[dict], round_no: int):
                nonlocal evidence
                cancellation.check("BEFORE_EVIDENCE_REFRESH")
                emit("EVIDENCE_REFRESH", {"round": round_no, "requests": raw_requests[:5]})
                refreshed = []
                for raw_request in raw_requests[:5]:
                    request_item = normalize_evidence_request(raw_request, round_no)
                    self.db.save_evidence_request(run_id, request_item, "OPEN")
                    self.db.save_evidence_request(run_id, request_item, "COLLECTING")
                    try:
                        if hasattr(collector, "collect_for_request"):
                            collected = collector.collect_for_request(ticker, request_item)
                        else:
                            collected = collector.collect(ticker)
                    except Exception:
                        self.db.save_evidence_request(run_id, request_item, "FAILED")
                        raise
                    refreshed.extend(collected)
                    self.db.save_evidence_request(run_id, request_item, "COLLECTED",
                                                  [item.evidence_id for item in collected])
                    self.db.save_evidence_request(run_id, request_item, "REVIEW_REQUIRED",
                                                  [item.evidence_id for item in collected])
                evidence = list({item.evidence_id: item for item in evidence + refreshed}.values())
                self._validate_evidence(evidence)
                self.db.save_evidence(evidence, run_id)
                self.db.save_evidence_conflicts(run_id, detect_evidence_conflicts(evidence))
                context_box["value"] = build_analysis_context(
                    request, market, state, evidence, market_regime,
                    persistent_knowledge=persistent_knowledge, fresh_delta=fresh_delta,
                    prior_analysis=prior_analysis or {}).to_dict()
                return context_box["value"]

            def persist_debate(debate_state, research_output, critic_output,
                               consensus, phase: str):
                self.db.save_debate_round(debate_state, research_output, critic_output,
                                          consensus, phase)

            def debate_progress(stage: str, payload: dict):
                emit(stage, payload)

            def cost_check(round_no: int) -> str:
                summary = self.db.usage_summary(run_id)
                decision = self.cost_guard.evaluate(
                    summary["estimated_cost_usd"], round_no >= request.min_debate_rounds)
                if decision.action == "WARN":
                    emit("COST_WARNING", asdict(decision))
                return decision.action

            research, critic, debate_state, consensus_result = self.debate_engine.run(
                run_id, request, context_box["value"], research_call, critic_call,
                refresh_call, persist_debate, debate_progress, cost_check,
                lambda: self.db.unresolved_must_answer_count(run_id))
            debate_rounds = debate_state.round_no

            capital_payload = capital_structure.to_dict() if capital_structure else {}
            certification = self.certification.evaluate(
                run_id=run_id,
                debate_status=debate_state.status,
                market=market,
                evidence=evidence,
                capital_structure=capital_payload,
                live_mode=selected_edgar_mode == "live",
                critical_open_issues=debate_state.critical_open_issue_count,
                unresolved_must_answer=self.db.unresolved_must_answer_count(run_id),
                claim_validation_passed=True,
                system_integrity_ok=True,
                sizing_requested=False,
            )
            emit("CERTIFICATION_EVALUATED", asdict(certification))
            if not certification.certified:
                self._record_usage(run_id, ticker)
                usage = self.db.usage_summary(run_id)
                started_at = self.db.get_run(run_id)["started_at"]
                manifest = RunManifest(
                    run_id, ticker, market.snapshot_id,
                    [item.evidence_id for item in evidence], state.last_updated,
                    research.prompt_version, critic.prompt_version,
                    getattr(self.chairman, "prompt_version", "unknown"),
                    self.config["risk_rules"].get("version", "risk_rules_v0.2"),
                    research.provider, research.model, started_at, now_iso(),
                    certification.action, analysis_intensity=request.analysis_intensity,
                    market_as_of=market.observed_at,
                    evidence_cutoff=max((item.filed_at or item.published_at for item in evidence), default=""),
                    companyfacts_as_of=state.companyfacts_as_of,
                    debate_status=debate_state.status, round_count=debate_rounds,
                    input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                    reasoning_tokens=usage["reasoning_tokens"],
                    estimated_cost_usd=usage["estimated_cost_usd"],
                    total_latency_ms=usage["latency_ms"], prompt_hashes=self._prompt_hashes(),
                    risk_config_hash=hashlib.sha256(json.dumps(
                        self.config["risk_rules"], sort_keys=True).encode()).hexdigest())
                report = render_uncertified_report(
                    run_id, ticker, certification, request_payload, market=market,
                    debate_state=debate_state, evidence=evidence, usage=usage)
                if self.config.get("report_dir"):
                    report_path = write_run_report(self.config["report_dir"], report, ticker, run_id)
                else:
                    report_path = self.knowledge.write_report(ticker, run_id, report)
                cancellation.check("BEFORE_FINAL_PERSIST")
                self.db.finalize_uncertified_analysis(
                    certification, manifest, research, critic, request.request_id, ticker,
                    str(report_path), debate_state.status, debate_rounds, usage)
                self.db.record_knowledge_sync(
                    run_id, ticker, "BLOCKED_CERTIFICATION", str(self.knowledge.root),
                    ",".join(certification.reason_codes))
                emit("RUN_UNCERTIFIED", {
                    "action": certification.action,
                    "certification_status": certification.certification_status,
                    "report": str(report_path),
                })
                self.notifier.send(
                    f"[{certification.action}] {ticker} certification="
                    f"{certification.certification_status} run_id={run_id}")
                return {
                    "run_id": run_id, "market": market, "state": state,
                    "evidence": evidence, "research": research, "critic": critic,
                    "risk": None, "chairman": None, "position_size": None,
                    "manifest": manifest, "decision": None, "report_path": report_path,
                    "market_regime": market_regime.regime,
                    "market_regime_context": market_regime,
                    "debate_rounds": debate_rounds, "debate_state": debate_state,
                    "consensus_result": consensus_result, "final_guard": None,
                    "certification": certification, "request": request,
                }

            trade_plan = build_heuristic_trade_plan(market)
            risk = self.risk.evaluate(research, critic, state, market, trade_plan)
            self.db.save_output("risk_outputs", run_id, ticker, risk)
            emit("RISK_COMPLETED", asdict(risk))
            account_id = str(self.config.get("paper", {}).get("account_id", "PAPER_DEFAULT"))
            self.db.update_position_mark(
                ticker, market.current, market.source,
                market.observed_at or market.timestamp, account_id)
            account = self.db.paper_account_state(account_id)
            position_size = self.sizing.calculate_for_account(trade_plan, account, market.sector_name)
            emit("POSITION_SIZING", asdict(position_size))

            self._set_agent_context(self.chairman, run_id, request, ticker, debate_rounds,
                                    "CHAIRMAN", cancellation.requested)
            cancellation.check("BEFORE_CHAIRMAN")
            chairman_output = self._run_chairman(research, critic, risk, request_payload, position_size)
            self.db.mark_evidence_seen(run_id, list(dict.fromkeys(research.evidence_ids)),
                                       "CHAIRMAN", debate_rounds)
            self.db.save_output("chairman_outputs", run_id, ticker, chairman_output)
            emit("CHAIRMAN_COMPLETED", chairman_output)
            claim_guard = self.guard.validate_claims(research.claims, evidence)
            plan_guard = self.guard.validate_trade_plan(trade_plan)
            critical_capital_unknown = bool(capital_structure and
                capital_structure.shares_outstanding is None)
            with self.db.connect() as connection:
                has_open_position = connection.execute("""SELECT 1 FROM portfolio_positions
                    WHERE ticker=? AND account_id=? AND status='OPEN' AND quantity>0 LIMIT 1""",
                    (ticker, account_id)).fetchone() is not None
            final_guard = self.guard.validate_final(chairman_output, risk,
                claim_guard["valid"], plan_guard["valid"], debate_state.status,
                debate_state.critical_open_issue_count, market.data_quality,
                critical_capital_unknown, has_open_position)
            decision_name = "WAIT" if market.is_mock else final_guard["final_decision"]
            emit("FINAL_GUARD_COMPLETED", final_guard)
            # Certification is a boundary decision, not a mutable pre-chairman label. Re-run
            # the complete contract after Risk, Chairman, claims, sizing, and FinalGuard exist.
            final_certification = self.certification.evaluate(
                run_id=run_id,
                debate_status=debate_state.status,
                market=market,
                evidence=evidence,
                capital_structure=capital_payload,
                live_mode=selected_edgar_mode == "live",
                critical_open_issues=debate_state.critical_open_issue_count,
                unresolved_must_answer=self.db.unresolved_must_answer_count(run_id),
                claim_validation_passed=claim_guard["valid"],
                system_integrity_ok=True,
                sizing_requested=request.paper_action_enabled,
                portfolio_state=account,
                final_boundary_failures=final_guard["errors"],
                final_decision=final_guard["final_decision"],
                risk_hard_filter_pass=risk.hard_filter_pass,
                risk_decision=risk.risk_decision,
            )
            certification = final_certification
            emit("FINAL_CERTIFICATION_EVALUATED", asdict(certification))
            if not certification.certified:
                self._record_usage(run_id, ticker)
                usage = self.db.usage_summary(run_id)
                started_at = self.db.get_run(run_id)["started_at"]
                manifest = RunManifest(
                    run_id, ticker, market.snapshot_id,
                    [item.evidence_id for item in evidence], state.last_updated,
                    research.prompt_version, critic.prompt_version,
                    getattr(self.chairman, "prompt_version", "unknown"), risk.rule_version,
                    research.provider, research.model, started_at, now_iso(),
                    certification.action, analysis_intensity=request.analysis_intensity,
                    market_as_of=market.observed_at,
                    evidence_cutoff=max((item.filed_at or item.published_at for item in evidence), default=""),
                    companyfacts_as_of=state.companyfacts_as_of,
                    debate_status=debate_state.status, round_count=debate_rounds,
                    input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                    reasoning_tokens=usage["reasoning_tokens"],
                    estimated_cost_usd=usage["estimated_cost_usd"],
                    total_latency_ms=usage["latency_ms"], prompt_hashes=self._prompt_hashes(),
                    risk_config_hash=hashlib.sha256(json.dumps(
                        self.config["risk_rules"], sort_keys=True).encode()).hexdigest())
                report = render_uncertified_report(
                    run_id, ticker, certification, request_payload, market=market,
                    debate_state=debate_state, evidence=evidence, usage=usage)
                if self.config.get("report_dir"):
                    report_path = write_run_report(self.config["report_dir"], report, ticker, run_id)
                else:
                    report_path = self.knowledge.write_report(ticker, run_id, report)
                cancellation.check("BEFORE_FINAL_PERSIST")
                self.db.finalize_uncertified_analysis(
                    certification, manifest, research, critic, request.request_id, ticker,
                    str(report_path), debate_state.status, debate_rounds, usage)
                self.db.record_knowledge_sync(
                    run_id, ticker, "BLOCKED_CERTIFICATION", str(self.knowledge.root),
                    ",".join(certification.reason_codes))
                emit("RUN_UNCERTIFIED", {"action": certification.action,
                                          "certification_status": certification.certification_status,
                                          "report": str(report_path)})
                return {"run_id": run_id, "market": market, "state": state,
                        "evidence": evidence, "research": research, "critic": critic,
                        "risk": risk, "chairman": chairman_output, "position_size": None,
                        "manifest": manifest, "decision": None, "report_path": report_path,
                        "market_regime": market_regime.regime,
                        "market_regime_context": market_regime, "debate_rounds": debate_rounds,
                        "debate_state": debate_state, "consensus_result": consensus_result,
                        "final_guard": final_guard, "certification": certification,
                        "request": request}
            raw_confidence = max(0, min(100, round((research.confidence + critic.confidence) / 2
                                                   - len(risk.warnings) * 3)))
            confidence_cap = self._confidence_cap(
                market.data_quality, market_regime.regime_confidence,
                critical_capital_unknown, debate_state.status)
            confidence = min(raw_confidence, confidence_cap)
            decision = InvestmentDecision(
                ticker, now_iso(), decision_name, confidence,
                "READY" if decision_name in {"BUY", "CONDITIONAL_BUY"} else "NOT_READY",
                trade_plan,
                ["MOCK 시나리오 신호" if market.is_mock else "실데이터·근거 기반 최근 사업 신호",
                 f"20D 수익률 {market.return_20d_pct:+.2f}% / 상대거래량 {market.relative_volume:.2f}x",
                 f"전략 적합도 {research.strategy_fit}/100"],
                state.known_risks[:3] + risk.warnings[:2], run_id)
            exported_position_size = (position_size
                                      if decision_name in {"BUY", "CONDITIONAL_BUY"} else None)
            started_at = self.db.get_run(run_id)["started_at"]
            self._record_usage(run_id, ticker)
            usage = self.db.usage_summary(run_id)
            manifest = RunManifest(run_id, ticker, market.snapshot_id,
                [item.evidence_id for item in evidence], state.last_updated,
                research.prompt_version, critic.prompt_version,
                getattr(self.chairman, "prompt_version", "unknown"), risk.rule_version,
                research.provider, research.model, started_at, now_iso(), decision_name,
                analysis_intensity=request.analysis_intensity,
                market_as_of=market.observed_at,
                evidence_cutoff=max((item.filed_at or item.published_at for item in evidence), default=""),
                companyfacts_as_of=state.companyfacts_as_of,
                debate_status=debate_state.status, round_count=debate_rounds,
                input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                reasoning_tokens=usage["reasoning_tokens"],
                estimated_cost_usd=usage["estimated_cost_usd"],
                total_latency_ms=usage["latency_ms"],
                prompt_hashes=self._prompt_hashes(),
                risk_config_hash=hashlib.sha256(json.dumps(
                    self.config["risk_rules"], sort_keys=True).encode()).hexdigest())
            state.previous_decision = decision_name
            state.last_updated = datetime.now(timezone.utc).date().isoformat()
            report = render_report(run_id, market, state, evidence, research, critic, risk,
                decision, request_payload, market_regime.regime, chairman_output,
                exported_position_size,
                debate_state=debate_state, usage=usage, fresh_delta=fresh_delta,
                capital_structure=capital_structure.to_dict() if capital_structure else {},
                certification=certification)
            if self.config.get("report_dir"):
                report_path = write_run_report(self.config["report_dir"], report, ticker, run_id)
            else:
                report_path = self.knowledge.write_report(ticker, run_id, report)
            paper_effect = self.paper.plan_effect(
                decision, position_size, market.sector_name, request.time_horizon)
            if not request.paper_action_enabled:
                paper_effect = {}
            cancellation.check("BEFORE_FINAL_PERSIST")
            self.db.finalize_analysis(decision, manifest, state, research, critic,
                risk.rule_version, request.request_id, str(report_path), paper_effect,
                debate_state.status, debate_rounds, usage, certification)
            try:
                self.knowledge.sync_run(ticker, run_id, state, evidence, research, decision,
                                        debate_state, report_path,
                                        certification_status=certification.certification_status)
                self.db.record_knowledge_sync(run_id, ticker, "SUCCESS", str(self.knowledge.root))
            except Exception as exc:
                safe_knowledge_error = redact_secrets(exc)
                self.db.record_knowledge_sync(run_id, ticker, "FAILED", str(self.knowledge.root),
                                              safe_knowledge_error)
                self.db.log(run_id, "WARNING", "KNOWLEDGE_SYNC_FAILED",
                            {"error": safe_knowledge_error})
            emit("RUN_SUCCESS", {"decision": decision_name, "report": str(report_path)})
            self.notifier.send(f"[{decision_name}] {ticker} confidence={confidence}/100 run_id={run_id}")
            return {"run_id": run_id, "market": market, "state": state, "evidence": evidence,
                    "research": research, "critic": critic, "risk": risk,
                    "chairman": chairman_output, "position_size": exported_position_size,
                    "manifest": manifest, "decision": decision, "report_path": report_path,
                    "market_regime": market_regime.regime, "market_regime_context": market_regime,
                    "debate_rounds": debate_rounds, "debate_state": debate_state,
                    "consensus_result": consensus_result,
                    "final_guard": final_guard, "certification": certification,
                    "request": request}
        except (RunCancelledError, HermesCancelledError) as exc:
            self.db.acknowledge_cancellation(run_id)
            self.db.update_request_status(request.request_id, "CANCELLED", run_id)
            self.db.log(run_id, "INFO", "RUN_CANCELLED", {"error": str(exc)})
            if progress:
                progress("RUN_CANCELLED", run_id, ticker, {"status": "CANCELLED"})
            raise
        except Exception as exc:
            safe_error = redact_secrets(exc)
            status = "DATA_INSUFFICIENT" if isinstance(exc, AnalysisIncompleteError) else "SYSTEM_ERROR"
            self.db.fail_run(run_id, safe_error, status)
            self.db.update_request_status(request.request_id, "FAILED", run_id)
            self.db.log(run_id, "ERROR", "RUN_FAILED", {"error": safe_error, "status": status})
            if progress:
                progress("RUN_FAILED", run_id, ticker, {"status": status, "error": safe_error})
            raise

    def _market_regime(self, ticker_snapshot=None) -> MarketRegimeContext:
        try:
            snapshots = {ticker: self.market_provider.snapshot(ticker)
                         for ticker in ("QQQ", "IWM", "SOXX")}
            return MarketRegimeEngine().context(snapshots, ticker_snapshot)
        except Exception:
            return MarketRegimeContext("UNKNOWN", now_iso(),
                {"QQQ": None, "IWM": None, "SOXX": None},
                {"QQQ": None, "IWM": None, "SOXX": None}, "UNKNOWN", 0)

    def _run_research(self, state, market, evidence, request, analysis_context=None, revision=None):
        for args in ((state, market, evidence, request, revision, analysis_context),
                     (state, market, evidence, request, revision),
                     (state, market, evidence)):
            try:
                inspect.signature(self.researcher.run).bind(*args)
            except TypeError:
                continue
            return self.researcher.run(*args)
        raise TypeError("unsupported Research agent signature")

    def _run_critic(self, research, state, market, evidence, request, analysis_context=None,
                    debate_context=None):
        for args in ((research, state, market, evidence, request, analysis_context, debate_context),
                     (research, state, market, evidence, request, analysis_context),
                     (research, state, market, evidence, request),
                     (research, state, market)):
            try:
                inspect.signature(self.critic.run).bind(*args)
            except TypeError:
                continue
            return self.critic.run(*args)
        raise TypeError("unsupported Critic agent signature")

    @staticmethod
    def _minimum_claims(request: UserRequest) -> int:
        return {"MINIMUM": 3, "NORMAL": 5, "MAXIMUM": 7}.get(
            request.analysis_intensity, 5)

    def _run_chairman(self, research, critic, risk, request, position_size):
        try:
            inspect.signature(self.chairman.run).bind(research, critic, risk, request, position_size)
        except TypeError:
            return self.chairman.run(research, critic, risk)
        return self.chairman.run(research, critic, risk, request, position_size)

    def _record_usage(self, run_id: str, ticker: str) -> None:
        # Canonical Hermes adapters record every invocation (including repairs) immediately.
        # Kept as a compatibility hook for non-Hermes test agents.
        return None

    @staticmethod
    def _prompt_hashes() -> dict[str, str]:
        root = __import__("pathlib").Path(__file__).resolve().parents[1] / "prompts"
        result = {}
        for path in sorted(root.glob("*.md")):
            result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    @staticmethod
    def _set_agent_context(agent, run_id: str, request: UserRequest, ticker: str,
                           round_no: int, phase: str, cancellation_check=None) -> None:
        adapter = getattr(agent, "adapter", None)
        setter = getattr(adapter, "set_call_context", None)
        if setter:
            setter(run_id=run_id, request_id=request.request_id, ticker=ticker,
                   round_no=round_no, phase=phase,
                   reasoning_effort=request.reasoning_profile, repair_attempt=False,
                   cancellation_check=cancellation_check)

    def _validate_evidence(self, evidence: list[EvidenceItem]) -> None:
        max_age = self.config["analysis"]["max_evidence_age_days"]
        now = datetime.now(timezone.utc)
        usable = 0
        for item in evidence:
            published = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if (now - published.astimezone(timezone.utc)).days > max_age:
                item.data_quality = "STALE"
            else:
                usable += 1
        required = self.config["analysis"]["min_evidence"]
        if usable < required:
            raise AnalysisIncompleteError(f"usable evidence {usable} is below minimum {required}")

    @staticmethod
    def _market_cap_from_facts(facts: dict, price: float) -> float:
        row = facts.get("shares_outstanding") or {}
        try:
            return float(row.get("value", row.get("val", 0))) * price
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _fact_value(facts: dict, name: str) -> float | None:
        row = facts.get(name) or {}
        try:
            value = row.get("value", row.get("val"))
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _confidence_cap(data_quality: str, regime_confidence: int,
                        critical_capital_unknown: bool, debate_status: str) -> int:
        cap = 100
        if data_quality == "PARTIAL":
            cap = min(cap, 80)
        elif data_quality != "OK":
            cap = min(cap, 55)
        if regime_confidence < 50:
            cap = min(cap, 80)
        if critical_capital_unknown:
            cap = min(cap, 70)
        if debate_status == "DEADLOCK":
            cap = min(cap, 65)
        return cap

    @staticmethod
    def _final_decision(research, critic, risk, is_mock: bool, chairman: dict | None = None) -> str:
        if not risk.hard_filter_pass:
            return "EXCLUDE"
        if risk.risk_decision in {"WAIT", "EXCLUDE"} or critic.critic_decision == "WAIT":
            return risk.risk_decision if risk.risk_decision == "EXCLUDE" else "WAIT"
        if is_mock:
            return "WAIT"
        proposed = (chairman or {}).get("decision", research.suggested_decision)
        allowed = {item.value for item in Decision}
        return proposed if proposed in allowed else "WAIT"
