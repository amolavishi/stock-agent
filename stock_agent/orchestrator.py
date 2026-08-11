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
                       company_facts_evidence, detect_evidence_conflicts,
                       market_snapshot_evidence,
                       normalize_evidence_request)
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
from .discovery.orchestrator import DiscoveryOrchestrator
from .discovery.providers_live import (SECCompanyTickerSecurityMasterProvider,
                                        TossDiscoveryMarketDataProvider)


class Orchestrator:
    """Hybrid prototype owner: deterministic Python workflow + Hermes role calls."""

    def __init__(self, config: dict, market_provider=None, evidence_collector=None,
                 researcher=None, critic=None, notifier=None, database=None,
                 knowledge=None, chairman=None, discovery_security_master=None,
                 discovery_market_data=None, discovery_fundamental_provider=None):
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
        if discovery_market_data is None and isinstance(self.market_provider, TossMarketDataProvider):
            discovery_market_data = TossDiscoveryMarketDataProvider(self.market_provider.client)
        if discovery_security_master is None and config.get("discovery", {}).get("enabled", False):
            user_agent = config.get("credentials", {}).get("sec_user_agent", "")
            if user_agent:
                discovery_security_master = SECCompanyTickerSecurityMasterProvider(
                    user_agent, self.knowledge.root / "99_Cache" / "discovery" / "company_tickers_exchange.json")
        self.discovery = DiscoveryOrchestrator(
            self.db, config, security_master=discovery_security_master,
            market_data=discovery_market_data,
            fundamental_provider=discovery_fundamental_provider,
            handoff=self.analyze_request)

    def discover_request(self, request: UserRequest):
        """Run the additive deterministic Discovery layer.

        Discovery is disabled by default and remains shadow/read-only until the
        operator explicitly enables it in config.  It never mutates PAPER.
        """
        discovery_config = self.config.get("discovery", {})
        if not discovery_config.get("enabled", False):
            result = self.discovery._empty_result(
                self.discovery._context_for_disabled(request), "BLOCKED_DATA", "DISCOVERY_DISABLED")
            self.discovery.store.save_run(result, now_iso(), now_iso())
            return result
        mode = request.discovery_mode or ("SECTOR" if request.requested_sector else "MARKET")
        return self.discovery.run(
            mode=mode, requested_sector=request.requested_sector,
            intensity=request.analysis_intensity,
            shadow=bool(request.shadow or discovery_config.get("shadow_mode", True)),
            request_id=request.request_id, request=request)

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
                              f"{ticker} ë¶„ì„", "ANALYZE", [ticker],
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
                evidence.append(company_facts_evidence(ticker, facts))
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
                evidence.append(market_snapshot_evidence(market))
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
            except Exception as exc:ço9¶‰žËkºwµç}Ñ½­•¹ÌõÕÍ…•l‰½ÕÑÁÕÑ}Ñ½­•¹Ì‰t°(€€€€€€€€€€€€€€€€€€€É•…Í½¹¥¹}Ñ½­•¹ÌõÕÍ…•l‰É•…Í½¹¥¹}Ñ½­•¹Ì‰t°(€€€€€€€€€€€€€€€€€€€•ÍÑ¥µ…Ñ•‘}½ÍÑ}ÕÍõÕÍ…•l‰•ÍÑ¥µ…Ñ•‘}½ÍÑ}ÕÍ‰t°(€€€€€€€€€€€€€€€€€€€Ñ½Ñ…±}±…Ñ•¹å}µÌõÕÍ…•l‰±…Ñ•¹å}µÌ‰t°ÁÉ½µÁÑ}¡…Í¡•ÌõÍ•±˜¹}ÁÉ½µÁÑ}¡…Í¡•Ì ¤°(€€€€€€€€€€€€€€€€€€€É¥Í­}½¹™¥}¡…Í õ¡…Í¡±¥ˆ¹Í¡„ÈÔØ¡©Í½¸¹‘ÕµÁÌ (€€€€€€€€€€€€€€€€€€€€€€€Í•±˜¹½¹™¥l‰É¥Í­}ÉÕ±•Ì‰t°Í½ÉÑ}­•åÌõQÉÕ”¤¹•¹½‘” ¤¤¹¡•á‘¥•ÍÐ ¤¤(€€€€€€€€€€€€€€€É•Á½ÉÐ€ôÉ•¹‘•É}Õ¹•ÉÑ¥™¥•‘}É•Á½ÉÐ (€€€€€€€€€€€€€€€€€€€ÉÕ¹}¥°Ñ¥­•È°•ÉÑ¥™¥…Ñ¥½¸°É•ÅÕ•ÍÑ}Á…å±½…°µ…É­•Ðõµ…É­•Ð°(€€€€€€€€€€€€€€€€€€€‘•‰…Ñ•}ÍÑ…Ñ”õ‘•‰…Ñ•}ÍÑ…Ñ”°•Ù¥‘•¹”õ•Ù¥‘•¹”°ÕÍ…”õÕÍ…”¤(€€€€€€€€€€€€€€€¥˜Í•±˜¹½¹™¥œ¹•Ð ‰É•Á½ÉÑ}‘¥Èˆ¤è(€€€€€€€€€€€€€€€€€€€É•Á½ÉÑ}Á…Ñ €ôÝÉ¥Ñ•}ÉÕ¹}É•Á½ÉÐ¡Í•±˜¹½¹™¥l‰É•Á½ÉÑ}‘¥È‰t°É•Á½ÉÐ°Ñ¥­•È°ÉÕ¹}¥¤(€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€É•Á½ÉÑ}Á…Ñ €ôÍ•±˜¹­¹½Ý±•‘”¹ÝÉ¥Ñ•}É•Á½ÉÐ¡Ñ¥­•È°ÉÕ¹}¥°É•Á½ÉÐ¤(€€€€€€€€€€€€€€€…¹•±±…Ñ¥½¸¹¡•¬ ‰	=I}%91}AIM%MPˆ¤(€€€€€€€€€€€€€€€Í•±˜¹‘ˆ¹™¥¹…±¥é•}Õ¹•ÉÑ¥™¥•‘}…¹…±åÍ¥Ì (€€€€€€€€€€€€€€€€€€€•ÉÑ¥™¥…Ñ¥½¸°µ…¹¥™•ÍÐ°É•Í•…É °É¥Ñ¥Œ°É•ÅÕ•ÍÐ¹É•ÅÕ•ÍÑ}¥°Ñ¥­•È°(€€€€€€€€€€€€€€€€€€€ÍÑÈ¡É•Á½ÉÑ}Á…Ñ ¤°‘•‰…Ñ•}ÍÑ…Ñ”¹ÍÑ…ÑÕÌ°‘•‰…Ñ•}É½Õ¹‘Ì°ÕÍ…”¤(€€€€€€€€€€€€€€€Í•±˜¹‘ˆ¹É•½É‘}­¹½Ý±•‘•}Íå¹Œ (€€€€€€€€€€€€€€€€€€€ÉÕ¹}¥°Ñ¥­•È°€‰	1=-}IQ%%Q%=8ˆ°ÍÑÈ¡Í•±˜¹­¹½Ý±•‘”¹É½½Ð¤°(€€€€€€€€€€€€€€€€€€€€ˆ°ˆ¹©½¥¸¡•ÉÑ¥™¥…Ñ¥½¸¹É•…Í½¹}½‘•Ì¤¤(€€€€€€€€€€€€€€€•µ¥Ð ‰IU9}U9IQ%%ˆ°ì‰…Ñ¥½¸ˆè•ÉÑ¥™¥…Ñ¥½¸¹…Ñ¥½¸°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰•ÉÑ¥™¥…Ñ¥½¹}ÍÑ…ÑÕÌˆè•ÉÑ¥™¥…Ñ¥½¸¹•ÉÑ¥™¥…Ñ¥½¹}ÍÑ…ÑÕÌ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰É•Á½ÉÐˆèÍÑÈ¡É•Á½ÉÑ}Á…Ñ ¥ô¤(€€€€€€€€€€€€€€€É•ÑÕÉ¸ì‰ÉÕ¹}¥ˆèÉÕ¹}¥°€‰µ…É­•Ðˆèµ…É­•Ð°€‰ÍÑ…Ñ”ˆèÍÑ…Ñ”°(€€€€€€€€€€€€€€€€€€€€€€€€‰•Ù¥‘•¹”ˆè•Ù¥‘•¹”°€‰É•Í•…É ˆèÉ•Í•…É °€‰É¥Ñ¥ŒˆèÉ¥Ñ¥Œ°(€€€€€€€€€€€€€€€€€€€€€€€€‰É¥Í¬ˆèÉ¥Í¬°€‰¡…¥Éµ…¸ˆè¡…¥Éµ…¹}½ÕÑÁÕÐ°€‰Á½Í¥Ñ¥½¹}Í¥é”ˆè9½¹”°(€€€€€€€€€€€€€€€€€€€€€€€€‰µ…¹¥™•ÍÐˆèµ…¹¥™•ÍÐ°€‰‘•¥Í¥½¸ˆè9½¹”°€‰É•Á½ÉÑ}Á…Ñ ˆèÉ•Á½ÉÑ}Á…Ñ °(€€€€€€€€€€€€€€€€€€€€€€€€‰µ…É­•Ñ}É•¥µ”ˆèµ…É­•Ñ}É•¥µ”¹É•¥µ”°(€€€€€€€€€€€€€€€€€€€€€€€€‰µ…É­•Ñ}É•¥µ•}½¹Ñ•áÐˆèµ…É­•Ñ}É•¥µ”°€‰‘•‰…Ñ•}É½Õ¹‘Ìˆè‘•‰…Ñ•}É½Õ¹‘Ì°(€€€€€€€€€€€€€€€€€€€€€€€€‰‘•‰…Ñ•}ÍÑ…Ñ”ˆè‘•‰…Ñ•}ÍÑ…Ñ”°€‰½¹Í•¹ÍÕÍ}É•ÍÕ±Ðˆè½¹Í•¹ÍÕÍ}É•ÍÕ±Ð°(€€€€€€€€€€€€€€€€€€€€€€€€‰™¥¹…±}Õ…Éˆè™¥¹…±}Õ…É°€‰•ÉÑ¥™¥…Ñ¥½¸ˆè•ÉÑ¥™¥…Ñ¥½¸°(€€€€€€€€€€€€€€€€€€€€€€€€‰É•ÅÕ•ÍÐˆèÉ•ÅÕ•ÍÑô(€€€€€€€€€€€É…Ý}½¹™¥‘•¹”€ôµ…à À°µ¥¸ ÄÀÀ°É½Õ¹ ¡É•Í•…É ¹½¹™¥‘•¹”€¬É¥Ñ¥Œ¹½¹™¥‘•¹”¤€¼€È(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€´±•¸¡É¥Í¬¹Ý…É¹¥¹Ì¤€¨€Ì¤¤¤(€€€€€€€€€€€½¹™¥‘•¹•}…À€ôÍ•±˜¹}½¹™¥‘•¹•}…À 4(€€€€€€€€€€€€€€€µ…É­•Ð¹‘…Ñ…}ÅÕ…±¥Ñä°µ…É­•Ñ}É•¥µ”¹É•¥µ•}½¹™¥‘•¹”°4(€€€€€€€€€€€€€€€É¥Ñ¥…±}…Á¥Ñ…±}Õ¹­¹½Ý¸°‘•‰…Ñ•}ÍÑ…Ñ”¹ÍÑ…ÑÕÌ¤4(€€€€€€€€€€€½¹™¥‘•¹”€ôµ¥¸¡É…Ý}½¹™¥‘•¹”°½¹™¥‘•¹•}…À¤4(€€€€€€€€€€€‘•¥Í¥½¸€ô%¹Ù•ÍÑµ•¹Ñ•¥Í¥½¸ 4(€€€€€€€€€€€€€€€Ñ¥­•È°¹½Ý}¥Í¼ ¤°‘•¥Í¥½¹}¹…µ”°½¹™¥‘•¹”°4(€€€€€€€€€€€€€€€€‰Idˆ¥˜‘•¥Í¥½¹}¹…µ”¥¸ì‰	Udˆ°€‰=9%Q%=91}	Ud‰ô•±Í”€‰9=Q}Idˆ°4(€€€€€€€€€€€€€€€ÑÉ…‘•}Á±…¸°4(€€€€€€€€€€€€€€€l‰5=,ƒ².s®
c®š³²bƒ².ƒ¶bàˆ¥˜µ…É­•Ð¹¥Í}µ½¬•±Í”€‹².“®6Ã²vÓ¶Ã
ßªÞóªÆÀƒªâÃ®Â`ƒ²ÖsªÞðƒ²
³²^ƒ².ƒ¶bàˆ°4(€€€€€€€€€€€€€€€€˜ˆÈÁƒ²"c²v×®–€íµ…É­•Ð¹É•ÑÕÉ¹|ÈÁ‘}ÁÐè¬¸É™ô”€¼ƒ²®2ªÆÃ®zc®~$íµ…É­•Ð¹É•±…Ñ¥Ù•}Ù½±Õµ”è¸É™õàˆ°4(€€€€€€€€€€€€€€€€˜‹²‚®zÔƒ²‚¶V§®>íÉ•Í•…É ¹ÍÑÉ…Ñ•å}™¥Ñô¼ÄÀÀ‰t°4(€€€€€€€€€€€€€€€ÍÑ…Ñ”¹­¹½Ý¹}É¥Í­ÍlèÍt€¬É¥Í¬¹Ý…É¹¥¹ÍlèÉt°ÉÕ¹}¥¤4(€€€€€€€€€€€•áÁ½ÉÑ•‘}Á½Í¥Ñ¥½¹}Í¥é”€ô€¡Á½Í¥Ñ¥½¹}Í¥é”4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€¥˜‘•¥Í¥½¹}¹…µ”¥¸ì‰	Udˆ°€‰=9%Q%=91}	Ud‰ô•±Í”9½¹”¤4(€€€€€€€€€€€ÍÑ…ÉÑ•‘}…Ð€ôÍ•±˜¹‘ˆ¹•Ñ}ÉÕ¸¡ÉÕ¹}¥¥l‰ÍÑ…ÉÑ•‘}…Ð‰t(€€€€€€€€€€€Í•±˜¹}É•½É‘}ÕÍ…”¡ÉÕ¹}¥°Ñ¥­•È¤4(€€€€€€€€€€€ÕÍ…”€ôÍ•±˜¹‘ˆ¹ÕÍ…•}ÍÕµµ…Éä¡ÉÕ¹}¥¤4(€€€€€€€€€€€µ…¹¥™•ÍÐ€ôIÕ¹5…¹¥™•ÍÐ¡ÉÕ¹}¥°Ñ¥­•È°µ…É­•Ð¹Í¹…ÁÍ¡½Ñ}¥°4(€€€€€€€€€€€€€€€m¥Ñ•´¹•Ù¥‘•¹•}¥™½È¥Ñ•´¥¸•Ù¥‘•¹•t°ÍÑ…Ñ”¹±…ÍÑ}ÕÁ‘…Ñ•°4(€€€€€€€€€€€€€€€É•Í•…É ¹ÁÉ½µÁÑ}Ù•ÉÍ¥½¸°É¥Ñ¥Œ¹ÁÉ½µÁÑ}Ù•ÉÍ¥½¸°4(€€€€€€€€€€€€€€€•Ñ…ÑÑÈ¡Í•±˜¹¡…¥Éµ…¸°€‰ÁÉ½µÁÑ}Ù•ÉÍ¥½¸ˆ°€‰Õ¹­¹½Ý¸ˆ¤°É¥Í¬¹ÉÕ±•}Ù•ÉÍ¥½¸°4(€€€€€€€€€€€€€€€É•Í•…É ¹ÁÉ½Ù¥‘•È°É•Í•…É ¹µ½‘•°°ÍÑ…ÉÑ•‘}…Ð°¹½Ý}¥Í¼ ¤°‘•¥Í¥½¹}¹…µ”°4(€€€€€€€€€€€€€€€…¹…±åÍ¥Í}¥¹Ñ•¹Í¥ÑäõÉ•ÅÕ•ÍÐ¹…¹…±åÍ¥Í}¥¹Ñ•¹Í¥Ñä°4(€€€€€€€€€€€€€€€µ…É­•Ñ}…Í}½˜õµ…É­•Ð¹½‰Í•ÉÙ•‘}…Ð°4(€€€€€€€€€€€€€€€•Ù¥‘•¹•}ÕÑ½™˜õµ…à ¡¥Ñ•´¹™¥±•‘}…Ð½È¥Ñ•´¹ÁÕ‰±¥Í¡•‘}…Ð™½È¥Ñ•´¥¸•Ù¥‘•¹”¤°‘•™…Õ±Ðôˆˆ¤°4(€€€€€€€€€€€€€€€½µÁ…¹å™…ÑÍ}…Í}½˜õÍÑ…Ñ”¹½µÁ…¹å™…ÑÍ}…Í}½˜°4(€€€€€€€€€€€€€€€‘•‰…Ñ•}ÍÑ…ÑÕÌõ‘•‰…Ñ•}ÍÑ…Ñ”¹ÍÑ…ÑÕÌ°É½Õ¹‘}½Õ¹Ðõ‘•‰…Ñ•}É½Õ¹‘Ì°4(€€€€€€€€€€€€€€€¥¹ÁÕÑ}Ñ½­•¹ÌõÕÍ…•l‰¥¹ÁÕÑ}Ñ½­•¹Ì‰t°½ÕÑÁÕÑ}Ñ½­•¹ÌõÕÍ…•l‰½ÕÑÁÕÑ}Ñ½­•¹Ì‰t°4(€€€€€€€€€€€€€€€É•…Í½¹¥¹}Ñ½­•¹ÌõÕÍ…•l‰É•…Í½¹¥¹}Ñ½­•¹Ì‰t°4(€€€€€€€€€€€€€€€•ÍÑ¥µ…Ñ•‘}½ÍÑ}ÕÍõÕÍ…•l‰•ÍÑ¥µ…Ñ•‘}½ÍÑ}ÕÍ‰t°4(€€€€€€€€€€€€€€€Ñ½Ñ…±}±…Ñ•¹å}µÌõÕÍ…•l‰±…Ñ•¹å}µÌ‰t°4(€€€€€€€€€€€€€€€ÁÉ½µÁÑ}¡…Í¡•ÌõÍ•±˜¹}ÁÉ½µÁÑ}¡…Í¡•Ì ¤°4(€€€€€€€€€€€€€€€É¥Í­}½¹™¥}¡…Í õ¡…Í¡±¥ˆ¹Í¡„ÈÔØ¡©Í½¸¹‘ÕµÁÌ 4(€€€€€€€€€€€€€€€€€€€Í•±˜¹½¹™¥l‰É¥Í­}ÉÕ±•Ì‰t°Í½ÉÑ}­•åÌõQÉÕ”¤¹•¹½‘” ¤¤¹¡•á‘¥•ÍÐ ¤¤4(€€€€€€€€€€€ÍÑ…Ñ”¹ÁÉ•Ù¥½ÕÍ}‘•¥Í¥½¸€ô‘•¥Í¥½¹}¹…µ”4(€€€€€€€€€€€ÍÑ…Ñ”¹±…ÍÑ}ÕÁ‘…Ñ•€ô‘…Ñ•Ñ¥µ”¹¹½Ü¡Ñ¥µ•é½¹”¹ÕÑŒ¤¹‘…Ñ” ¤¹¥Í½™½Éµ…Ð ¤4(€€€€€€€€€€€É•Á½ÉÐ€ôÉ•¹‘•É}É•Á½ÉÐ¡ÉÕ¹}¥°µ…É­•Ð°ÍÑ…Ñ”°•Ù¥‘•¹”°É•Í•…É °É¥Ñ¥Œ°É¥Í¬°4(€€€€€€€€€€€€€€€‘•¥Í¥½¸°É•ÅÕ•ÍÑ}Á…å±½…°µ…É­•Ñ}É•¥µ”¹É•¥µ”°¡…¥Éµ…¹}½ÕÑÁÕÐ°4(€€€€€€€€€€€€€€€•áÁ½ÉÑ•‘}Á½Í¥Ñ¥½¹}Í¥é”°4(€€€€€€€€€€€€€€€‘•‰…Ñ•}ÍÑ…Ñ”õ‘•‰…Ñ•}ÍÑ…Ñ”°ÕÍ…”õÕÍ…”°™É•Í¡}‘•±Ñ„õ™É•Í¡}‘•±Ñ„°4(€€€€€€€€€€€€€€€…Á¥Ñ…±}ÍÑÉÕÑÕÉ”õ…Á¥Ñ…±}ÍÑÉÕÑÕÉ”¹Ñ½}‘¥Ð ¤¥˜…Á¥Ñ…±}ÍÑÉÕÑÕÉ”•±Í”íô°4(€€€€€€€€€€€€€€€•ÉÑ¥™¥…Ñ¥½¸õ•ÉÑ¥™¥…Ñ¥½¸¤4(€€€€€€€€€€€¥˜Í•±˜¹½¹™¥œ¹•Ð ‰É•Á½ÉÑ}‘¥Èˆ¤è4(€€€€€€€€€€€€€€€É•Á½ÉÑ}Á…Ñ €ôÝÉ¥Ñ•}ÉÕ¹}É•Á½ÉÐ¡Í•±˜¹½¹™¥l‰É•Á½ÉÑ}‘¥È‰t°É•Á½ÉÐ°Ñ¥­•È°ÉÕ¹}¥¤4(€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€É•Á½ÉÑ}Á…Ñ €ôÍ•±˜¹­¹½Ý±•‘”¹ÝÉ¥Ñ•}É•Á½ÉÐ¡Ñ¥­•È°ÉÕ¹}¥°É•Á½ÉÐ¤4(€€€€€€€€€€€Á…Á•É}•™™•Ð€ôÍ•±˜¹Á…Á•È¹Á±…¹}•™™•Ð 4(€€€€€€€€€€€€€€€‘•¥Í¥½¸°Á½Í¥Ñ¥½¹}Í¥é”°µ…É­•Ð¹Í•Ñ½É}¹…µ”°É•ÅÕ•ÍÐ¹Ñ¥µ•}¡½É¥é½¸¤4(€€€€€€€€€€€¥˜¹½ÐÉ•ÅÕ•ÍÐ¹Á…Á•É}…Ñ¥½¹}•¹…‰±•è4(€€€€€€€€€€€€€€€Á…Á•É}•™™•Ð€ôíô4(€€€€€€€€€€€…¹•±±…Ñ¥½¸¹¡•¬ ‰	=I}%91}AIM%MPˆ¤4(€€€€€€€€€€€Í•±˜¹‘ˆ¹™¥¹…±¥é•}…¹…±åÍ¥Ì¡‘•¥Í¥½¸°µ…¹¥™•ÍÐ°ÍÑ…Ñ”°É•Í•…É °É¥Ñ¥Œ°4(€€€€€€€€€€€€€€€É¥Í¬¹ÉÕ±•}Ù•ÉÍ¥½¸°É•ÅÕ•ÍÐ¹É•ÅÕ•ÍÑ}¥°ÍÑÈ¡É•Á½ÉÑ}Á…Ñ ¤°Á…Á•É}•™™•Ð°4(€€€€€€€€€€€€€€€‘•‰…Ñ•}ÍÑ…Ñ”¹ÍÑ…ÑÕÌ°‘•‰…Ñ•}É½Õ¹‘Ì°ÕÍ…”°•ÉÑ¥™¥…Ñ¥½¸¤4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€Í•±˜¹­¹½Ý±•‘”¹Íå¹}ÉÕ¸¡Ñ¥­•È°ÉÕ¹}¥°ÍÑ…Ñ”°•Ù¥‘•¹”°É•Í•…É °‘•¥Í¥½¸°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‘•‰…Ñ•}ÍÑ…Ñ”°É•Á½ÉÑ}Á…Ñ °4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€•ÉÑ¥™¥…Ñ¥½¹}ÍÑ…ÑÕÌõ•ÉÑ¥™¥…Ñ¥½¸¹•ÉÑ¥™¥…Ñ¥½¹}ÍÑ…ÑÕÌ¤4(€€€€€€€€€€€€€€€Í•±˜¹‘ˆ¹É•½É‘}­¹½Ý±•‘•}Íå¹Œ¡ÉÕ¹}¥°Ñ¥­•È°€‰MUMLˆ°ÍÑÈ¡Í•±˜¹­¹½Ý±•‘”¹É½½Ð¤¤4(€€€€€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè4(€€€€€€€€€€€€€€€Í…™•}­¹½Ý±•‘•}•ÉÉ½È€ôÉ•‘…Ñ}Í•É•ÑÌ¡•áŒ¤4(€€€€€€€€€€€€€€€Í•±˜¹‘ˆ¹É•½É‘}­¹½Ý±•‘•}Íå¹Œ¡ÉÕ¹}¥°Ñ¥­•È°€‰%1ˆ°ÍÑÈ¡Í•±˜¹­¹½Ý±•‘”¹É½½Ð¤°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€Í…™•}­¹½Ý±•‘•}•ÉÉ½È¤4(€€€€€€€€€€€€€€€Í•±˜¹‘ˆ¹±½œ¡ÉÕ¹}¥°€‰]I9%9ˆ°€‰-9=]1}Me9}%1ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€ì‰•ÉÉ½ÈˆèÍ…™•}­¹½Ý±•‘•}•ÉÉ½Éô¤4(€€€€€€€€€€€•µ¥Ð ‰IU9}MUMLˆ°ì‰‘•¥Í¥½¸ˆè‘•¥Í¥½¹}¹…µ”°€‰É•Á½ÉÐˆèÍÑÈ¡É•Á½ÉÑ}Á…Ñ ¥ô¤4(€€€€€€€€€€€Í•±˜¹¹½Ñ¥™¥•È¹Í•¹¡˜‰mí‘•¥Í¥½¹}¹…µ•õtíÑ¥­•Éô½¹™¥‘•¹”õí½¹™¥‘•¹•ô¼ÄÀÀÉÕ¹}¥õíÉÕ¹}¥‘ôˆ¤4(€€€€€€€€€€€É•ÑÕÉ¸ì‰ÉÕ¹}¥ˆèÉÕ¹}¥°€‰µ…É­•Ðˆèµ…É­•Ð°€‰ÍÑ…Ñ”ˆèÍÑ…Ñ”°€‰•Ù¥‘•¹”ˆè•Ù¥‘•¹”°4(€€€€€€€€€€€€€€€€€€€€‰É•Í•…É ˆèÉ•Í•…É °€‰É¥Ñ¥ŒˆèÉ¥Ñ¥Œ°€‰É¥Í¬ˆèÉ¥Í¬°4(€€€€€€€€€€€€€€€€€€€€‰¡…¥Éµ…¸ˆè¡…¥Éµ…¹}½ÕÑÁÕÐ°€‰Á½Í¥Ñ¥½¹}Í¥é”ˆè•áÁ½ÉÑ•‘}Á½Í¥Ñ¥½¹}Í¥é”°4(€€€€€€€€€€€€€€€€€€€€‰µ…¹¥™•ÍÐˆèµ…¹¥™•ÍÐ°€‰‘•¥Í¥½¸ˆè‘•¥Í¥½¸°€‰É•Á½ÉÑ}Á…Ñ ˆèÉ•Á½ÉÑ}Á…Ñ °4(€€€€€€€€€€€€€€€€€€€€‰µ…É­•Ñ}É•¥µ”ˆèµ…É­•Ñ}É•¥µ”¹É•¥µ”°€‰µ…É­•Ñ}É•¥µ•}½¹Ñ•áÐˆèµ…É­•Ñ}É•¥µ”°4(€€€€€€€€€€€€€€€€€€€€‰‘•‰…Ñ•}É½Õ¹‘Ìˆè‘•‰…Ñ•}É½Õ¹‘Ì°€‰‘•‰…Ñ•}ÍÑ…Ñ”ˆè‘•‰…Ñ•}ÍÑ…Ñ”°4(€€€€€€€€€€€€€€€€€€€€‰½¹Í•¹ÍÕÍ}É•ÍÕ±Ðˆè½¹Í•¹ÍÕÍ}É•ÍÕ±Ð°4(€€€€€€€€€€€€€€€€€€€€‰™¥¹…±}Õ…Éˆè™¥¹…±}Õ…É°€‰•ÉÑ¥™¥…Ñ¥½¸ˆè•ÉÑ¥™¥…Ñ¥½¸°4(€€€€€€€€€€€€€€€€€€€€‰É•ÅÕ•ÍÐˆèÉ•ÅÕ•ÍÑô4(€€€€€€€•á•ÁÐ€¡IÕ¹…¹•±±•‘ÉÉ½È°!•Éµ•Í…¹•±±•‘ÉÉ½È¤…Ì•áŒè4(€€€€€€€€€€€Í•±˜¹‘ˆ¹…­¹½Ý±•‘•}…¹•±±…Ñ¥½¸¡ÉÕ¹}¥¤4(€€€€€€€€€€€Í•±˜¹‘ˆ¹ÕÁ‘…Ñ•}É•ÅÕ•ÍÑ}ÍÑ…ÑÕÌ¡É•ÅÕ•ÍÐ¹É•ÅÕ•ÍÑ}¥°€‰911ˆ°ÉÕ¹}¥¤4(€€€€€€€€€€€Í•±˜¹‘ˆ¹±½œ¡ÉÕ¹}¥°€‰%9<ˆ°€‰IU9}911ˆ°ì‰•ÉÉ½ÈˆèÍÑÈ¡•áŒ¥ô¤4(€€€€€€€€€€€¥˜ÁÉ½É•ÍÌè4(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ ‰IU9}911ˆ°ÉÕ¹}¥°Ñ¥­•È°ì‰ÍÑ…ÑÕÌˆè€‰911‰ô¤4(€€€€€€€€€€€É…¥Í”4(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè4(€€€€€€€€€€€Í…™•}•ÉÉ½È€ôÉ•‘…Ñ}Í•É•ÑÌ¡•áŒ¤4(€€€€€€€€€€€ÍÑ…ÑÕÌ€ô€‰Q}%9MU%%9Pˆ¥˜¥Í¥¹ÍÑ…¹”¡•áŒ°¹…±åÍ¥Í%¹½µÁ±•Ñ•ÉÉ½È¤•±Í”€‰MeMQ5}II=Hˆ4(€€€€€€€€€€€Í•±˜¹‘ˆ¹™…¥±}ÉÕ¸¡ÉÕ¹}¥°Í…™•}•ÉÉ½È°ÍÑ…ÑÕÌ¤4(€€€€€€€€€€€Í•±˜¹‘ˆ¹ÕÁ‘…Ñ•}É•ÅÕ•ÍÑ}ÍÑ…ÑÕÌ¡É•ÅÕ•ÍÐ¹É•ÅÕ•ÍÑ}¥°€‰%1ˆ°ÉÕ¹}¥¤4(€€€€€€€€€€€Í•±˜¹‘ˆ¹±½œ¡ÉÕ¹}¥°€‰II=Hˆ°€‰IU9}%1ˆ°ì‰•ÉÉ½ÈˆèÍ…™•}•ÉÉ½È°€‰ÍÑ…ÑÕÌˆèÍÑ…ÑÕÍô¤4(€€€€€€€€€€€¥˜ÁÉ½É•ÍÌè4(€€€€€€€€€€€€€€€ÁÉ½É•ÍÌ ‰IU9}%1ˆ°ÉÕ¹}¥°Ñ¥­•È°ì‰ÍÑ…ÑÕÌˆèÍÑ…ÑÕÌ°€‰•ÉÉ½ÈˆèÍ…™•}•ÉÉ½Éô¤4(€€€€€€€€€€€É…¥Í”4(4(€€€‘•˜}µ…É­•Ñ}É•¥µ”¡Í•±˜°Ñ¥­•É}Í¹…ÁÍ¡½Ðõ9½¹”¤€´ø5…É­•ÑI•¥µ•½¹Ñ•áÐè4(€€€€€€€ÑÉäè4(€€€€€€€€€€€Í¹…ÁÍ¡½ÑÌ€ôíÑ¥­•ÈèÍ•±˜¹µ…É­•Ñ}ÁÉ½Ù¥‘•È¹Í¹…ÁÍ¡½Ð¡Ñ¥­•È¤4(€€€€€€€€€€€€€€€€€€€€€€€€™½ÈÑ¥­•È¥¸€ ‰EEDˆ°€‰%]4ˆ°€‰M=a`ˆ¥ô4(€€€€€€€€€€€É•ÑÕÉ¸5…É­•ÑI•¥µ•¹¥¹” ¤¹½¹Ñ•áÐ¡Í¹…ÁÍ¡½ÑÌ°Ñ¥­•É}Í¹…ÁÍ¡½Ð¤4(€€€€€€€•á•ÁÐá•ÁÑ¥½¸è4(€€€€€€€€€€€É•ÑÕÉ¸5…É­•ÑI•¥µ•½¹Ñ•áÐ ‰U9-9=]8ˆ°¹½Ý}¥Í¼ ¤°4(€€€€€€€€€€€€€€€ì‰EEDˆè9½¹”°€‰%]4ˆè9½¹”°€‰M=a`ˆè9½¹•ô°4(€€€€€€€€€€€€€€€ì‰EEDˆè9½¹”°€‰%]4ˆè9½¹”°€‰M=a`ˆè9½¹•ô°€‰U9-9=]8ˆ°€À¤4(4(€€€‘•˜}ÉÕ¹}É•Í•…É ¡Í•±˜°ÍÑ…Ñ”°µ…É­•Ð°•Ù¥‘•¹”°É•ÅÕ•ÍÐ°…¹…±åÍ¥Í}½¹Ñ•áÐõ9½¹”°É•Ù¥Í¥½¸õ9½¹”¤è4(€€€€€€€™½È…ÉÌ¥¸€ ¡ÍÑ…Ñ”°µ…É­•Ð°•Ù¥‘•¹”°É•ÅÕ•ÍÐ°É•Ù¥Í¥½¸°…¹…±åÍ¥Í}½¹Ñ•áÐ¤°4(€€€€€€€€€€€€€€€€€€€€€¡ÍÑ…Ñ”°µ…É­•Ð°•Ù¥‘•¹”°É•ÅÕ•ÍÐ°É•Ù¥Í¥½¸¤°4(€€€€€€€€€€€€€€€€€€€€€¡ÍÑ…Ñ”°µ…É­•Ð°•Ù¥‘•¹”¤¤è4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€¥¹ÍÁ•Ð¹Í¥¹…ÑÕÉ”¡Í•±˜¹É•Í•…É¡•È¹ÉÕ¸¤¹‰¥¹ ©…ÉÌ¤4(€€€€€€€€€€€•á•ÁÐQåÁ•ÉÉ½Èè4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹É•Í•…É¡•È¹ÉÕ¸ ©…ÉÌ¤4(€€€€€€€É…¥Í”QåÁ•ÉÉ½È ‰Õ¹ÍÕÁÁ½ÉÑ•I•Í•…É …•¹ÐÍ¥¹…ÑÕÉ”ˆ¤4(4(€€€‘•˜}ÉÕ¹}É¥Ñ¥Œ¡Í•±˜°É•Í•…É °ÍÑ…Ñ”°µ…É­•Ð°•Ù¥‘•¹”°É•ÅÕ•ÍÐ°…¹…±åÍ¥Í}½¹Ñ•áÐõ9½¹”°4(€€€€€€€€€€€€€€€€€€€‘•‰…Ñ•}½¹Ñ•áÐõ9½¹”¤è4(€€€€€€€™½È…ÉÌ¥¸€ ¡É•Í•…É °ÍÑ…Ñ”°µ…É­•Ð°•Ù¥‘•¹”°É•ÅÕ•ÍÐ°…¹…±åÍ¥Í}½¹Ñ•áÐ°‘•‰…Ñ•}½¹Ñ•áÐ¤°4(€€€€€€€€€€€€€€€€€€€€€¡É•Í•…É °ÍÑ…Ñ”°µ…É­•Ð°•Ù¥‘•¹”°É•ÅÕ•ÍÐ°…¹…±åÍ¥Í}½¹Ñ•áÐ¤°4(€€€€€€€€€€€€€€€€€€€€€¡É•Í•…É °ÍÑ…Ñ”°µ…É­•Ð°•Ù¥‘•¹”°É•ÅÕ•ÍÐ¤°4(€€€€€€€€€€€€€€€€€€€€€¡É•Í•…É °ÍÑ…Ñ”°µ…É­•Ð¤¤è4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€¥¹ÍÁ•Ð¹Í¥¹…ÑÕÉ”¡Í•±˜¹É¥Ñ¥Œ¹ÉÕ¸¤¹‰¥¹ ©…ÉÌ¤4(€€€€€€€€€€€•á•ÁÐQåÁ•ÉÉ½Èè4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹É¥Ñ¥Œ¹ÉÕ¸ ©…ÉÌ¤4(€€€€€€€É…¥Í”QåÁ•ÉÉ½È ‰Õ¹ÍÕÁÁ½ÉÑ•É¥Ñ¥Œ…•¹ÐÍ¥¹…ÑÕÉ”ˆ¤4(4(€€€ÍÑ…Ñ¥µ•Ñ¡½4(€€€‘•˜}µ¥¹¥µÕµ}±…¥µÌ¡É•ÅÕ•ÍÐèUÍ•ÉI•ÅÕ•ÍÐ¤€´ø¥¹Ðè4(€€€€€€€É•ÑÕÉ¸ì‰5%9%5U4ˆè€Ì°€‰9=I50ˆè€Ô°€‰5a%5U4ˆè€Ýô¹•Ð 4(€€€€€€€€€€€É•ÅÕ•ÍÐ¹…¹…±åÍ¥Í}¥¹Ñ•¹Í¥Ñä°€Ô¤4(4(€€€‘•˜}ÉÕ¹}¡…¥Éµ…¸¡Í•±˜°É•Í•…É °É¥Ñ¥Œ°É¥Í¬°É•ÅÕ•ÍÐ°Á½Í¥Ñ¥½¹}Í¥é”¤è4(€€€€€€€ÑÉäè4(€€€€€€€€€€€¥¹ÍÁ•Ð¹Í¥¹…ÑÕÉ”¡Í•±˜¹¡…¥Éµ…¸¹ÉÕ¸¤¹‰¥¹¡É•Í•…É °É¥Ñ¥Œ°É¥Í¬°É•ÅÕ•ÍÐ°Á½Í¥Ñ¥½¹}Í¥é”¤4(€€€€€€€•á•ÁÐQåÁ•ÉÉ½Èè4(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹¡…¥Éµ…¸¹ÉÕ¸¡É•Í•…É °É¥Ñ¥Œ°É¥Í¬¤4(€€€€€€€É•ÑÕÉ¸Í•±˜¹¡…¥Éµ…¸¹ÉÕ¸¡É•Í•…É °É¥Ñ¥Œ°É¥Í¬°É•ÅÕ•ÍÐ°Á½Í¥Ñ¥½¹}Í¥é”¤4(4(€€€‘•˜}É•½É‘}ÕÍ…”¡Í•±˜°ÉÕ¹}¥èÍÑÈ°Ñ¥­•ÈèÍÑÈ¤€´ø9½¹”è4(€€€€€€€€Œ…¹½¹¥…°!•Éµ•Ì…‘…ÁÑ•ÉÌÉ•½É•Ù•Éä¥¹Ù½…Ñ¥½¸€¡¥¹±Õ‘¥¹œÉ•Á…¥ÉÌ¤¥µµ•‘¥…Ñ•±ä¸4(€€€€€€€€Œ-•ÁÐ…Ì„½µÁ…Ñ¥‰¥±¥Ñä¡½½¬™½È¹½¸µ!•Éµ•ÌÑ•ÍÐ…•¹ÑÌ¸4(€€€€€€€É•ÑÕÉ¸9½¹”4(4(€€€ÍÑ…Ñ¥µ•Ñ¡½4(€€€‘•˜}ÁÉ½µÁÑ}¡…Í¡•Ì ¤€´ø‘¥ÑmÍÑÈ°ÍÑÉtè4(€€€€€€€É½½Ð€ô}}¥µÁ½ÉÑ}| ‰Á…Ñ¡±¥ˆˆ¤¹A…Ñ ¡}}™¥±•}|¤¹É•Í½±Ù” ¤¹Á…É•¹ÑÍlÅt€¼€‰ÁÉ½µÁÑÌˆ4(€€€€€€€É•ÍÕ±Ð€ôíô4(€€€€€€€™½ÈÁ…Ñ ¥¸Í½ÉÑ•¡É½½Ð¹±½ˆ ˆ¨¹µˆ¤¤è4(€€€€€€€€€€€É•ÍÕ±ÑmÁ…Ñ ¹¹…µ•t€ô¡…Í¡±¥ˆ¹Í¡„ÈÔØ¡Á…Ñ ¹É•…‘}‰åÑ•Ì ¤¤¹¡•á‘¥•ÍÐ ¤4(€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð4(4(€€€ÍÑ…Ñ¥µ•Ñ¡½4(€€€‘•˜}Í•Ñ}…•¹Ñ}½¹Ñ•áÐ¡…•¹Ð°ÉÕ¹}¥èÍÑÈ°É•ÅÕ•ÍÐèUÍ•ÉI•ÅÕ•ÍÐ°Ñ¥­•ÈèÍÑÈ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€É½Õ¹‘}¹¼è¥¹Ð°Á¡…Í”èÍÑÈ°…¹•±±…Ñ¥½¹}¡•¬õ9½¹”¤€´ø9½¹”è4(€€€€€€€…‘…ÁÑ•È€ô•Ñ…ÑÑÈ¡…•¹Ð°€‰…‘…ÁÑ•Èˆ°9½¹”¤4(€€€€€€€Í•ÑÑ•È€ô•Ñ…ÑÑÈ¡…‘…ÁÑ•È°€‰Í•Ñ}…±±}½¹Ñ•áÐˆ°9½¹”¤4(€€€€€€€¥˜Í•ÑÑ•Èè4(€€€€€€€€€€€Í•ÑÑ•È¡ÉÕ¹}¥õÉÕ¹}¥°É•ÅÕ•ÍÑ}¥õÉ•ÅÕ•ÍÐ¹É•ÅÕ•ÍÑ}¥°Ñ¥­•ÈõÑ¥­•È°4(€€€€€€€€€€€€€€€€€€É½Õ¹‘}¹¼õÉ½Õ¹‘}¹¼°Á¡…Í”õÁ¡…Í”°4(€€€€€€€€€€€€€€€€€€É•…Í½¹¥¹}•™™½ÉÐõÉ•ÅÕ•ÍÐ¹É•…Í½¹¥¹}ÁÉ½™¥±”°É•Á…¥É}…ÑÑ•µÁÐõ…±Í”°4(€€€€€€€€€€€€€€€€€€…¹•±±…Ñ¥½¹}¡•¬õ…¹•±±…Ñ¥½¹}¡•¬¤4(4(€€€‘•˜}Ù…±¥‘…Ñ•}•Ù¥‘•¹”¡Í•±˜°•Ù¥‘•¹”è±¥ÍÑmÙ¥‘•¹•%Ñ•µt¤€´ø9½¹”è4(€€€€€€€µ…á}…”€ôÍ•±˜¹½¹™¥l‰…¹…±åÍ¥Ì‰ul‰µ…á}•Ù¥‘•¹•}…•}‘…åÌ‰t4(€€€€€€€¹½Ü€ô‘…Ñ•Ñ¥µ”¹¹½Ü¡Ñ¥µ•é½¹”¹ÕÑŒ¤4(€€€€€€€ÕÍ…‰±”€ô€À4(€€€€€€€™½È¥Ñ•´¥¸•Ù¥‘•¹”è4(€€€€€€€€€€€ÁÕ‰±¥Í¡•€ô‘…Ñ•Ñ¥µ”¹™É½µ¥Í½™½Éµ…Ð¡¥Ñ•´¹ÁÕ‰±¥Í¡•‘}…Ð¹É•Á±…” ‰hˆ°€ˆ¬ÀÀèÀÀˆ¤¤4(€€€€€€€€€€€¥˜ÁÕ‰±¥Í¡•¹Ñé¥¹™¼¥Ì9½¹”è4(€€€€€€€€€€€€€€€ÁÕ‰±¥Í¡•€ôÁÕ‰±¥Í¡•¹É•Á±…”¡Ñé¥¹™¼õÑ¥µ•é½¹”¹ÕÑŒ¤4(€€€€€€€€€€€¥˜€¡¹½Ü€´ÁÕ‰±¥Í¡•¹…ÍÑ¥µ•é½¹”¡Ñ¥µ•é½¹”¹ÕÑŒ¤¤¹‘…åÌ€øµ…á}…”è4(€€€€€€€€€€€€€€€¥Ñ•´¹‘…Ñ…}ÅÕ…±¥Ñä€ô€‰MQ1ˆ4(€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€ÕÍ…‰±”€¬ô€Ä4(€€€€€€€É•ÅÕ¥É•€ôÍ•±˜¹½¹™¥l‰…¹…±åÍ¥Ì‰ul‰µ¥¹}•Ù¥‘•¹”‰t4(€€€€€€€¥˜ÕÍ…‰±”€ðÉ•ÅÕ¥É•è4(€€€€€€€€€€€É…¥Í”¹…±åÍ¥Í%¹½µÁ±•Ñ•ÉÉ½È¡˜‰ÕÍ…‰±”•Ù¥‘•¹”íÕÍ…‰±•ô¥Ì‰•±½Üµ¥¹¥µÕ´íÉ•ÅÕ¥É•‘ôˆ¤4(4(€€€ÍÑ…Ñ¥µ•Ñ¡½4(€€€‘•˜}µ…É­•Ñ}…Á}™É½µ}™…ÑÌ¡™…ÑÌè‘¥Ð°ÁÉ¥”è™±½…Ð¤€´ø™±½…Ðè4(€€€€€€€É½Ü€ô™…ÑÌ¹•Ð ‰Í¡…É•Í}½ÕÑÍÑ…¹‘¥¹œˆ¤½Èíô4(€€€€€€€ÑÉäè4(€€€€€€€€€€€É•ÑÕÉ¸™±½…Ð¡É½Ü¹•Ð ‰Ù…±Õ”ˆ°É½Ü¹•Ð ‰Ù…°ˆ°€À¤¤¤€¨ÁÉ¥”4(€€€€€€€•á•ÁÐ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€€€€€É•ÑÕÉ¸€À¸À4(4(€€€ÍÑ…Ñ¥µ•Ñ¡½4(€€€‘•˜}™…Ñ}Ù…±Õ”¡™…ÑÌè‘¥Ð°¹…µ”èÍÑÈ¤€´ø™±½…Ðð9½¹”è4(€€€€€€€É½Ü€ô™…ÑÌ¹•Ð¡¹…µ”¤½Èíô4(€€€€€€€ÑÉäè4(€€€€€€€€€€€Ù…±Õ”€ôÉ½Ü¹•Ð ‰Ù…±Õ”ˆ°É½Ü¹•Ð ‰Ù…°ˆ¤¤4(€€€€€€€€€€€É•ÑÕÉ¸™±½…Ð¡Ù…±Õ”¤¥˜Ù…±Õ”¥Ì¹½Ð9½¹”•±Í”9½¹”4(€€€€€€€•á•ÁÐ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€€€€€É•ÑÕÉ¸9½¹”4(4(€€€ÍÑ…Ñ¥µ•Ñ¡½4(€€€‘•˜}½¹™¥‘•¹•}…À¡‘…Ñ…}ÅÕ…±¥ÑäèÍÑÈ°É•¥µ•}½¹™¥‘•¹”è¥¹Ð°4(€€€€€€€€€€€€€€€€€€€€€€€É¥Ñ¥…±}…Á¥Ñ…±}Õ¹­¹½Ý¸è‰½½°°‘•‰…Ñ•}ÍÑ…ÑÕÌèÍÑÈ¤€´ø¥¹Ðè4(€€€€€€€…À€ô€ÄÀÀ4(€€€€€€€¥˜‘…Ñ…}ÅÕ…±¥Ñä€ôô€‰AIQ%0ˆè4(€€€€€€€€€€€…À€ôµ¥¸¡…À°€àÀ¤4(€€€€€€€•±¥˜‘…Ñ…}ÅÕ…±¥Ñä€„ô€‰=,ˆè4(€€€€€€€€€€€…À€ôµ¥¸¡…À°€ÔÔ¤4(€€€€€€€¥˜É•¥µ•}½¹™¥‘•¹”€ð€ÔÀè4(€€€€€€€€€€€…À€ôµ¥¸¡…À°€àÀ¤4(€€€€€€€¥˜É¥Ñ¥…±}…Á¥Ñ…±}Õ¹­¹½Ý¸è4(€€€€€€€€€€€…À€ôµ¥¸¡…À°€ÜÀ¤4(€€€€€€€¥˜‘•‰…Ñ•}ÍÑ…ÑÕÌ€ôô€‰1=,ˆè4(€€€€€€€€€€€…À€ôµ¥¸¡…À°€ØÔ¤4(€€€€€€€É•ÑÕÉ¸…À4(4(€€€ÍÑ…Ñ¥µ•Ñ¡½4(€€€‘•˜}™¥¹…±}‘•¥Í¥½¸¡É•Í•…É °É¥Ñ¥Œ°É¥Í¬°¥Í}µ½¬è‰½½°°¡…¥Éµ…¸è‘¥Ðð9½¹”€ô9½¹”¤€´øÍÑÈè4(€€€€€€€¥˜¹½ÐÉ¥Í¬¹¡…É‘}™¥±Ñ•É}Á…ÍÌè4(€€€€€€€€€€€É•ÑÕÉ¸€‰a1Uˆ4(€€€€€€€¥˜É¥Í¬¹É¥Í­}‘•¥Í¥½¸¥¸ì‰]%Pˆ°€‰a1U‰ô½ÈÉ¥Ñ¥Œ¹É¥Ñ¥}‘•¥Í¥½¸€ôô€‰]%Pˆè4(€€€€€€€€€€€É•ÑÕÉ¸É¥Í¬¹É¥Í­}‘•¥Í¥½¸¥˜É¥Í¬¹É¥Í­}‘•¥Í¥½¸€ôô€‰a1Uˆ•±Í”€‰]%Pˆ4(€€€€€€€¥˜¥Í}µ½¬è4(€€€€€€€€€€€É•ÑÕÉ¸€‰]%Pˆ4(€€€€€€€ÁÉ½Á½Í•€ô€¡¡…¥Éµ…¸½Èíô¤¹•Ð ‰‘•¥Í¥½¸ˆ°É•Í•…É ¹ÍÕ•ÍÑ•‘}‘•¥Í¥½¸¤4(€€€€€€€…±±½Ý•€ôí¥Ñ•´¹Ù…±Õ”™½È¥Ñ•´¥¸•¥Í¥½¹ô4(€€€€€€€É•ÑÕÉ¸ÁÉ½Á½Í•¥˜ÁÉ½Á½Í•¥¸…±±½Ý••±Í”€‰]%Pˆ4(