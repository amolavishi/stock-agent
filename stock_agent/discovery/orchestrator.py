from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..reports import write_run_report
from ..schemas import now_iso
from .budget import DiscoveryBudgetGuard
from .diversity import diversity_filter
from .features import FEATURE_VERSION, build_candidate, known_field, value
from .fuel import FuelEngine, infer_fuel_events
from .gates import DiscoveryGateRules, final_candidate_gate, market_screen_gate
from .handoff import EvidencePreflight, make_child_request
from .ingestion import EmptyDiscoveryMarketDataProvider
from .pareto import pareto_filter
from .ranking import rank_candidates, preliminary_priority_score, size_bucket
from .regime import DiscoveryMarketRegimeEngine
from .schemas import (CoverageMetrics, DiscoveryContext, DiscoveryResult, DiscoveryStatus,
                      FieldValue, UnknownState)
from .scanners import (AIBottleneckExpansionScanner, CustomerDiversificationScanner,
                        GeneralInflectionScanner, InsiderBuybackScanner,
                        MomentumInflectionScanner, OfferingSecondaryRecoveryScanner,
                        PolicyDefenseEnergySecurityScanner, PostEarningsRevisionDriftScanner,
                        ProfitabilityInflectionScanner, RefinancingDistressRemovalScanner,
                        TurnaroundScanner)
from .sectors import rank_sectors
from .stage import DiscoveryStageEngine, DiscoveryStageRules
from .store import DiscoveryStore
from .tournament import final_selection, scorecard_metadata
from .universe import EmptySecurityMasterProvider, UniverseIntegrityEngine
from .expiry import can_promote


