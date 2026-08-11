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
from .gates import DiscoveryGateRules, global_gate
from .handoff import EvidencePreflight, make_child_request
from .ingestion import EmptyDiscoveryMarketDataProvider
from .pareto import pareto_filter
from .ranking import rank_candidates
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
from .tournament import final_selection
from .universe import EmptySecurityMasterProvider, UniverseIntegrityEngine


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
        quotes = self.market_data.batch_quotes(tickers, as_of)
        quote_counter = getattr(self.market_data, "quote_batches", None)
        if quote_counter is None:
            quote_counter = len(getattr(self.market_data, "quote_calls", [])) or 1
        budget.used["max_quote_batches"] = int(quote_counter)
        feature_rows = []
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
            candidate = build_candidate(record, quote, bars, run_id, as_of)
            candidate.fuel_events = infer_fuel_events(candidate)
            self.stage_engine.apply(candidate)
            self.fuel_engine.evaluate(candidate)
            eligibility, reasons = global_gate(candidate, self.gate_rules)
            candidate.eligibility = eligibility
            candidate.gate_results["global_gate"] = "PASS" if eligibility == "ELIGIBLE" else eligibility
            for reason in reasons:
                candidate.risk_flags.append(reason)
            feature_rows.append(candidate)
        # Layer B: hydrate only market survivors, never the entire raw universe.
        survivor_limit = int(self.config.get("discovery", {}).get("enrichment", {}).get("market_survivor_n", 300))
        market_survivors = sorted(
            [item for item in feature_rows if item.eligibility != "INELIGIBLE"],
            key=lambda item: (value(item, "market_cap_usd", 0) or 0), reverse=True)[:survivor_limit]
        fundamental_rows = {}
        if self.fundamental_provider and market_survivors and budget.allow(
                "max_companyfacts_calls", len(market_survivors)):
            fundamental_rows = self.fundamental_provider.fundamentals(
                [item.security.ticker for item in market_survivors], as_of)
            budget.consume("max_companyfacts_calls", len(market_survivors))
            for candidate in market_survivors:
                candidate.fields.update(fundamental_rows.get(candidate.security.ticker, {}))
                candidate.unknown_fields = sorted(name for name, field in candidate.fields.items() if not field.known)
                self.fuel_engine.evaluate(candidate)
                eligibility, reasons = global_gate(candidate, self.gate_rules)
                candidate.eligibility = eligibility
                candidate.gate_results["global_gate"] = "PASS" if eligibility == "ELIGIBLE" else eligibility
                for reason in reasons:
                    if reason not in candidate.risk_flags:
                        candidate.risk_flags.append(reason)
        # Layer C: expensive offering/capital semantics only for the evidence
        # preflight slice, never for the complete market universe.
        preflight_n = int(self.config.get("discovery", {}).get("shortlist", {}).get("evidence_preflight_n", 8))
        preflight_rows = market_survivors[:preflight_n]
        if (self.capital_preflight_provider and preflight_rows and
                budget.allow("max_sec_calls", len(preflight_rows))):
            capital_rows = self.capital_preflight_provider.preflight(
                [item.security.ticker for item in preflight_rows], as_of)
            budget.consume("max_sec_calls", len(preflight_rows))
            for candidate in preflight_rows:
                candidate.fields.update(capital_rows.get(candidate.security.ticker, {}))
                candidate.unknown_fields = sorted(name for name, field in candidate.fields.items() if not field.known)
                eligibility, reasons = global_gate(candidate, self.gate_rules)
                candidate.eligibility = eligibility
                candidate.gate_results["global_gate"] = "PASS" if eligibility == "ELIGIBLE" else eligibility
                for reason in reasons:
                    if reason not in candidate.risk_flags:
                        candidate.risk_flags.append(reason)
        sectors = rank_sectors(feature_rows)
        top_sectors = {row["sector"] for row in sectors[:3] if row.get("rotation_score") is not None}
        sector_by_name = {row["sector"]: row for row in sectors}
        for candidate in feature_rows:
            sector = sector_by_name.get(candidate.security.sector_canonical, {})
            rotation_score = sector.get("rotation_score")
            candidate.fields["sector_rotation_score"] = FieldValue(
                rotation_score, UnknownState.KNOWN.value if rotation_score is not None else UnknownState.UNKNOWN_NOT_AVAILABLE.value,
                "DISCOVERY_SECTOR_SNAPSHOT", as_of)
            candidate.fields["sector_regime_fit"] = FieldValue(
                max(0.0, min(100.0, float(rotation_score))) if rotation_score is not None else None,
                UnknownState.KNOWN.value if rotation_score is not None else UnknownState.UNKNOWN_NOT_AVAILABLE.value,
                "DISCOVERY_SECTOR_SNAPSHOT", as_of)
            candidate.fields["sector_rotation_phase"] = FieldValue(
                sector.get("rotation_phase"), UnknownState.KNOWN.value if sector.get("rotation_phase") else UnknownState.UNKNOWN_NOT_AVAILABLE.value,
                "DISCOVERY_SECTOR_SNAPSHOT", as_of)
            candidate.unknown_fields = sorted(name for name, field in candidate.fields.items() if not field.known)
            candidate.paths.append("BLIND")
            if candidate.security.sector_canonical in top_sectors:
                candidate.paths.append("TOP_DOWN")
            for scanner in self.scanners:
                result = scanner.evaluate(candidate, context)
                if result.hit:
                    candidate.scanner_hits.append(result.scanner_name)
            if candidate.eligibility == "ELIGIBLE" and not candidate.scanner_hits:
                candidate.eligibility = "REVIEW_REQUIRED"
                candidate.risk_flags.append("NO_SCANNER_HIT")
        ranked = rank_candidates(feature_rows)
        pareto_filter(ranked)
        shortlist_n = int(self.config.get("discovery", {}).get("shortlist", {}).get("python_top_n", 20))
        p1_candidates = [item for item in ranked if item.discovery_bucket == "P1_DEEP_ANALYSIS"
                         and item.eligibility == "ELIGIBLE"
                         and item.gate_results.get("fuel_gate") == "PASS"]
        watch_candidates = [item for item in ranked if item.discovery_bucket == "WATCH"]
        rejected_candidates = [item for item in ranked if item.discovery_bucket == "REJECT"]
        secondary_candidates = [item for item in ranked if item.discovery_bucket == "P2_SECONDARY"]
        shortlisted = diversity_filter(p1_candidates + secondary_candidates,
                                       int(self.config.get("discovery", {}).get("diversity", {}).get("max_same_sector", 2)),
                                       int(self.config.get("discovery", {}).get("diversity", {}).get("max_same_theme", 2)))[:shortlist_n]
        market_loaded = len(quotes)
        feature_ready = sum(self._feature_ready(item) for item in feature_rows)
        sector_mapped = sum(item.security.sector_canonical != "UNKNOWN" for item in feature_rows)
        fundamental_ready = sum(self._fundamental_ready(item) for item in feature_rows)
        capital_ready = sum(self._capital_preflight_ready(item) for item in market_survivors)
        total = len(records)
        survivor_total = len(market_survivors)
        health = universe.get("health", {})
        coverage = CoverageMetrics(total, market_loaded, feature_ready, sector_mapped, fundamental_ready,
                                   round(market_loaded / total * 100, 4), round(feature_ready / total * 100, 4),
                                   round(sector_mapped / total * 100, 4), round(fundamental_ready / total * 100, 4),
                                   float(health.get("identity_coverage_pct", 0)),
                                   round(fundamental_ready / survivor_total * 100, 4) if survivor_total else 0.0,
                                   round(capital_ready / survivor_total * 100, 4) if survivor_total else 0.0)
        coverage_rules = self.config.get("discovery", {}).get("coverage", {})
        min_market = float(coverage_rules.get("market_min_pct", 95))
        min_feature = float(coverage_rules.get("feature_min_pct", 90))
        min_fundamental = float(coverage_rules.get("fundamental_enrichment_min_pct", 80))
        min_capital = float(coverage_rules.get("capital_preflight_min_pct", 80))
        if coverage.market_coverage_pct < min_market or coverage.feature_coverage_pct < min_feature:
            status, certification = DiscoveryStatus.BLOCKED_COVERAGE.value, "BLOCKED_COVERAGE"
        elif not fundamental_rows or coverage.fundamental_enrichment_coverage_pct < min_fundamental:
            status, certification = DiscoveryStatus.COMPLETED_SHADOW_MARKET_ONLY.value, "SHADOW_MARKET_ONLY"
        elif coverage.capital_preflight_coverage_pct < min_capital:
            status, certification = DiscoveryStatus.COMPLETED_SHADOW_ENRICHED.value, "SHADOW_ENRICHED"
        elif shadow:
            status, certification = DiscoveryStatus.COMPLETED_SHADOW_ENRICHED.value, "SHADOW_ENRICHED"
        else:
            status, certification = DiscoveryStatus.READY_FOR_DEEP_HANDOFF.value, "READY_FOR_DEEP_HANDOFF"
        benchmark_returns = self._benchmark_returns(as_of)
        bar_counter = getattr(self.market_data, "bar_calls", None)
        if not isinstance(bar_counter, (int, float)):
            bar_counter = len(bar_counter or [])
        result = DiscoveryResult(run_id, status, certification, context, coverage,
                                 DiscoveryMarketRegimeEngine().evaluate_with_benchmark_returns(feature_rows, benchmark_returns),
                                 sectors, p1_candidates[:shortlist_n],
                                 rejection_counts=universe["rejected"],
                                 scanner_counts={scanner.name: sum(scanner.name in item.scanner_hits for item in feature_rows)
                                                 for scanner in self.scanners},
                                 api_telemetry={"quote_batches": int(quote_counter),
                                                "bar_fetches": int(bar_counter),
                                 "fundamental_calls": len(getattr(self.fundamental_provider, "calls", [])),
                                                "capital_preflight_calls": len(getattr(self.capital_preflight_provider, "calls", [])),
                                                "benchmark_tickers": sorted(benchmark_returns)},
                                 all_candidates=ranked,
                                 watch_candidates=[item.to_dict() for item in watch_candidates],
                                 rejected_candidates=[item.to_dict() for item in rejected_candidates],
                                 budget_status=budget.snapshot())
        result.report_path = self._write_report(result)
        if (not shadow and request is not None and self.handoff and
                result.status == DiscoveryStatus.READY_FOR_DEEP_HANDOFF.value):
            result.deep_analysis_results = self.deep_analyze(result, request, budget)
            result.certified_candidates = [row for row in result.deep_analysis_results if row.get("certified")]
            result.blocked_candidates = [row for row in result.deep_analysis_results if not row.get("certified")]
            result.final_selection = final_selection(result.certified_candidates)
            result.final_selection_status = "EXECUTABLE" if result.final_selection != "NONE" else "NONE"
            result.final_selection_reason_codes = (["CERTIFIED_CHILD_SELECTED"] if result.final_selection != "NONE"
                                                   else ["NO_CERTIFIED_CHILD"])
            result.status = (DiscoveryStatus.COMPLETED.value if result.final_selection != "NONE"
                             else DiscoveryStatus.FINAL_NONE.value)
            result.budget_status = budget.snapshot()
        result.report_path = self._write_report(result)
        self.store.save_run(result, started, now_iso())
        return result

    def deep_analyze(self, result: DiscoveryResult, request, budget: DiscoveryBudgetGuard | None = None) -> list[dict[str, Any]]:
        """Explicit non-shadow handoff through the existing analyze_request call path."""
        limit = min(int(self.config.get("discovery", {}).get("shortlist", {}).get("deep_analysis_n", 3)),
                    int(self.cost_limits.get("max_deep_analysis_candidates") or 10_000))
        outputs = []
        for candidate in result.candidates[:limit]:
            if candidate.discovery_bucket != "P1_DEEP_ANALYSIS" or candidate.eligibility != "ELIGIBLE":
                outputs.append({"ticker": candidate.security.ticker, "status": "BLOCKED",
                                "reason_codes": ["NOT_P1_DEEP_ANALYSIS"], "certified": False})
                continue
            if budget and not budget.allow("max_child_analysis_runs"):
                outputs.append({"ticker": candidate.security.ticker, "status": "BLOCKED",
                                "reason_codes": ["MAX_CHILD_ANALYSIS_RUNS"], "certified": False})
                continue
            if budget and not budget.allow("max_llm_calls", 1):
                outputs.append({"ticker": candidate.security.ticker, "status": "BLOCKED",
                                "reason_codes": ["MAX_LLM_CALLS_PER_DISCOVERY"], "certified": False})
                continue
            preflight = self.evidence_preflight.evaluate(candidate)
            if preflight["status"] != "READY":
                candidate.risk_flags.extend(preflight["reason_codes"])
                outputs.append({"ticker": candidate.security.ticker, "status": "BLOCKED",
                                "certified": False, **preflight})
                continue
            child = self.handoff(make_child_request(request, candidate.security.ticker))
            if budget:
                budget.consume("max_child_analysis_runs")
                budget.consume("max_llm_calls")
            child_run_id = str(child.get("run_id", "")) if isinstance(child, dict) else ""
            if child_run_id:
                self.store.save_analysis_link(result.run_id, candidate.security.ticker, child_run_id, now_iso())
                result.analysis_links.append({"ticker": candidate.security.ticker,
                                              "analysis_run_id": child_run_id})
            certification = child.get("certification") if isinstance(child, dict) else None
            child_decision = getattr(child.get("decision"), "decision", None) if isinstance(child, dict) else None
            child_risk = child.get("risk") if isinstance(child, dict) else None
            certified = bool(getattr(certification, "certified", False)) and \
                child_decision in {"BUY", "CONDITIONAL_BUY"} and \
                bool(getattr(child_risk, "hard_filter_pass", False))
            decision_obj = child.get("decision") if isinstance(child, dict) else None
            risk_plan = getattr(child_risk, "trade_plan", None)
            decision_plan = getattr(decision_obj, "trade_plan", None)
            plan = decision_plan or risk_plan
            rr = float(getattr(plan, "reward_risk", 0.0) or 0.0)
            confidence = float(getattr(certification, "decision_confidence", None)
                               or getattr(decision_obj, "confidence", 0) or 0)
            entry_status = str(getattr(decision_obj, "entry_status", "UNKNOWN"))
            research_obj = child.get("research") if isinstance(child, dict) else None
            catalysts = getattr(research_obj, "catalysts", []) or []
            child_scores = {
                "catalyst_quality": min(100.0, len(catalysts) * 25.0),
                "expectation_gap": confidence,
                "entry_readiness": 100.0 if entry_status in {"READY", "CONDITIONAL", "READY_NOW"} else 0.0,
                "capital_structure_safety": max(0.0, 100.0 - len(getattr(child_risk, "warnings", []) or []) * 20.0),
                "reward_risk": min(100.0, max(0.0, rr * 20.0)),
                "data_confidence": confidence,
            }
            outputs.append({"ticker": candidate.security.ticker, "status": "HANDOFF",
                            "analysis_run_id": child_run_id, "certified": certified,
                            "certification_status": getattr(certification, "certification_status", "UNKNOWN"),
                            "decision": child_decision, "scores": child_scores,
                            "entry_readiness": child_scores["entry_readiness"],
                            "capital_structure_safety": child_scores["capital_structure_safety"],
                            "reward_risk": rr, "data_confidence": confidence})
        return outputs

    @staticmethod
    def _feature_ready(candidate) -> bool:
        return candidate.fields.get("current_price") is not None and candidate.fields.get("bar_count") is not None and candidate.fields["bar_count"].known and (candidate.fields["bar_count"].value or 0) >= 20

    @staticmethod
    def _capital_preflight_ready(candidate) -> bool:
        field = candidate.fields.get("capital_overhang_status")
        return bool(field and field.known and str(field.value).upper() not in {"UNKNOWN", "HIGH_RISK"})

    @staticmethod
    def _fundamental_ready(candidate) -> bool:
        field = candidate.fields.get("primary_financial_evidence")
        return bool(field and field.known and field.value is True)

    def _benchmark_returns(self, as_of: str) -> dict[str, float | None]:
        provider = self.benchmark_provider
        if not hasattr(provider, "benchmark_bars"):
            return {}
        try:
            bars = provider.benchmark_bars(["SPY", "QQQ", "IWM"], as_of)
        except Exception:
            return {}
        output: dict[str, float | None] = {}
        for ticker, rows in bars.items():
            usable = [row for row in rows if row.usable]
            prices = [float(row.adjusted_close if row.adjusted_close is not None else row.close)
                      for row in usable]
            output[ticker] = round((prices[-1] / prices[-21] - 1) * 100, 4) if len(prices) > 20 else None
        return output

    def _empty_result(self, context, status: str, error_code: str) -> DiscoveryResult:
        zero = CoverageMetrics(0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0)
        return DiscoveryResult(context.discovery_run_id, status, "BLOCKED_DATA", context, zero,
                               {"regime": "UNKNOWN", "confidence": 0, "reasons": [error_code]}, [], [],
                               error_code=error_code)

    def _write_report(self, result: DiscoveryResult) -> str:
        lines = ["# Discovery Report", "", f"- Run: `{result.run_id}`", f"- Status: `{result.status}`",
                 f"- Certification: `{result.certification_status}`", f"- As-of: `{result.context.discovery_as_of}`",
                 f"- Coverage: `{result.coverage.market_coverage_pct:.2f}% market / {result.coverage.feature_coverage_pct:.2f}% feature`",
                 f"- Regime: `{result.regime.get('regime')}`", "", "## Pipeline", "",
                 f"- Universe: `{result.coverage.eligible_universe_count}`",
                 f"- Market data: `{result.coverage.market_data_loaded_count}`",
                 f"- Feature ready: `{result.coverage.feature_ready_count}`",
                 f"- Shortlist: `{len(result.candidates)}`", "", "## Candidates"]
        for candidate in result.candidates:
            lines.append(f"- `{candidate.security.ticker}` — {candidate.discovery_bucket} / {candidate.stage} / score={candidate.composite_score:.2f}; unknown={','.join(candidate.unknown_fields) or 'none'}")
        if result.error_code:
            lines.extend(["", f"## Block reason", "", f"`{result.error_code}`"])
        path = write_run_report(self.config.get("report_dir", "data/reports"), "\n".join(lines), "DISCOVERY", result.run_id)
        return str(path)
