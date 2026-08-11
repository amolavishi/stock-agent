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
        preflight_rows = self._capital_preflight_candidates(
            market_survivors, preflight_n, diversity)
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
                candidate.gate_results["capital_preflight_status"] = (
                    "READY" if candidate.security.ticker in capital_rows else "FAILED")
                eligibility, reasons = final_candidate_gate(candidate, self.gate_rules,
                                                            require_capital=True)
                candidate.eligibility = eligibility
                candidate.gate_results["final_candidate_gate"] = "PASS" if eligibility == "ELIGIBLE" else eligibility
                for reason in reasons:
                    if reason not in candidate.risk_flags:
                        candidate.risk_flags.append(reason)
        sectors = rank_sectors(feature_rows)
        self._apply_market_paths(feature_rows, context, as_of)
        ranked = rank_candidates(feature_rows)
        for candidate in ranked:
            candidate.gate_results["score_coverage_status"] = (
                "PASS" if candidate.score_coverage_pct >= 70 else "INSUFFICIENT")
            candidate.gate_results["final_bucket_reason_codes"] = ",".join(
                candidate.risk_flags or ([candidate.discovery_bucket] if candidate.discovery_bucket else []))
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
                                       int(self.config.get("discovery", {}).get("diversity", {}).get("max_same_theme", 2)),
                                       int(self.config.get("discovery", {}).get("diversity", {}).get("max_same_size_bucket", shortlist_n)))[:shortlist_n]
        market_loaded = len(quotes)
        feature_ready = sum(self._feature_ready(item) for item in feature_rows)
        sector_mapped = sum(item.security.sector_canonical != "UNKNOWN" for item in feature_rows)
        fundamental_ready = sum(self._fundamental_ready(item) for item in feature_rows)
        capital_ready = sum(self._capital_preflight_ready(item) for item in preflight_rows)
        total = len(records)
        survivor_total = len(market_survivors)
        preflight_total = len(preflight_rows)
        health = universe.get("health", {})
        coverage = CoverageMetrics(total, market_loaded, feature_ready, sector_mapped, fundamental_ready,
                                   round(market_loaded / total * 100, 4), round(feature_ready / total * 100, 4),
                                   round(sector_mapped / total * 100, 4), round(fundamental_ready / total * 100, 4),
                                   float(health.get("identity_coverage_pct", 0)),
                                   round(fundamental_ready / survivor_total * 100, 4) if survivor_total else 0.0,
                                   round(capital_ready / preflight_total * 100, 4) if preflight_total else 0.0,
                                   round(preflight_total / survivor_total * 100, 4) if survivor_total else 0.0)
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
        bar_counter = max(0, self._counter(self.market_data, "bar_calls", "bar_calls") - bar_before)
        fundamental_calls = max(0, len(getattr(self.fundamental_provider, "calls", [])) - fundamental_before)
        capital_calls = max(0, len(getattr(self.capital_preflight_provider, "calls", [])) - capital_before)
        market_scan_status = "MARKET_SCAN_READY" if coverage.market_coverage_pct >= min_market and coverage.feature_coverage_pct >= min_feature else "BOOTSTRAP_REQUIRED"
        enrichment_status = "ENRICHMENT_READY" if fundamental_rows and coverage.fundamental_enrichment_coverage_pct >= min_fundamental else "BOOTSTRAP_REQUIRED"
        deep_handoff_status = ("DEEP_HANDOFF_READY" if enrichment_status == "ENRICHMENT_READY" and
                               coverage.capital_preflight_coverage_pct >= min_capital and
                               (self.cost_limits.get("max_actual_llm_calls") or 0) > 0 else "BOOTSTRAP_REQUIRED")
        funnel = {
            "raw_universe": universe.get("raw_count", total),
            "accepted_universe": total,
            "market_ready": len(feature_rows),
            "preliminary_survivors": len(market_survivors),
            "fundamental_hydrated": sum(self._fundamental_ready(item) for item in market_survivors),
            "final_fuel_pass": sum(item.gate_results.get("fuel_gate") == "PASS" for item in market_survivors),
            "capital_preflight_requested": preflight_total,
            "capital_preflight_success": capital_ready,
            "p1": len(p1_candidates), "p2": len(secondary_candidates),
            "watch": len(watch_candidates), "rejected": len(rejected_candidates),
            "deep_analyzed": 0, "certified": 0, "final": "NONE",
        }
        result = DiscoveryResult(run_id, status, certification, context, coverage,
                                 DiscoveryMarketRegimeEngine().evaluate_with_benchmark_returns(feature_rows, benchmark_returns),
                                 sectors, p1_candidates[:shortlist_n],
                                 rejection_counts=universe["rejected"],
                                 scanner_counts={scanner.name: sum(scanner.name in item.scanner_hits for item in feature_rows)
                                                 for scanner in self.scanners},
                                 api_telemetry={"quote_batches": int(quote_counter),
                                                "bar_fetches": int(bar_counter),
                                 "fundamental_calls": fundamental_calls,
                                                 "capital_preflight_calls": capital_calls,
                                                "benchmark_tickers": sorted(benchmark_returns)},
                                 all_candidates=ranked,
                                 watch_candidates=[item.to_dict() for item in watch_candidates],
                                 rejected_candidates=[item.to_dict() for item in rejected_candidates],
                                 budget_status=budget.snapshot(), market_scan_status=market_scan_status,
                                 enrichment_status=enrichment_status, deep_handoff_status=deep_handoff_status)
        result.api_telemetry["funnel"] = funnel
        result.report_path = self._write_report(result)
        telemetry_started = started
        telemetry_finished = now_iso()
        self.store.save_provider_call(result.run_id, "MARKET", "BATCH_QUOTES", 1,
                                      len(tickers), len(quotes), len(tickers) - len(quotes), 0,
                                      telemetry_started, telemetry_finished,
                                      {"run_local": True})
        self.store.save_provider_call(result.run_id, "MARKET", "DAILY_BARS", 1,
                                      len(records), len(feature_rows), max(0, len(records) - len(feature_rows)),
                                      bar_cache_hits, telemetry_started, telemetry_finished,
                                      {"run_local": True})
        if fundamental_rows:
            self.store.save_provider_call(result.run_id, "SEC_COMPANYFACTS", "FUNDAMENTALS", 1,
                                          len(market_survivors), fundamental_calls,
                                          max(0, len(market_survivors) - fundamental_calls), 0,
                                          telemetry_started, telemetry_finished, {"run_local": True})
        if preflight_rows and self.capital_preflight_provider:
            self.store.save_provider_call(result.run_id, "SEC_EDGAR", "CAPITAL_PREFLIGHT", 1,
                                          len(preflight_rows), capital_calls,
                                          max(0, len(preflight_rows) - capital_calls), 0,
                                          telemetry_started, telemetry_finished, {"run_local": True})
        # A scan, even when enabled and non-shadow, never promotes children.
        # Promotion is a separate explicit command handled by the parent
        # orchestrator.
        result.report_path = self._write_report(result)
        self.store.save_run(result, started, now_iso())
        return result

    @staticmethod
    def _counter(provider, numeric_name: str, sequence_name: str) -> int:
        value = getattr(provider, numeric_name, None)
        if isinstance(value, (int, float)):
            return int(value)
        sequence = getattr(provider, sequence_name, None)
        return len(sequence) if sequence is not None else 0

    @staticmethod
    def _diversified_prefix(candidates, limit: int, max_sector: int,
                            max_theme: int, max_size: int):
        """Use diversity to order enrichment, then fill remaining capacity.

        Hard caps belong to the expensive preflight slice; Layer-B hydration
        still fills its configured survivor budget so one sector cannot hide
        the existence of other candidates while small caps remain eligible.
        """
        selected = diversity_filter(candidates, max_sector, max_theme, max_size,
                                     sort_by="preliminary_priority_score")
        chosen = {item.security.ticker for item in selected}
        selected.extend(item for item in candidates if item.security.ticker not in chosen)
        return selected[:limit]

    @staticmethod
    def _capital_preflight_candidates(market_survivors, limit: int, diversity: dict[str, Any]):
        """Select SEC preflight rows only from candidates still eligible.

        Fundamental hydration is intentionally cheap relative to targeted SEC
        evidence.  A candidate that already failed the final fuel gate must
        never consume a capital-preflight slot, even when its stale/composite
        score is high.
        """
        eligible = [item for item in market_survivors
                    if item.eligibility == "ELIGIBLE"
                    and item.gate_results.get("fuel_gate") == "PASS"
                    and item.gate_results.get("fundamental_hydration_status") == "READY"
                    and item.gate_results.get("final_candidate_gate") == "PASS"]
        return diversity_filter(
            sorted(eligible, key=lambda item: (-item.composite_score, item.security.ticker)),
            int(diversity.get("max_same_sector", limit)),
            int(diversity.get("max_same_theme", limit)),
            int(diversity.get("max_same_size_bucket", limit)))[:limit]

    def _apply_market_paths(self, candidates, context, as_of, demote_no_hit: bool = True) -> None:
        sectors = rank_sectors(candidates)
        top_sectors = {row["sector"] for row in sectors[:3] if row.get("rotation_score") is not None}
        sector_by_name = {row["sector"]: row for row in sectors}
        for candidate in candidates:
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
            candidate.paths = [path for path in candidate.paths if path not in {"BLIND", "TOP_DOWN"}]
            candidate.paths.append("BLIND")
            if candidate.security.sector_canonical in top_sectors:
                candidate.paths.append("TOP_DOWN")
            candidate.scanner_hits = []
            for scanner in self.scanners:
                result = scanner.evaluate(candidate, context)
                if result.hit:
                    candidate.scanner_hits.append(result.scanner_name)
            if demote_no_hit and candidate.eligibility == "ELIGIBLE" and not candidate.scanner_hits:
                candidate.eligibility = "REVIEW_REQUIRED"
                if "NO_SCANNER_HIT" not in candidate.risk_flags:
                    candidate.risk_flags.append("NO_SCANNER_HIT")

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
            if budget and not self._llm_budget_allows(budget):
                outputs.append({"ticker": candidate.security.ticker, "status": "BLOCKED",
                                "reason_codes": ["MAX_LLM_CALLS_PER_DISCOVERY"], "certified": False})
                continue
            preflight = self.evidence_preflight.evaluate(candidate)
            if preflight["status"] != "READY":
                candidate.risk_flags.extend(preflight["reason_codes"])
                outputs.append({"ticker": candidate.security.ticker, "status": "BLOCKED",
                                "certified": False, **preflight})
                continue
            analysis_started_at = now_iso()
            child = self.handoff(make_child_request(request, candidate.security.ticker))
            child_run_id = str(child.get("run_id", "")) if isinstance(child, dict) else ""
            usage = self.database.usage_summary(child_run_id) if child_run_id else {}
            if budget:
                budget.consume("max_child_analysis_runs")
                budget.record_usage(usage)
            analysis_finished_at = now_iso()
            if child_run_id:
                self.store.save_analysis_link(result.run_id, candidate.security.ticker, child_run_id, analysis_finished_at,
                                              promotion_requested_at=analysis_started_at,
                                              analysis_started_at=analysis_started_at,
                                              analysis_finished_at=analysis_finished_at,
                                              actual_llm_calls=int(usage.get("llm_calls", 0) or 0),
                                              actual_cost_usd=float(usage.get("estimated_cost_usd", 0) or 0))
                result.analysis_links.append({"ticker": candidate.security.ticker,
                                              "analysis_run_id": child_run_id,
                                              "actual_llm_calls": int(usage.get("llm_calls", 0) or 0),
                                              "actual_cost_usd": float(usage.get("estimated_cost_usd", 0) or 0)})
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
            research_obj = child.get("research") if isinstance(child, dict) else None
            child_scores, score_provenance = self._certified_scorecard(child, research_obj, child_run_id, result.context.discovery_as_of)
            final_scorecard_config = self.config.get("discovery", {}).get("final_scorecard", {})
            scorecard = scorecard_metadata(
                child_scores, float(final_scorecard_config.get("min_coverage_pct", 75)))
            rr = getattr(plan, "reward_risk", None) if plan is not None else None
            trade_plan_valid = bool(plan is not None and rr is not None and float(rr) > 0)
            unresolved = bool(getattr(certification, "required_data_failures", []) or
                              getattr(certification, "important_data_warnings", []))
            certified = certified and trade_plan_valid and not unresolved
            outputs.append({"ticker": candidate.security.ticker, "status": "HANDOFF",
                            "analysis_run_id": child_run_id, "certified": certified,
                            "certification_status": getattr(certification, "certification_status", "UNKNOWN"),
                            "decision": child_decision, "scores": child_scores,
                            "score_provenance": score_provenance,
                            "sector": candidate.security.sector_canonical,
                            **scorecard,
                            "min_scorecard_coverage_pct": float(final_scorecard_config.get("min_coverage_pct", 75)),
                            "min_reward_risk": float(final_scorecard_config.get("min_reward_risk", 1.5)),
                            "risk_hard_filter_pass": bool(getattr(child_risk, "hard_filter_pass", False)),
                            "trade_plan_valid": trade_plan_valid,
                            "market_fresh": bool(child.get("market_fresh", True)) if isinstance(child, dict) else False,
                            "no_material_unresolved_blocker": not unresolved,
                            "reward_risk": rr})
            if budget and budget.snapshot().get("exceeded"):
                outputs[-1]["budget_reason_codes"] = ["BUDGET_OVERSHOOT_WITHIN_CHILD"]
        return outputs

    def promote(self, source_run_id: str, request, limit: int = 0) -> DiscoveryResult:
        """Explicitly promote a stored shadow run; scans never call this path."""
        payload = self.store.latest(source_run_id)
        if not payload:
            raise ValueError("DISCOVERY_SOURCE_RUN_NOT_FOUND")
        result = DiscoveryResult.from_dict(payload)
        if result.status in {DiscoveryStatus.BLOCKED_DATA.value, DiscoveryStatus.BLOCKED_COVERAGE.value}:
            result.final_selection_reason_codes = ["SOURCE_RUN_NOT_COMPLETED"]
            return result
        as_of = now_iso()
        candidates = []
        for candidate in result.candidates:
            allowed, reason = can_promote(candidate.to_dict(), as_of)
            if candidate.discovery_bucket != "P1_DEEP_ANALYSIS":
                candidate.promotion_status = "BLOCKED"
                candidate.promotion_reason_codes = ["NOT_P1_DEEP_ANALYSIS"]
            elif candidate.eligibility != "ELIGIBLE":
                candidate.promotion_status = "BLOCKED"
                candidate.promotion_reason_codes = ["NOT_ELIGIBLE"]
            elif candidate.gate_results.get("fuel_gate") != "PASS":
                candidate.promotion_status = "BLOCKED"
                candidate.promotion_reason_codes = ["FUEL_GATE_NOT_PASS"]
            elif not self._capital_preflight_ready(candidate):
                candidate.promotion_status = "BLOCKED"
                candidate.promotion_reason_codes = ["CAPITAL_PREFLIGHT_NOT_READY"]
            elif not allowed:
                candidate.promotion_status = "BLOCKED"
                candidate.promotion_reason_codes = [reason]
            else:
                candidate.promotion_status = "READY"
                candidate.promotion_reason_codes = ["PROMOTION_ALLOWED"]
                candidates.append(candidate)
        lifecycle_by_ticker = {candidate.security.ticker: candidate for candidate in result.candidates}
        for snapshot in result.all_candidates:
            updated = lifecycle_by_ticker.get(snapshot.security.ticker)
            if updated is not None:
                snapshot.promotion_status = updated.promotion_status
                snapshot.promotion_reason_codes = list(updated.promotion_reason_codes)
        result.candidates = candidates[:limit or len(candidates)]
        budget = DiscoveryBudgetGuard(self.cost_limits)
        result.deep_analysis_results = self.deep_analyze(result, request, budget)
        result.certified_candidates = [row for row in result.deep_analysis_results if row.get("certified")]
        result.blocked_candidates = [row for row in result.deep_analysis_results if not row.get("certified")]
        result.final_selection = final_selection(result.certified_candidates, self._portfolio_context())
        result.final_selection_status = "EXECUTABLE" if result.final_selection != "NONE" else "NONE"
        result.final_selection_reason_codes = (["CERTIFIED_CHILD_SELECTED"] if result.final_selection != "NONE"
                                               else ["NO_CERTIFIED_CHILD"])
        result.deep_handoff_status = "DEEP_HANDOFF_READY" if result.deep_analysis_results else "BOOTSTRAP_REQUIRED"
        result.budget_status = budget.snapshot()
        result.budget_status["overshoot_semantics"] = "SOFT_PARENT_BUDGET_HARD_CHILD_UNENFORCED"
        if result.budget_status.get("exceeded"):
            result.final_selection_reason_codes.append("BUDGET_OVERSHOOT_WITHIN_CHILD")
        measured = budget.measured()
        result.actual_llm_calls = int(measured["actual_llm_calls"])
        result.actual_input_tokens = int(measured["actual_input_tokens"])
        result.actual_output_tokens = int(measured["actual_output_tokens"])
        result.actual_cost_usd = float(measured["actual_cost_usd"])
        funnel = result.api_telemetry.setdefault("funnel", {})
        funnel["deep_analyzed"] = len(result.deep_analysis_results)
        funnel["certified"] = len(result.certified_candidates)
        funnel["final"] = result.final_selection
        result.status = DiscoveryStatus.COMPLETED.value if result.final_selection != "NONE" else DiscoveryStatus.FINAL_NONE.value
        self.store.save_run(result, result.context.discovery_as_of, now_iso())
        return result

    def _portfolio_context(self) -> dict[str, Any]:
        try:
            state = self.database.paper_account_state()
        except Exception:
            return {
                "portfolio_context_status": "UNKNOWN",
                "portfolio_context_reason_codes": ["PORTFOLIO_CONTEXT_UNKNOWN"],
                "remaining_risk_budget_usd": 0.0,
            }
        equity = max(0.01, float(state.get("equity", 0) or 0))
        def pct_map(values):
            return {key: round(float(value or 0) / equity * 100, 4)
                    for key, value in (values or {}).items()}
        return {
            "portfolio_context_status": "READY",
            "portfolio_context_reason_codes": [],
            "remaining_risk_budget_usd": max(0.0, float(state.get("risk_budget", 0)) -
                                               float(state.get("portfolio_risk_used", 0))),
            "existing_sector_exposure_pct": pct_map(state.get("sector_exposure")),
            "pending_sector_exposure_pct": pct_map(state.get("pending_sector_committed_exposure")),
            "existing_ticker_exposure_pct": pct_map(state.get("ticker_exposure")),
            "pending_ticker_exposure_pct": pct_map(state.get("pending_ticker_committed_exposure")),
            "total_committed_exposure_pct": round(
                (float(state.get("current_exposure", 0) or 0) +
                 float(state.get("pending_committed_exposure", 0) or 0)) / equity * 100, 4),
            "sector_cap_pct": float(self.config.get("paper", {}).get("max_sector_exposure_pct", 25)),
        }

    @staticmethod
    def _llm_budget_allows(budget: DiscoveryBudgetGuard) -> bool:
        if "max_actual_llm_calls" in budget.limits:
            if not budget.allow_usage("max_actual_llm_calls", "actual_llm_calls", 1):
                return False
            for limit_name, usage_name in (
                ("max_actual_input_tokens", "actual_input_tokens"),
                ("max_actual_output_tokens", "actual_output_tokens"),
                ("max_actual_cost_usd", "actual_cost_usd"),
            ):
                limit = budget.limits.get(limit_name)
                if limit is not None and (limit <= 0 or budget.used.get(usage_name, 0) >= limit):
                    return False
            return True
        return budget.allow("max_llm_calls", 1)

    @staticmethod
    def _certified_scorecard(child: dict[str, Any], research_obj: Any,
                             source_run_id: str, source_as_of: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Copy actual scorecard values; missing axes stay absent/UNKNOWN."""
        score_details = getattr(research_obj, "score_details", {}) or {}
        scores: dict[str, Any] = {}
        provenance: dict[str, Any] = {}
        if research_obj is not None:
            for axis in ("signal_strength", "catalyst_quality", "expectation_gap", "surge_elasticity",
                         "entry_readiness", "strategy_fit"):
                raw = getattr(research_obj, axis, None)
                if raw is not None:
                    scores[axis] = float(raw)
                    provenance[axis] = {"source_run_id": source_run_id, "source_component": "RESEARCH",
                                       "source_field": axis, "source_as_of": source_as_of}
            # ResearchAnalysis exposes capital_structure_risk.  The final
            # scorecard uses the inverse safety axis; this is a deterministic
            # semantic transformation, not a proxy or a confidence reuse.
            risk = getattr(research_obj, "capital_structure_risk", None)
            if isinstance(risk, (int, float)) and not isinstance(risk, bool):
                risk = max(0.0, min(100.0, float(risk)))
                scores["capital_structure_safety"] = 100.0 - risk
                provenance["capital_structure_safety"] = {
                    "source_run_id": source_run_id,
                    "source_component": "RESEARCH",
                    "source_field": "capital_structure_risk",
                    "source_as_of": source_as_of,
                    "transformation": "100 - capital_structure_risk",
                }
            for axis in ("capital_structure_safety", "data_confidence"):
                if axis == "capital_structure_safety" and axis in scores:
                    continue
                detail = score_details.get(axis)
                if isinstance(detail, dict) and detail.get("value") is not None:
                    scores[axis] = float(detail["value"])
                    provenance[axis] = {"source_run_id": source_run_id,
                                       "source_component": detail.get("source_component", "RESEARCH_SCORECARD"),
                                       "source_field": detail.get("source_field", axis),
                                       "source_as_of": detail.get("source_as_of", source_as_of)}
        decision = child.get("decision") if isinstance(child, dict) else None
        plan = getattr(child.get("risk"), "trade_plan", None) if isinstance(child, dict) else None
        plan = getattr(decision, "trade_plan", None) or plan
        if plan is not None and getattr(plan, "reward_risk", None) is not None:
            scores["reward_risk"] = float(plan.reward_risk)
            provenance["reward_risk"] = {"source_run_id": source_run_id, "source_component": "TRADE_PLAN",
                                          "source_field": "reward_risk", "source_as_of": source_as_of}
        certification = child.get("certification") if isinstance(child, dict) else None
        if getattr(certification, "decision_confidence", None) is not None:
            scores["decision_confidence"] = float(certification.decision_confidence)
            provenance["decision_confidence"] = {"source_run_id": source_run_id,
                                                  "source_component": "CERTIFICATION",
                                                  "source_field": "decision_confidence", "source_as_of": source_as_of}
        return scores, provenance

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
                 f"- Market scan: `{result.market_scan_status}` / enrichment: `{result.enrichment_status}` / deep handoff: `{result.deep_handoff_status}`",
                 f"- Coverage: `{result.coverage.market_coverage_pct:.2f}% market / {result.coverage.feature_coverage_pct:.2f}% feature / {result.coverage.fundamental_enrichment_coverage_pct:.2f}% fundamental / {result.coverage.capital_preflight_coverage_pct:.2f}% capital`",
                 f"- Capital preflight scope: `{result.coverage.capital_preflight_scope_pct:.2f}% of market survivors`",
                 f"- Identity/Sector: `{result.coverage.identity_coverage_pct:.2f}% / {result.coverage.sector_coverage_pct:.2f}%`",
                 f"- Regime: `{result.regime.get('regime')}`", "", "## Pipeline", "",
                 f"- Universe accepted: `{result.coverage.eligible_universe_count}`",
                 f"- Market data: `{result.coverage.market_data_loaded_count}`",
                 f"- Feature ready: `{result.coverage.feature_ready_count}`",
                 f"- P1: `{len(result.candidates)}` / P2: `{len([item for item in result.all_candidates if item.discovery_bucket == 'P2_SECONDARY'])}` / Watch: `{len(result.watch_candidates)}` / Rejected: `{len(result.rejected_candidates)}`",
                 f"- Actual LLM: `{result.actual_llm_calls}` calls / `{result.actual_input_tokens}` input / `{result.actual_output_tokens}` output / `${result.actual_cost_usd:.4f}`",
                 f"- Funnel: `{result.api_telemetry.get('funnel', {})}`",
                 "", "## Candidates"]
        for candidate in result.candidates:
            lines.append(f"- `{candidate.security.ticker}` — {candidate.discovery_bucket} / {candidate.stage} / score={candidate.composite_score:.2f}; unknown={','.join(candidate.unknown_fields) or 'none'}")
        lines.extend(["", "## Final", "", f"- Final selection: `{result.final_selection}`",
                      f"- Final status: `{result.final_selection_status}`",
                      f"- Reasons: `{','.join(result.final_selection_reason_codes) or 'none'}`"])
        if result.error_code:
            lines.extend(["", f"## Block reason", "", f"`{result.error_code}`"])
        path = write_run_report(self.config.get("report_dir", "data/reports"), "\n".join(lines), "DISCOVERY", result.run_id)
        return str(path)