class DiscoveryOrchestrator:
    """Phase-1 deterministic pipeline.  It is read-only with respect to PAPER."""

    def __init__(self, database, config: dict[str, Any], security_master=None,
                 market_data=None, fundamental_provider=None, benchmark_provider=None,
                 capital_preflight_provider=None,
                 handoff: Callable | None = None):
        self.database = database
        self.config = config
        self.security_master = security_master or EmptySecurityMasterProvider()
        self.market_data = market_data or EmptyDiscoveryMarketDataProvider()
        self.fundamental_provider = fundamental_provider
        self.capital_preflight_provider = capital_preflight_provider
        self.benchmark_provider = benchmark_provider or self.market_data
        self.handoff = handoff
        self.store = DiscoveryStore(database)
        discovery = config.get("discovery", {})
        universe = discovery.get("universe", {})
        self.universe_engine = UniverseIntegrityEngine(bool(universe.get("allow_adr", False)))
        self.gate_rules = DiscoveryGateRules(
            float(universe.get("min_price", 3.0)),
            float(universe.get("min_market_cap_usd", 300_000_000)),
            float(universe.get("min_adv20_usd", 10_000_000)))
        stage = discovery.get("stage", {})
        self.stage_engine = DiscoveryStageEngine(DiscoveryStageRules(
            float(stage.get("stage3_return_1d_pct", 25)),
            float(stage.get("stage3_return_5d_pct", 35)),
            float(stage.get("stage3_return_20d_pct", 50)),
            float(stage.get("stage3_distance_ma20_pct", 20)),
            float(stage.get("stage3_atr_multiple", 3))))
        self.fuel_engine = FuelEngine()
        self.scanners = [GeneralInflectionScanner(), MomentumInflectionScanner(),
                         ProfitabilityInflectionScanner(), AIBottleneckExpansionScanner(),
                         TurnaroundScanner(), PolicyDefenseEnergySecurityScanner(),
                         OfferingSecondaryRecoveryScanner(), InsiderBuybackScanner(),
                         RefinancingDistressRemovalScanner(), PostEarningsRevisionDriftScanner(),
                         CustomerDiversificationScanner()]
        self.evidence_preflight = EvidencePreflight()
        cost = discovery.get("cost", {})
        self.cost_limits = {
            "max_quote_batches": cost.get("max_quote_batches"),
            "max_bar_calls": cost.get("max_bar_calls"),
            "max_companyfacts_calls": cost.get("max_companyfacts_calls"),
            "max_sec_calls": cost.get("max_sec_calls"),
            "max_deep_analysis_candidates": cost.get("max_deep_analysis_candidates", 3),
            "max_child_analysis_runs": cost.get("max_child_analysis_runs", 3),
            "max_actual_llm_calls": cost.get("max_actual_llm_calls", cost.get("max_llm_calls_per_discovery", 0)),
            "max_actual_input_tokens": cost.get("max_llm_input_tokens", 0),
            "max_actual_output_tokens": cost.get("max_llm_output_tokens", 0),
            "max_actual_cost_usd": cost.get("max_estimated_cost_usd", 0),
            # Legacy test/config key is retained only as an alias for the
            # measured call limit; consumption is always actual telemetry.
            "max_llm_calls": cost.get("max_llm_calls_per_discovery", 0),
        }

    def _context_for_disabled(self, request) -> DiscoveryContext:
        run_id = f"DISC_DISABLED_{uuid.uuid4().hex[:8]}"
        as_of = now_iso()
        discovery = self.config.get("discovery", {})
        return DiscoveryContext(
            discovery_run_id=run_id, mode=request.discovery_mode or "MARKET",
            requested_sector=getattr(request, "requested_sector", ""),
            intensity=getattr(request, "analysis_intensity", "MINIMUM"),
            discovery_as_of=as_of, quote_batch_cutoff=as_of,
            completed_bar_cutoff=as_of, fundamental_cutoff=as_of, evidence_cutoff=as_of,
            rule_version=discovery.get("rule_version", "discovery_rules_v001"),
            feature_version=discovery.get("feature_version", FEATURE_VERSION),
            code_sha=self.config.get("code_sha", "WORKTREE"), universe_snapshot_id="",
            shadow=True)

    def run(self, mode: str = "MARKET", requested_sector: str = "", intensity: str = "MINIMUM",
            shadow: bool = True, as_of: str | None = None, request_id: str = "", request=None) -> DiscoveryResult:
        self.database.init()
        budget = DiscoveryBudgetGuard(self.cost_limits)
        run_id = f"DISC_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
        as_of = as_of or now_iso()
        started = now_iso()
        universe = self.universe_engine.build(self.security_master, as_of)
        records = [record for record in universe["records"]
                   if not requested_sector or record.sector_canonical.casefold() == requested_sector.casefold()]
        context = DiscoveryContext(
            discovery_run_id=run_id, mode=mode, requested_sector=requested_sector,
            intensity=intensity, discovery_as_of=as_of, quote_batch_cutoff=as_of,
            completed_bar_cutoff=as_of, fundamental_cutoff=as_of, evidence_cutoff=as_of,
            rule_version=self.config.get("discovery", {}).get("rule_version", "discovery_rules_v001"),
            feature_version=FEATURE_VERSION, code_sha=self.config.get("code_sha", "WORKTREE"),
            universe_snapshot_id=universe["snapshot_id"], shadow=shadow)
        if not records:
            result = self._empty_result(context, DiscoveryStatus.BOOTSTRAP_REQUIRED.value,
                                        "BLOCKED_BOOTSTRAP_OR_EMPTY_UNIVERSE")
            self.store.save_run(result, started, now_iso())
            return result
        self.store.save_universe(universe)
        tickers = [record.ticker for record in records]
        quote_before = self._counter(self.market_data, "quote_batches", "quote_calls")
        quote_batch_size = max(1, int(getattr(self.market_data, "quote_batch_size", len(tickers) or 1)))
        quotes: dict[str, Any] = {}
        for offset in range(0, len(tickers), quote_batch_size):
            if not budget.allow("max_quote_batches"):
                break
            chunk = tickers[offset:offset + quote_batch_size]
            quotes.update(self.market_data.batch_quotes(chunk, as_of))
            budget.consume("max_quote_batches")
        quote_counter = max(0, self._counter(self.market_data, "quote_batches", "quote_calls") - quote_before)
        feature_rows = []
        bar_before = self._counter(self.market_data, "bar_calls", "bar_calls")
        bar_cache_hits = 0
        for record in records:
            quote = quotes.get(record.ticker)
            if quote is None:
                continue
            bars = self.store.load_bars(record.ticker, as_of)
            if not bars:
                if not budget.allow("max_bar_calls"):
                    continue
                bars = self.market_data.daily_bars(record.ticker, as_of)
                budget.consume("max_bar_calls")
                self.store.save_bars(bars)
            else:
                refresh = getattr(self.market_data, "daily_bars_incremental", None)
                if callable(refresh) and budget.allow("max_bar_calls"):
                    updates = refresh(record.ticker, as_of, bars[-1].session_date)
                    budget.consume("max_bar_calls")
                    if updates:
                        by_date = {bar.session_date: bar for bar in bars}
                        by_date.update({bar.session_date: bar for bar in updates})
                        bars = sorted(by_date.values(), key=lambda bar: bar.session_date)
                        self.store.save_bars(updates)
                    else:
                        bar_cache_hits += 1
                else:
                    bar_cache_hits += 1
            candidate = build_candidate(record, quote, bars, run_id, as_of)
            self.stage_engine.apply(candidate)
            candidate.fuel_events = infer_fuel_events(candidate)
            self.fuel_engine.evaluate_preliminary(candidate)
            candidate.gate_results["stage_status"] = candidate.stage
            eligibility, reasons = market_screen_gate(candidate, self.gate_rules)
            candidate.eligibility = eligibility
            candidate.gate_results["global_gate"] = "PASS" if eligibility == "ELIGIBLE" else eligibility
            for reason in reasons:
                candidate.risk_flags.append(reason)
            feature_rows.append(candidate)
        self._apply_market_paths(feature_rows, context, as_of, demote_no_hit=False)
        # Layer B: hydrate only market survivors, never the entire raw universe.
        survivor_limit = int(self.config.get("discovery", {}).get("enrichment", {}).get("market_survivor_n", 300))
        eligible = [item for item in feature_rows if item.eligibility != "INELIGIBLE"]
        for item in eligible:
            item.preliminary_priority_score, _ = preliminary_priority_score(item)
            item.size_bucket = size_bucket(value(item, "market_cap_usd", None))
        diversity = self.config.get("discovery", {}).get("diversity", {})
        market_survivors = self._diversified_prefix(
            sorted(eligible, key=lambda item: (-float(item.preliminary_priority_score or 0), item.security.ticker)),
            survivor_limit, int(diversity.get("max_same_sector", survivor_limit)),
            int(diversity.get("max_same_theme", survivor_limit)),
            int(diversity.get("max_same_size_bucket", survivor_limit)))
        for candidate in market_survivors:
            candidate.gate_results.setdefault("fundamental_hydration_status", "NOT_FETCHED")
            candidate.gate_results.setdefault("capital_preflight_status", "NOT_REQUESTED")
        fundamental_rows = {}
        fundamental_before = len(getattr(self.fundamental_provider, "calls", [])) if self.fundamental_provider else 0
        if self.fundamental_provider and market_survivors and budget.allow(
                "max_companyfacts_calls", len(market_survivors)):
            fundamental_rows = self.fundamental_provider.fundamentals(
                [item.security.ticker for item in market_survivors], as_of)
            budget.consume("max_companyfacts_calls", len(market_survivors))
            for candidate in market_survivors:
                candidate.fields.update(fundamental_rows.get(candidate.security.ticker, {}))
                candidate.unknown_fields = sorted(name for name, field in candidate.fields.items() if not field.known)
                candidate.gate_results["fundamental_hydration_status"] = (
                    "READY" if candidate.security.ticker in fundamental_rows else "FAILED")
                # CompanyFacts changes the available fuel families.  Rebuild
                # the event list before evaluating the final hard gate.
                candidate.fuel_events = infer_fuel_events(candidate)
                self.fuel_engine.evaluate(candidate)
                eligibility, reasons = final_candidate_gate(candidate, self.gate_rules,
                                                            require_capital=False)
                candidate.eligibility = eligibility
                candidate.gate_results["final_candidate_gate"] = "PASS" if eligibility == "ELIGIBLE" else eligibility
                for reason in reasons:
                    if reason not in candidate.risk_flags:
                        candidate.risk_flags.append(reason)
        # Re-rank hydrated survivors before selecting expensive capital/SEC
        # preflight.  The first rank is cheap prioritization only.
        rank_candidates(market_survivors)
        for index, candidate in enumerate(market_survivors, 1):
            candidate.fundamental_rank = index
        # Layer C: expensive offering/capital semantics only for the evidence
        # preflight slice, never for the complete market universe.
        preflight_n = int(self.config.get("discovery", {}).get("shortlist", {}).get("evidence_preflight_n", 8))
        preflight_rows = diversity_filter(
            sorted(market_survivors, key=lambda item: (-item.composite_score, item.security.ticker)),
            int(diversity.get("max_same_sector", preflight_n)),
            int(diversity.get("max_same_theme", preflight_n)),
            int(diversity.get("max_same_size_bucket", preflight_n)))[:preflight_n]
        for index, candidate in enumerate(preflight_rows, 1):
            candidate.capital_preflight_rank = index
        capital_before = len(getattr(self.capital_preflight_provider, "calls", [])) if self.capital_preflight_provider else 0
        if (self.capital_preflight_provider and preflight_rows and
                budget.allow("max_sec_calls", len(preflight_rows))):
            capital_rows = self.capital_preflight_provider.preflight(
                [item.security.ticker for item in preflight_rows], as_of)
            budget.consume("max_sec_calls", len(preflight_rows))
            for candidate in preflight_rows:
                candidate.fields.update(capital_rows.get(candidate.security.ticker, {}))
                candidate.unknown_fields = sorted(name for name, field in candidate.fields.items() if not field.known)
                candiëN½¶‰žËkºwµç}¸°€‰¥µÁ½ÉÑ…¹Ñ}‘…Ñ…}Ý…É¹¥¹Ìˆ°mt¤¤(€€€€€€€€€€€•ÉÑ¥™¥•€ô•ÉÑ¥™¥•…¹ÑÉ…‘•}Á±…¹}Ù…±¥…¹¹½ÐÕ¹É•Í½±Ù•(€€€€€€€€€€€½ÕÑÁÕÑÌ¹…ÁÁ•¹¡ì‰Ñ¥­•Èˆè…¹‘¥‘…Ñ”¹Í•ÕÉ¥Ñä¹Ñ¥­•È°€‰ÍÑ…ÑÕÌˆè€‰!9=ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰…¹…±åÍ¥Í}ÉÕ¹}¥ˆè¡¥±‘}ÉÕ¹}¥°€‰•ÉÑ¥™¥•ˆè•ÉÑ¥™¥•°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰•ÉÑ¥™¥…Ñ¥½¹}ÍÑ…ÑÕÌˆè•Ñ…ÑÑÈ¡•ÉÑ¥™¥…Ñ¥½¸°€‰•ÉÑ¥™¥…Ñ¥½¹}ÍÑ…ÑÕÌˆ°€‰U9-9=]8ˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‘•¥Í¥½¸ˆè¡¥±‘}‘•¥Í¥½¸°€‰Í½É•Ìˆè¡¥±‘}Í½É•Ì°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½É•}ÁÉ½Ù•¹…¹”ˆèÍ½É•}ÁÉ½Ù•¹…¹”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í•Ñ½Èˆè…¹‘¥‘…Ñ”¹Í•ÕÉ¥Ñä¹Í•Ñ½É}…¹½¹¥…°°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€¨©Í½É•…É°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰µ¥¹}Í½É•…É‘}½Ù•É…•}ÁÐˆè™±½…Ð¡™¥¹…±}Í½É•…É‘}½¹™¥œ¹•Ð ‰µ¥¹}½Ù•É…•}ÁÐˆ°€ÜÔ¤¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰µ¥¹}É•Ý…É‘}É¥Í¬ˆè™±½…Ð¡™¥¹…±}Í½É•…É‘}½¹™¥œ¹•Ð ‰µ¥¹}É•Ý…É‘}É¥Í¬ˆ°€Ä¸Ô¤¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰É¥Í­}¡…É‘}™¥±Ñ•É}Á…ÍÌˆè‰½½°¡•Ñ…ÑÑÈ¡¡¥±‘}É¥Í¬°€‰¡…É‘}™¥±Ñ•É}Á…ÍÌˆ°…±Í”¤¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÑÉ…‘•}Á±…¹}Ù…±¥ˆèÑÉ…‘•}Á±…¹}Ù…±¥°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰µ…É­•Ñ}™É•Í ˆè‰½½°¡¡¥±¹•Ð ‰µ…É­•Ñ}™É•Í ˆ°QÉÕ”¤¤¥˜¥Í¥¹ÍÑ…¹”¡¡¥±°‘¥Ð¤•±Í”…±Í”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰¹½}µ…Ñ•É¥…±}Õ¹É•Í½±Ù•‘}‰±½­•Èˆè¹½ÐÕ¹É•Í½±Ù•°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰É•Ý…É‘}É¥Í¬ˆèÉÉô¤(€€€€€€€€€€€¥˜‰Õ‘•Ð…¹‰Õ‘•Ð¹Í¹…ÁÍ¡½Ð ¤¹•Ð ‰•á••‘•ˆ¤è(€€€€€€€€€€€€€€€½ÕÑÁÕÑÍl´Åul‰‰Õ‘•Ñ}É•…Í½¹}½‘•Ì‰t€ôl‰	UQ}=YIM!==Q}]%Q!%9}!%1‰t(€€€€€€€É•ÑÕÉ¸½ÕÑÁÕÑÌ((€€€‘•˜ÁÉ½µ½Ñ”¡Í•±˜°Í½ÕÉ•}ÉÕ¹}¥èÍÑÈ°É•ÅÕ•ÍÐ°±¥µ¥Ðè¥¹Ð€ô€À¤€´ø¥Í½Ù•ÉåI•ÍÕ±Ðè(€€€€€€€€ˆˆ‰áÁ±¥¥Ñ±äÁÉ½µ½Ñ”„ÍÑ½É•Í¡…‘½ÜÉÕ¸ìÍ…¹Ì¹•Ù•È…±°Ñ¡¥ÌÁ…Ñ ¸ˆˆˆ(€€€€€€€Á…å±½…€ôÍ•±˜¹ÍÑ½É”¹±…Ñ•ÍÐ¡Í½ÕÉ•}ÉÕ¹}¥¤(€€€€€€€¥˜¹½ÐÁ…å±½…è(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰%M=YIe}M=UI}IU9}9=Q}=U9ˆ¤(€€€€€€€É•ÍÕ±Ð€ô¥Í½Ù•ÉåI•ÍÕ±Ð¹™É½µ}‘¥Ð¡Á…å±½…¤(€€€€€€€¥˜É•ÍÕ±Ð¹ÍÑ…ÑÕÌ¥¸í¥Í½Ù•ÉåMÑ…ÑÕÌ¹	1=-}Q¹Ù…±Õ”°¥Í½Ù•ÉåMÑ…ÑÕÌ¹	1=-}=YI¹Ù…±Õ•ôè(€€€€€€€€€€€É•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¹}É•…Í½¹}½‘•Ì€ôl‰M=UI}IU9}9=Q}=5A1Q‰t(€€€€€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð(€€€€€€€…Í}½˜€ô¹½Ý}¥Í¼ ¤(€€€€€€€…¹‘¥‘…Ñ•Ì€ômt(€€€€€€€™½È…¹‘¥‘…Ñ”¥¸É•ÍÕ±Ð¹…¹‘¥‘…Ñ•Ìè(€€€€€€€€€€€…±±½Ý•°É•…Í½¸€ô…¹}ÁÉ½µ½Ñ”¡…¹‘¥‘…Ñ”¹Ñ½}‘¥Ð ¤°…Í}½˜¤(€€€€€€€€€€€¥˜…¹‘¥‘…Ñ”¹‘¥Í½Ù•Éå}‰Õ­•Ð€„ô€‰@Å}A}91eM%Lˆè(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰	1=-ˆ(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}É•…Í½¹}½‘•Ì€ôl‰9=Q}@Å}A}91eM%L‰t(€€€€€€€€€€€•±¥˜…¹‘¥‘…Ñ”¹•±¥¥‰¥±¥Ñä€„ô€‰1%%	1ˆè(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰	1=-ˆ(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}É•…Í½¹}½‘•Ì€ôl‰9=Q}1%%	1‰t(€€€€€€€€€€€•±¥˜…¹‘¥‘…Ñ”¹…Ñ•}É•ÍÕ±ÑÌ¹•Ð ‰™Õ•±}…Ñ”ˆ¤€„ô€‰AMLˆè(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰	1=-ˆ(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}É•…Í½¹}½‘•Ì€ôl‰U1}Q}9=Q}AML‰t(€€€€€€€€€€€•±¥˜¹½ÐÍ•±˜¹}…Á¥Ñ…±}ÁÉ•™±¥¡Ñ}É•…‘ä¡…¹‘¥‘…Ñ”¤è(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰	1=-ˆ(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}É•…Í½¹}½‘•Ì€ôl‰A%Q1}AI1%!Q}9=Q}Id‰t(€€€€€€€€€€€•±¥˜¹½Ð…±±½Ý•è(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰	1=-ˆ(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}É•…Í½¹}½‘•Ì€ômÉ•…Í½¹t(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰Idˆ(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ”¹ÁÉ½µ½Ñ¥½¹}É•…Í½¹}½‘•Ì€ôl‰AI=5=Q%=9}11=]‰t(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹¡…¹‘¥‘…Ñ”¤(€€€€€€€±¥™•å±•}‰å}Ñ¥­•È€ôí…¹‘¥‘…Ñ”¹Í•ÕÉ¥Ñä¹Ñ¥­•Èè…¹‘¥‘…Ñ”™½È…¹‘¥‘…Ñ”¥¸É•ÍÕ±Ð¹…¹‘¥‘…Ñ•Íô(€€€€€€€™½ÈÍ¹…ÁÍ¡½Ð¥¸É•ÍÕ±Ð¹…±±}…¹‘¥‘…Ñ•Ìè(€€€€€€€€€€€ÕÁ‘…Ñ•€ô±¥™•å±•}‰å}Ñ¥­•È¹•Ð¡Í¹…ÁÍ¡½Ð¹Í•ÕÉ¥Ñä¹Ñ¥­•È¤(€€€€€€€€€€€¥˜ÕÁ‘…Ñ•¥Ì¹½Ð9½¹”è(€€€€€€€€€€€€€€€Í¹…ÁÍ¡½Ð¹ÁÉ½µ½Ñ¥½¹}ÍÑ…ÑÕÌ€ôÕÁ‘…Ñ•¹ÁÉ½µ½Ñ¥½¹}ÍÑ…ÑÕÌ(€€€€€€€€€€€€€€€Í¹…ÁÍ¡½Ð¹ÁÉ½µ½Ñ¥½¹}É•…Í½¹}½‘•Ì€ô±¥ÍÐ¡ÕÁ‘…Ñ•¹ÁÉ½µ½Ñ¥½¹}É•…Í½¹}½‘•Ì¤(€€€€€€€É•ÍÕ±Ð¹…¹‘¥‘…Ñ•Ì€ô…¹‘¥‘…Ñ•Ílé±¥µ¥Ð½È±•¸¡…¹‘¥‘…Ñ•Ì¥t(€€€€€€€‰Õ‘•Ð€ô¥Í½Ù•Éå	Õ‘•ÑÕ…É¡Í•±˜¹½ÍÑ}±¥µ¥ÑÌ¤(€€€€€€€É•ÍÕ±Ð¹‘••Á}…¹…±åÍ¥Í}É•ÍÕ±ÑÌ€ôÍ•±˜¹‘••Á}…¹…±åé”¡É•ÍÕ±Ð°É•ÅÕ•ÍÐ°‰Õ‘•Ð¤(€€€€€€€É•ÍÕ±Ð¹•ÉÑ¥™¥•‘}…¹‘¥‘…Ñ•Ì€ômÉ½Ü™½ÈÉ½Ü¥¸É•ÍÕ±Ð¹‘••Á}…¹…±åÍ¥Í}É•ÍÕ±ÑÌ¥˜É½Ü¹•Ð ‰•ÉÑ¥™¥•ˆ¥t(€€€€€€€É•ÍÕ±Ð¹‰±½­•‘}…¹‘¥‘…Ñ•Ì€ômÉ½Ü™½ÈÉ½Ü¥¸É•ÍÕ±Ð¹‘••Á}…¹…±åÍ¥Í}É•ÍÕ±ÑÌ¥˜¹½ÐÉ½Ü¹•Ð ‰•ÉÑ¥™¥•ˆ¥t(€€€€€€€É•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¸€ô™¥¹…±}Í•±•Ñ¥½¸¡É•ÍÕ±Ð¹•ÉÑ¥™¥•‘}…¹‘¥‘…Ñ•Ì°Í•±˜¹}Á½ÉÑ™½±¥½}½¹Ñ•áÐ ¤¤(€€€€€€€É•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¹}ÍÑ…ÑÕÌ€ô€‰aUQ	1ˆ¥˜É•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¸€„ô€‰9=9ˆ•±Í”€‰9=9ˆ(€€€€€€€É•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¹}É•…Í½¹}½‘•Ì€ô€¡l‰IQ%%}!%1}M1Q‰t¥˜É•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¸€„ô€‰9=9ˆ(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€•±Í”l‰9=}IQ%%}!%1‰t¤(€€€€€€€É•ÍÕ±Ð¹‘••Á}¡…¹‘½™™}ÍÑ…ÑÕÌ€ô€‰A}!9=}Idˆ¥˜É•ÍÕ±Ð¹‘••Á}…¹…±åÍ¥Í}É•ÍÕ±ÑÌ•±Í”€‰	==QMQIA}IEU%Iˆ(€€€€€€€É•ÍÕ±Ð¹‰Õ‘•Ñ}ÍÑ…ÑÕÌ€ô‰Õ‘•Ð¹Í¹…ÁÍ¡½Ð ¤(€€€€€€€É•ÍÕ±Ð¹‰Õ‘•Ñ}ÍÑ…ÑÕÍl‰½Ù•ÉÍ¡½½Ñ}Í•µ…¹Ñ¥Ì‰t€ô€‰M=Q}AI9Q}	UQ}!I}!%1}U99=Iˆ(€€€€€€€¥˜É•ÍÕ±Ð¹‰Õ‘•Ñ}ÍÑ…ÑÕÌ¹•Ð ‰•á••‘•ˆ¤è(€€€€€€€€€€€É•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¹}É•…Í½¹}½‘•Ì¹…ÁÁ•¹ ‰	UQ}=YIM!==Q}]%Q!%9}!%1ˆ¤(€€€€€€€µ•…ÍÕÉ•€ô‰Õ‘•Ð¹µ•…ÍÕÉ• ¤(€€€€€€€É•ÍÕ±Ð¹…ÑÕ…±}±±µ}…±±Ì€ô¥¹Ð¡µ•…ÍÕÉ•‘l‰…ÑÕ…±}±±µ}…±±Ì‰t¤(€€€€€€€É•ÍÕ±Ð¹…ÑÕ…±}¥¹ÁÕÑ}Ñ½­•¹Ì€ô¥¹Ð¡µ•…ÍÕÉ•‘l‰…ÑÕ…±}¥¹ÁÕÑ}Ñ½­•¹Ì‰t¤(€€€€€€€É•ÍÕ±Ð¹…ÑÕ…±}½ÕÑÁÕÑ}Ñ½­•¹Ì€ô¥¹Ð¡µ•…ÍÕÉ•‘l‰…ÑÕ…±}½ÕÑÁÕÑ}Ñ½­•¹Ì‰t¤(€€€€€€€É•ÍÕ±Ð¹…ÑÕ…±}½ÍÑ}ÕÍ€ô™±½…Ð¡µ•…ÍÕÉ•‘l‰…ÑÕ…±}½ÍÑ}ÕÍ‰t¤(€€€€€€€™Õ¹¹•°€ôÉ•ÍÕ±Ð¹…Á¥}Ñ•±•µ•ÑÉä¹Í•Ñ‘•™…Õ±Ð ‰™Õ¹¹•°ˆ°íô¤(€€€€€€€™Õ¹¹•±l‰‘••Á}…¹…±åé•‰t€ô±•¸¡É•ÍÕ±Ð¹‘••Á}…¹…±åÍ¥Í}É•ÍÕ±ÑÌ¤(€€€€€€€™Õ¹¹•±l‰•ÉÑ¥™¥•‰t€ô±•¸¡É•ÍÕ±Ð¹•ÉÑ¥™¥•‘}…¹‘¥‘…Ñ•Ì¤(€€€€€€€™Õ¹¹•±l‰™¥¹…°‰t€ôÉ•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¸(€€€€€€€É•ÍÕ±Ð¹ÍÑ…ÑÕÌ€ô¥Í½Ù•ÉåMÑ…ÑÕÌ¹=5A1Q¹Ù…±Õ”¥˜É•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¸€„ô€‰9=9ˆ•±Í”¥Í½Ù•ÉåMÑ…ÑÕÌ¹%91}9=9¹Ù…±Õ”(€€€€€€€Í•±˜¹ÍÑ½É”¹Í…Ù•}ÉÕ¸¡É•ÍÕ±Ð°É•ÍÕ±Ð¹½¹Ñ•áÐ¹‘¥Í½Ù•Éå}…Í}½˜°¹½Ý}¥Í¼ ¤¤(€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð((€€€‘•˜}Á½ÉÑ™½±¥½}½¹Ñ•áÐ¡Í•±˜¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€ÑÉäè(€€€€€€€€€€€ÍÑ…Ñ”€ôÍ•±˜¹‘…Ñ…‰…Í”¹Á…Á•É}…½Õ¹Ñ}ÍÑ…Ñ” ¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸è(€€€€€€€€€€€É•ÑÕÉ¸íô(€€€€€€€•ÅÕ¥Ñä€ôµ…à À¸ÀÄ°™±½…Ð¡ÍÑ…Ñ”¹•Ð ‰•ÅÕ¥Ñäˆ°€À¤½È€À¤¤(€€€€€€€‘•˜ÁÑ}µ…À¡Ù…±Õ•Ì¤è(€€€€€€€€€€€É•ÑÕÉ¸í­•äèÉ½Õ¹¡™±½…Ð¡Ù…±Õ”½È€À¤€¼•ÅÕ¥Ñä€¨€ÄÀÀ°€Ð¤(€€€€€€€€€€€€€€€€€€€™½È­•ä°Ù…±Õ”¥¸€¡Ù…±Õ•Ì½Èíô¤¹¥Ñ•µÌ ¥ô(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰É•µ…¥¹¥¹}É¥Í­}‰Õ‘•Ñ}ÕÍˆèµ…à À¸À°™±½…Ð¡ÍÑ…Ñ”¹•Ð ‰É¥Í­}‰Õ‘•Ðˆ°€À¤¤€´(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€™±½…Ð¡ÍÑ…Ñ”¹•Ð ‰Á½ÉÑ™½±¥½}É¥Í­}ÕÍ•ˆ°€À¤¤¤°(€€€€€€€€€€€€‰•á¥ÍÑ¥¹}Í•Ñ½É}•áÁ½ÍÕÉ•}ÁÐˆèÁÑ}µ…À¡ÍÑ…Ñ”¹•Ð ‰Í•Ñ½É}•áÁ½ÍÕÉ”ˆ¤¤°(€€€€€€€€€€€€‰Á•¹‘¥¹}Í•Ñ½É}•áÁ½ÍÕÉ•}ÁÐˆèÁÑ}µ…À¡ÍÑ…Ñ”¹•Ð ‰Á•¹‘¥¹}Í•Ñ½É}½µµ¥ÑÑ•‘}•áÁ½ÍÕÉ”ˆ¤¤°(€€€€€€€€€€€€‰•á¥ÍÑ¥¹}Ñ¥­•É}•áÁ½ÍÕÉ•}ÁÐˆèÁÑ}µ…À¡ÍÑ…Ñ”¹•Ð ‰Ñ¥­•É}•áÁ½ÍÕÉ”ˆ¤¤°(€€€€€€€€€€€€‰Á•¹‘¥¹}Ñ¥­•É}•áÁ½ÍÕÉ•}ÁÐˆèÁÑ}µ…À¡ÍÑ…Ñ”¹•Ð ‰Á•¹‘¥¹}Ñ¥­•É}½µµ¥ÑÑ•‘}•áÁ½ÍÕÉ”ˆ¤¤°(€€€€€€€€€€€€‰Ñ½Ñ…±}½µµ¥ÑÑ•‘}•áÁ½ÍÕÉ•}ÁÐˆèÉ½Õ¹ (€€€€€€€€€€€€€€€€¡™±½…Ð¡ÍÑ…Ñ”¹•Ð ‰ÕÉÉ•¹Ñ}•áÁ½ÍÕÉ”ˆ°€À¤½È€À¤€¬(€€€€€€€€€€€€€€€€™±½…Ð¡ÍÑ…Ñ”¹•Ð ‰Á•¹‘¥¹}½µµ¥ÑÑ•‘}•áÁ½ÍÕÉ”ˆ°€À¤½È€À¤¤€¼•ÅÕ¥Ñä€¨€ÄÀÀ°€Ð¤°(€€€€€€€€€€€€‰Í•Ñ½É}…Á}ÁÐˆè™±½…Ð¡Í•±˜¹½¹™¥œ¹•Ð ‰Á…Á•Èˆ°íô¤¹•Ð ‰µ…á}Í•Ñ½É}•áÁ½ÍÕÉ•}ÁÐˆ°€ÈÔ¤¤°(€€€€€€€ô((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}±±µ}‰Õ‘•Ñ}…±±½ÝÌ¡‰Õ‘•Ðè¥Í½Ù•Éå	Õ‘•ÑÕ…É¤€´ø‰½½°è(€€€€€€€¥˜€‰µ…á}…ÑÕ…±}±±µ}…±±Ìˆ¥¸‰Õ‘•Ð¹±¥µ¥ÑÌè(€€€€€€€€€€€¥˜¹½Ð‰Õ‘•Ð¹…±±½Ý}ÕÍ…” ‰µ…á}…ÑÕ…±}±±µ}…±±Ìˆ°€‰…ÑÕ…±}±±µ}…±±Ìˆ°€Ä¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€€€€€™½È±¥µ¥Ñ}¹…µ”°ÕÍ…•}¹…µ”¥¸€ (€€€€€€€€€€€€€€€€ ‰µ…á}…ÑÕ…±}¥¹ÁÕÑ}Ñ½­•¹Ìˆ°€‰…ÑÕ…±}¥¹ÁÕÑ}Ñ½­•¹Ìˆ¤°(€€€€€€€€€€€€€€€€ ‰µ…á}…ÑÕ…±}½ÕÑÁÕÑ}Ñ½­•¹Ìˆ°€‰…ÑÕ…±}½ÕÑÁÕÑ}Ñ½­•¹Ìˆ¤°(€€€€€€€€€€€€€€€€ ‰µ…á}…ÑÕ…±}½ÍÑ}ÕÍˆ°€‰…ÑÕ…±}½ÍÑ}ÕÍˆ¤°(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€±¥µ¥Ð€ô‰Õ‘•Ð¹±¥µ¥ÑÌ¹•Ð¡±¥µ¥Ñ}¹…µ”¤(€€€€€€€€€€€€€€€¥˜±¥µ¥Ð¥Ì¹½Ð9½¹”…¹€¡±¥µ¥Ð€ðô€À½È‰Õ‘•Ð¹ÕÍ•¹•Ð¡ÕÍ…•}¹…µ”°€À¤€øô±¥µ¥Ð¤è(€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€€€€€É•ÑÕÉ¸QÉÕ”(€€€€€€€É•ÑÕÉ¸‰Õ‘•Ð¹…±±½Ü ‰µ…á}±±µ}…±±Ìˆ°€Ä¤((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}•ÉÑ¥™¥•‘}Í½É•…É¡¡¥±è‘¥ÑmÍÑÈ°¹åt°É•Í•…É¡}½‰¨è¹ä°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€Í½ÕÉ•}ÉÕ¹}¥èÍÑÈ°Í½ÕÉ•}…Í}½˜èÍÑÈ¤€´øÑÕÁ±•m‘¥ÑmÍÑÈ°¹åt°‘¥ÑmÍÑÈ°¹åutè(€€€€€€€€ˆˆ‰½Áä…ÑÕ…°Í½É•…ÉÙ…±Õ•Ììµ¥ÍÍ¥¹œ…á•ÌÍÑ…ä…‰Í•¹Ð½U9-9=]8¸ˆˆˆ(€€€€€€€Í½É•}‘•Ñ…¥±Ì€ô•Ñ…ÑÑÈ¡É•Í•…É¡}½‰¨°€‰Í½É•}‘•Ñ…¥±Ìˆ°íô¤½Èíô(€€€€€€€Í½É•Ìè‘¥ÑmÍÑÈ°¹åt€ôíô(€€€€€€€ÁÉ½Ù•¹…¹”è‘¥ÑmÍÑÈ°¹åt€ôíô(€€€€€€€¥˜É•Í•…É¡}½‰¨¥Ì¹½Ð9½¹”è(€€€€€€€€€€€™½È…á¥Ì¥¸€ ‰Í¥¹…±}ÍÑÉ•¹Ñ ˆ°€‰…Ñ…±åÍÑ}ÅÕ…±¥Ñäˆ°€‰•áÁ•Ñ…Ñ¥½¹}…Àˆ°€‰ÍÕÉ•}•±…ÍÑ¥¥Ñäˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€‰•¹ÑÉå}É•…‘¥¹•ÍÌˆ°€‰ÍÑÉ…Ñ•å}™¥Ðˆ¤è(€€€€€€€€€€€€€€€É…Ü€ô•Ñ…ÑÑÈ¡É•Í•…É¡}½‰¨°…á¥Ì°9½¹”¤(€€€€€€€€€€€€€€€¥˜É…Ü¥Ì¹½Ð9½¹”è(€€€€€€€€€€€€€€€€€€€Í½É•Ím…á¥Ít€ô™±½…Ð¡É…Ü¤(€€€€€€€€€€€€€€€€€€€ÁÉ½Ù•¹…¹•m…á¥Ít€ôì‰Í½ÕÉ•}ÉÕ¹}¥ˆèÍ½ÕÉ•}ÉÕ¹}¥°€‰Í½ÕÉ•}½µÁ½¹•¹Ðˆè€‰IMI ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}™¥•±ˆè…á¥Ì°€‰Í½ÕÉ•}…Í}½˜ˆèÍ½ÕÉ•}…Í}½™ô(€€€€€€€€€€€™½È…á¥Ì¥¸€ ‰…Á¥Ñ…±}ÍÑÉÕÑÕÉ•}Í…™•Ñäˆ°€‰‘…Ñ…}½¹™¥‘•¹”ˆ¤è(€€€€€€€€€€€€€€€‘•Ñ…¥°€ôÍ½É•}‘•Ñ…¥±Ì¹•Ð¡…á¥Ì¤(€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡‘•Ñ…¥°°‘¥Ð¤…¹‘•Ñ…¥°¹•Ð ‰Ù…±Õ”ˆ¤¥Ì¹½Ð9½¹”è(€€€€€€€€€€€€€€€€€€€Í½É•Ím…á¥Ít€ô™±½…Ð¡‘•Ñ…¥±l‰Ù…±Õ”‰t¤(€€€€€€€€€€€€€€€€€€€ÁÉ½Ù•¹…¹•m…á¥Ít€ôì‰Í½ÕÉ•}ÉÕ¹}¥ˆèÍ½ÕÉ•}ÉÕ¹}¥°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}½µÁ½¹•¹Ðˆè‘•Ñ…¥°¹•Ð ‰Í½ÕÉ•}½µÁ½¹•¹Ðˆ°€‰IMI!}M=IIˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}™¥•±ˆè‘•Ñ…¥°¹•Ð ‰Í½ÕÉ•}™¥•±ˆ°…á¥Ì¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}…Í}½˜ˆè‘•Ñ…¥°¹•Ð ‰Í½ÕÉ•}…Í}½˜ˆ°Í½ÕÉ•}…Í}½˜¥ô(€€€€€€€‘•¥Í¥½¸€ô¡¥±¹•Ð ‰‘•¥Í¥½¸ˆ¤¥˜¥Í¥¹ÍÑ…¹”¡¡¥±°‘¥Ð¤•±Í”9½¹”(€€€€€€€Á±…¸€ô•Ñ…ÑÑÈ¡¡¥±¹•Ð ‰É¥Í¬ˆ¤°€‰ÑÉ…‘•}Á±…¸ˆ°9½¹”¤¥˜¥Í¥¹ÍÑ…¹”¡¡¥±°‘¥Ð¤•±Í”9½¹”(€€€€€€€Á±…¸€ô•Ñ…ÑÑÈ¡‘•¥Í¥½¸°€‰ÑÉ…‘•}Á±…¸ˆ°9½¹”¤½ÈÁ±…¸(€€€€€€€¥˜Á±…¸¥Ì¹½Ð9½¹”…¹•Ñ…ÑÑÈ¡Á±…¸°€‰É•Ý…É‘}É¥Í¬ˆ°9½¹”¤¥Ì¹½Ð9½¹”è(€€€€€€€€€€€Í½É•Íl‰É•Ý…É‘}É¥Í¬‰t€ô™±½…Ð¡Á±…¸¹É•Ý…É‘}É¥Í¬¤(€€€€€€€€€€€ÁÉ½Ù•¹…¹•l‰É•Ý…É‘}É¥Í¬‰t€ôì‰Í½ÕÉ•}ÉÕ¹}¥ˆèÍ½ÕÉ•}ÉÕ¹}¥°€‰Í½ÕÉ•}½µÁ½¹•¹Ðˆè€‰QI}A18ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}™¥•±ˆè€‰É•Ý…É‘}É¥Í¬ˆ°€‰Í½ÕÉ•}…Í}½˜ˆèÍ½ÕÉ•}…Í}½™ô(€€€€€€€•ÉÑ¥™¥…Ñ¥½¸€ô¡¥±¹•Ð ‰•ÉÑ¥™¥…Ñ¥½¸ˆ¤¥˜¥Í¥¹ÍÑ…¹”¡¡¥±°‘¥Ð¤•±Í”9½¹”(€€€€€€€¥˜•Ñ…ÑÑÈ¡•ÉÑ¥™¥…Ñ¥½¸°€‰‘•¥Í¥½¹}½¹™¥‘•¹”ˆ°9½¹”¤¥Ì¹½Ð9½¹”è(€€€€€€€€€€€Í½É•Íl‰‘•¥Í¥½¹}½¹™¥‘•¹”‰t€ô™±½…Ð¡•ÉÑ¥™¥…Ñ¥½¸¹‘•¥Í¥½¹}½¹™¥‘•¹”¤(€€€€€€€€€€€ÁÉ½Ù•¹…¹•l‰‘•¥Í¥½¹}½¹™¥‘•¹”‰t€ôì‰Í½ÕÉ•}ÉÕ¹}¥ˆèÍ½ÕÉ•}ÉÕ¹}¥°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}½µÁ½¹•¹Ðˆè€‰IQ%%Q%=8ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}™¥•±ˆè€‰‘•¥Í¥½¹}½¹™¥‘•¹”ˆ°€‰Í½ÕÉ•}…Í}½˜ˆèÍ½ÕÉ•}…Í}½™ô(€€€€€€€É•ÑÕÉ¸Í½É•Ì°ÁÉ½Ù•¹…¹”((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}™•…ÑÕÉ•}É•…‘ä¡…¹‘¥‘…Ñ”¤€´ø‰½½°è(€€€€€€€É•ÑÕÉ¸…¹‘¥‘…Ñ”¹™¥•±‘Ì¹•Ð ‰ÕÉÉ•¹Ñ}ÁÉ¥”ˆ¤¥Ì¹½Ð9½¹”…¹…¹‘¥‘…Ñ”¹™¥•±‘Ì¹•Ð ‰‰…É}½Õ¹Ðˆ¤¥Ì¹½Ð9½¹”…¹…¹‘¥‘…Ñ”¹™¥•±‘Íl‰‰…É}½Õ¹Ð‰t¹­¹½Ý¸…¹€¡…¹‘¥‘…Ñ”¹™¥•±‘Íl‰‰…É}½Õ¹Ð‰t¹Ù…±Õ”½È€À¤€øô€ÈÀ((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}…Á¥Ñ…±}ÁÉ•™±¥¡Ñ}É•…‘ä¡…¹‘¥‘…Ñ”¤€´ø‰½½°è(€€€€€€€™¥•±€ô…¹‘¥‘…Ñ”¹™¥•±‘Ì¹•Ð ‰…Á¥Ñ…±}½Ù•É¡…¹}ÍÑ…ÑÕÌˆ¤(€€€€€€€É•ÑÕÉ¸‰½½°¡™¥•±…¹™¥•±¹­¹½Ý¸…¹ÍÑÈ¡™¥•±¹Ù…±Õ”¤¹ÕÁÁ•È ¤¹½Ð¥¸ì‰U9-9=]8ˆ°€‰!%!}I%M,‰ô¤((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}™Õ¹‘…µ•¹Ñ…±}É•…‘ä¡…¹‘¥‘…Ñ”¤€´ø‰½½°è(€€€€€€€™¥•±€ô…¹‘¥‘…Ñ”¹™¥•±‘Ì¹•Ð ‰ÁÉ¥µ…Éå}™¥¹…¹¥…±}•Ù¥‘•¹”ˆ¤(€€€€€€€É•ÑÕÉ¸‰½½°¡™¥•±…¹™¥•±¹­¹½Ý¸…¹™¥•±¹Ù…±Õ”¥ÌQÉÕ”¤((€€€‘•˜}‰•¹¡µ…É­}É•ÑÕÉ¹Ì¡Í•±˜°…Í}½˜èÍÑÈ¤€´ø‘¥ÑmÍÑÈ°™±½…Ðð9½¹•tè(€€€€€€€ÁÉ½Ù¥‘•È€ôÍ•±˜¹‰•¹¡µ…É­}ÁÉ½Ù¥‘•È(€€€€€€€¥˜¹½Ð¡…Í…ÑÑÈ¡ÁÉ½Ù¥‘•È°€‰‰•¹¡µ…É­}‰…ÉÌˆ¤è(€€€€€€€€€€€É•ÑÕÉ¸íô(€€€€€€€ÑÉäè(€€€€€€€€€€€‰…ÉÌ€ôÁÉ½Ù¥‘•È¹‰•¹¡µ…É­}‰…ÉÌ¡l‰MAdˆ°€‰EEDˆ°€‰%]4‰t°…Í}½˜¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸è(€€€€€€€€€€€É•ÑÕÉ¸íô(€€€€€€€½ÕÑÁÕÐè‘¥ÑmÍÑÈ°™±½…Ðð9½¹•t€ôíô(€€€€€€€™½ÈÑ¥­•È°É½ÝÌ¥¸‰…ÉÌ¹¥Ñ•µÌ ¤è(€€€€€€€€€€€ÕÍ…‰±”€ômÉ½Ü™½ÈÉ½Ü¥¸É½ÝÌ¥˜É½Ü¹ÕÍ…‰±•t(€€€€€€€€€€€ÁÉ¥•Ì€ôm™±½…Ð¡É½Ü¹…‘©ÕÍÑ•‘}±½Í”¥˜É½Ü¹…‘©ÕÍÑ•‘}±½Í”¥Ì¹½Ð9½¹”•±Í”É½Ü¹±½Í”¤(€€€€€€€€€€€€€€€€€€€€€™½ÈÉ½Ü¥¸ÕÍ…‰±•t(€€€€€€€€€€€½ÕÑÁÕÑmÑ¥­•Ét€ôÉ½Õ¹ ¡ÁÉ¥•Íl´Åt€¼ÁÉ¥•Íl´ÈÅt€´€Ä¤€¨€ÄÀÀ°€Ð¤¥˜±•¸¡ÁÉ¥•Ì¤€ø€ÈÀ•±Í”9½¹”(€€€€€€€É•ÑÕÉ¸½ÕÑÁÕÐ((€€€‘•˜}•µÁÑå}É•ÍÕ±Ð¡Í•±˜°½¹Ñ•áÐ°ÍÑ…ÑÕÌèÍÑÈ°•ÉÉ½É}½‘”èÍÑÈ¤€´ø¥Í½Ù•ÉåI•ÍÕ±Ðè(€€€€€€€é•É¼€ô½Ù•É…•5•ÑÉ¥Ì À°€À°€À°€À°€À°€À¸À°€À¸À°€À¸À°€À¸À¤(€€€€€€€É•ÑÕÉ¸¥Í½Ù•ÉåI•ÍÕ±Ð¡½¹Ñ•áÐ¹‘¥Í½Ù•Éå}ÉÕ¹}¥°ÍÑ…ÑÕÌ°€‰	1=-}Qˆ°½¹Ñ•áÐ°é•É¼°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ì‰É•¥µ”ˆè€‰U9-9=]8ˆ°€‰½¹™¥‘•¹”ˆè€À°€‰É•…Í½¹Ìˆèm•ÉÉ½É}½‘•uô°mt°mt°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€•ÉÉ½É}½‘”õ•ÉÉ½É}½‘”¤((€€€‘•˜}ÝÉ¥Ñ•}É•Á½ÉÐ¡Í•±˜°É•ÍÕ±Ðè¥Í½Ù•ÉåI•ÍÕ±Ð¤€´øÍÑÈè(€€€€€€€±¥¹•Ì€ôlˆŒ¥Í½Ù•ÉäI•Á½ÉÐˆ°€ˆˆ°˜ˆ´IÕ¸èíÉ•ÍÕ±Ð¹ÉÕ¹}¥‘õ€ˆ°˜ˆ´MÑ…ÑÕÌèíÉ•ÍÕ±Ð¹ÍÑ…ÑÕÍõ€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´•ÉÑ¥™¥…Ñ¥½¸èíÉ•ÍÕ±Ð¹•ÉÑ¥™¥…Ñ¥½¹}ÍÑ…ÑÕÍõ€ˆ°˜ˆ´Ìµ½˜èíÉ•ÍÕ±Ð¹½¹Ñ•áÐ¹‘¥Í½Ù•Éå}…Í}½™õ€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´5…É­•ÐÍ…¸èíÉ•ÍÕ±Ð¹µ…É­•Ñ}Í…¹}ÍÑ…ÑÕÍõ€€¼•¹É¥¡µ•¹ÐèíÉ•ÍÕ±Ð¹•¹É¥¡µ•¹Ñ}ÍÑ…ÑÕÍõ€€¼‘••À¡…¹‘½™˜èíÉ•ÍÕ±Ð¹‘••Á}¡…¹‘½™™}ÍÑ…ÑÕÍõ€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´½Ù•É…”èíÉ•ÍÕ±Ð¹½Ù•É…”¹µ…É­•Ñ}½Ù•É…•}ÁÐè¸É™ô”µ…É­•Ð€¼íÉ•ÍÕ±Ð¹½Ù•É…”¹™•…ÑÕÉ•}½Ù•É…•}ÁÐè¸É™ô”™•…ÑÕÉ”€¼íÉ•ÍÕ±Ð¹½Ù•É…”¹™Õ¹‘…µ•¹Ñ…±}•¹É¥¡µ•¹Ñ}½Ù•É…•}ÁÐè¸É™ô”™Õ¹‘…µ•¹Ñ…°€¼íÉ•ÍÕ±Ð¹½Ù•É…”¹…Á¥Ñ…±}ÁÉ•™±¥¡Ñ}½Ù•É…•}ÁÐè¸É™ô”…Á¥Ñ…±€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´…Á¥Ñ…°ÁÉ•™±¥¡ÐÍ½Á”èíÉ•ÍÕ±Ð¹½Ù•É…”¹…Á¥Ñ…±}ÁÉ•™±¥¡Ñ}Í½Á•}ÁÐè¸É™ô”½˜µ…É­•ÐÍÕÉÙ¥Ù½ÉÍ€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´%‘•¹Ñ¥Ñä½M•Ñ½ÈèíÉ•ÍÕ±Ð¹½Ù•É…”¹¥‘•¹Ñ¥Ñå}½Ù•É…•}ÁÐè¸É™ô”€¼íÉ•ÍÕ±Ð¹½Ù•É…”¹Í•Ñ½É}½Ù•É…•}ÁÐè¸É™ô•€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´I•¥µ”èíÉ•ÍÕ±Ð¹É•¥µ”¹•Ð É•¥µ”œ¥õ€ˆ°€ˆˆ°€ˆŒŒA¥Á•±¥¹”ˆ°€ˆˆ°(€€€€€€€€€€€€€€€€˜ˆ´U¹¥Ù•ÉÍ”…•ÁÑ•èíÉ•ÍÕ±Ð¹½Ù•É…”¹•±¥¥‰±•}Õ¹¥Ù•ÉÍ•}½Õ¹Ñõ€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´5…É­•Ð‘…Ñ„èíÉ•ÍÕ±Ð¹½Ù•É…”¹µ…É­•Ñ}‘…Ñ…}±½…‘•‘}½Õ¹Ñõ€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´•…ÑÕÉ”É•…‘äèíÉ•ÍÕ±Ð¹½Ù•É…”¹™•…ÑÕÉ•}É•…‘å}½Õ¹Ñõ€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´@Äèí±•¸¡É•ÍÕ±Ð¹…¹‘¥‘…Ñ•Ì¥õ€€¼@Èèí±•¸¡m¥Ñ•´™½È¥Ñ•´¥¸É•ÍÕ±Ð¹…±±}…¹‘¥‘…Ñ•Ì¥˜¥Ñ•´¹‘¥Í½Ù•Éå}‰Õ­•Ð€ôô€@É}M=9Idt¥õ€€¼]…Ñ èí±•¸¡É•ÍÕ±Ð¹Ý…Ñ¡}…¹‘¥‘…Ñ•Ì¥õ€€¼I•©•Ñ•èí±•¸¡É•ÍÕ±Ð¹É•©•Ñ•‘}…¹‘¥‘…Ñ•Ì¥õ€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´ÑÕ…°114èíÉ•ÍÕ±Ð¹…ÑÕ…±}±±µ}…±±Íõ€…±±Ì€¼íÉ•ÍÕ±Ð¹…ÑÕ…±}¥¹ÁÕÑ}Ñ½­•¹Íõ€¥¹ÁÕÐ€¼íÉ•ÍÕ±Ð¹…ÑÕ…±}½ÕÑÁÕÑ}Ñ½­•¹Íõ€½ÕÑÁÕÐ€¼€‘íÉ•ÍÕ±Ð¹…ÑÕ…±}½ÍÑ}ÕÍè¸Ñ™õ€ˆ°(€€€€€€€€€€€€€€€€˜ˆ´Õ¹¹•°èíÉ•ÍÕ±Ð¹…Á¥}Ñ•±•µ•ÑÉä¹•Ð ™Õ¹¹•°œ°íô¥õ€ˆ°(€€€€€€€€€€€€€€€€€ˆˆ°€ˆŒŒ…¹‘¥‘…Ñ•Ì‰t(€€€€€€€™½È…¹‘¥‘…Ñ”¥¸É•ÍÕ±Ð¹…¹‘¥‘…Ñ•Ìè(€€€€€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡˜ˆ´í…¹‘¥‘…Ñ”¹Í•ÕÉ¥Ñä¹Ñ¥­•Éõ€ƒŠPí…¹‘¥‘…Ñ”¹‘¥Í½Ù•Éå}‰Õ­•Ñô€¼í…¹‘¥‘…Ñ”¹ÍÑ…•ô€¼Í½É”õí…¹‘¥‘…Ñ”¹½µÁ½Í¥Ñ•}Í½É”è¸É™ôìÕ¹­¹½Ý¸õìœ°œ¹©½¥¸¡…¹‘¥‘…Ñ”¹Õ¹­¹½Ý¹}™¥•±‘Ì¤½È€¹½¹”ôˆ¤(€€€€€€€±¥¹•Ì¹•áÑ•¹¡lˆˆ°€ˆŒŒ¥¹…°ˆ°€ˆˆ°˜ˆ´¥¹…°Í•±•Ñ¥½¸èíÉ•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¹õ€ˆ°(€€€€€€€€€€€€€€€€€€€€€˜ˆ´¥¹…°ÍÑ…ÑÕÌèíÉ•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¹}ÍÑ…ÑÕÍõ€ˆ°(€€€€€€€€€€€€€€€€€€€€€˜ˆ´I•…Í½¹Ìèìœ°œ¹©½¥¸¡É•ÍÕ±Ð¹™¥¹…±}Í•±•Ñ¥½¹}É•…Í½¹}½‘•Ì¤½È€¹½¹”õ€‰t¤(€€€€€€€¥˜É•ÍÕ±Ð¹•ÉÉ½É}½‘”è(€€€€€€€€€€€±¥¹•Ì¹•áÑ•¹¡lˆˆ°˜ˆŒŒ	±½¬É•…Í½¸ˆ°€ˆˆ°˜‰íÉ•ÍÕ±Ð¹•ÉÉ½É}½‘•õ€‰t¤(€€€€€€€Á…Ñ €ôÝÉ¥Ñ•}ÉÕ¹}É•Á½ÉÐ¡Í•±˜¹½¹™¥œ¹•Ð ‰É•Á½ÉÑ}‘¥Èˆ°€‰‘…Ñ„½É•Á½ÉÑÌˆ¤°€‰q¸ˆ¹©½¥¸¡±¥¹•Ì¤°€‰%M=YIdˆ°É•ÍÕ±Ð¹ÉÕ¹}¥¤(€€€€€€€É•ÑÕÉ¸ÍÑÈ¡Á…Ñ ¤(