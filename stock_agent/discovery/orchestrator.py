from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..reports import write_run_report
from ..schemas import now_iso
from .diversity import diversity_filter
from .features import FEATURE_VERSION, build_candidate
from .fuel import FuelEngine, infer_fuel_events
from .gates import DiscoveryGateRules, global_gate
from .handoff import EvidencePreflight, make_child_request
from .ingestion import EmptyDiscoveryMarketDataProvider
from .pareto import pareto_filter
from .ranking import rank_candidates
from .regime import DiscoveryMarketRegimeEngine
from .schemas import CoverageMetrics, DiscoveryContext, DiscoveryResult, DiscoveryStatus
from .scanners import (AIBottleneckExpansionScanner, CustomerDiversificationScanner,
                        GeneralInflectionScanner, InsiderBuybackScanner,
                        MomentumInflectionScanner, OfferingSecondaryRecoveryScanner,
                        PolicyDefenseEnergySecurityScanner, PostEarningsRevisionDriftScanner,
                        ProfitabilityInflectionScanner, RefinancingDistressRemovalScanner,
                        TurnaroundScanner)
from .sectors import rank_sectors
from .stage import DiscoveryStageEngine, DiscoveryStageRules
from .store import DiscoveryStore
from .universe import EmptySecurityMasterProvider, UniverseIntegrityEngine


class DiscoveryOrchestrator:
    """Phase-1 deterministic pipeline.  It is read-only with respect to PAPER."""

    def __init__(self, database, config: dict[str, Any], security_master=None,
                 market_data=None, fundamental_provider=None, handoff: Callable | None = None):
        self.database = database
        self.config = config
        self.security_master = security_master or EmptySecurityMasterProvider()
        self.market_data = market_data or EmptyDiscoveryMarketDataProvider()
        self.fundamental_provider = fundamental_provider
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
        feature_rows = []
        fundamental_rows = self.fundamental_provider.fundamentals(tickers, as_of) if self.fundamental_provider else {}
        for record in records:
            quote = quotes.get(record.ticker)
            if quote is None:
                continue
            bars = self.store.load_bars(record.ticker, as_of)
            if not bars:
                bars = self.market_data.daily_bars(record.ticker, as_of)
                self.store.save_bars(bars)
            candidate = build_candidate(record, quote, bars, run_id, as_of, fundamental_rows.get(record.ticker))
            candidate.fuel_events = infer_fuel_events(candidate)
            self.stage_engine.apply(candidate)
            self.fuel_engine.evaluate(candidate)
            eligibility, reasons = global_gate(candidate, self.gate_rules)
            candidate.eligibility = eligibility
            candidate.gate_results["global_gate"] = "PASS" if eligibility == "ELIGIBLE" else eligibility
            for reason in reasons:
                candidate.risk_flags.append(reason)
            feature_rows.append(candidate)
        sectors = rank_sectors(feature_rows)
        top_sectors = {row["sector"] for row in sectors[:3] if row.get("rotation_score") is not None}
        for candidate in feature_rows:
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
        shortlisted = diversity_filter([item for item in ranked if item.discovery_bucket != "REJECT"],
                                       int(self.config.get("discovery", {}).get("diversity", {}).get("max_same_sector", 2)),
                                       int(self.config.get("discovery", {}).get("diversity", {}).get("max_same_theme", 2)))[:shortlist_n]
        market_loaded = len(quotes)
        feature_ready = sum(self._feature_ready(item) for item in feature_rows)
        sector_mapped = sum(item.security.sector_canonical != "UNKNOWN" for item in feature_rows)
        fundamental_ready = sum(self._fundamental_ready(item) for item in feature_rows)
        total = len(records)
        coverage = CoverageMetrics(total, market_loaded, feature_ready, sector_mapped, fundamental_ready,
                                   round(market_loaded / total * 100, 4), round(feature_ready / total * 100, 4),
                                   round(sector_mapped / total * 100, 4), round(fundamental_ready / total * 100, 4))
        coverage_rules = self.config.get("discovery", {}).get("coverage", {})
        min_market = float(coverage_rules.get("market_min_pct", 95))
        min_feature = float(coverage_rules.get("feature_min_pct", 90))
        if coverage.market_coverage_pct < min_market or coverage.feature_coverage_pct < min_feature:
            status, certification = DiscoveryStatus.BLOCKED_COVERAGE.value, "BLOCKED_COVERAGE"
        else:
            status, certification = DiscoveryStatus.COMPLETED.value, "SHADOW_ONLY" if shadow else "READY_FOR_HANDOFF"
        result = DiscoveryResult(run_id, status, certification, context, coverage,
                                 DiscoveryMarketRegimeEngine().evaluate(feature_rows, {"SPY", "QQQ", "IWM"}),
                                 sectors, shortlisted,
                                 rejection_counts=universe["rejected"],
                                 scanner_counts={scanner.name: sum(scanner.name in item.scanner_hits for item in feature_rows)
                                                 for scanner in self.scanners},
                                 api_telemetry={"quote_batches": 1, "bar_fetches": len(self.market_data.bar_calls)
                                                if hasattr(self.market_data, "bar_calls") else None},
                                 all_candidates=ranked)
        result.report_path = self._write_report(result)
        self.store.save_run(result, started, now_iso())
        if not shadow and request is not None and self.handoff:
            self.deep_analyze(result, request)
        return result

    def deep_analyze(self, result: DiscoveryResult, request) -> list[dict[str, Any]]:
        """Explicit non-shadow handoff through the existing analyze_request call path."""
        limit = int(self.config.get("discovery", {}).get("shortlist", {}).get("deep_analysis_n", 3))
        outputs = []
        for candidate in result.candidates[:limit]:
            preflight = self.evidence_preflight.evaluate(candidate)
            if preflight["status"] != "READY":
                candidate.risk_flags.extend(preflight["reason_codes"])
                outputs.append({"ticker": candidate.security.ticker, "status": "BLOCKED", **preflight})
                continue
            child = self.handoff(make_child_request(request, candidate.security.ticker))
            child_run_id = str(child.get("run_id", "")) if isinstance(child, dict) else ""
            if child_run_id:
                self.store.save_analysis_link(result.run_id, candidate.security.ticker, child_run_id, now_iso())
            outputs.append({"ticker": candidate.security.ticker, "status": "HANDOFF", "analysis_run_id": child_run_id})
        return outputs

    @staticmethod
    def _feature_ready(candidate) -> bool:
        return candidate.fields.get("current_price") is not None and candidate.fields.get("bar_count") is not None and candidate.fields["bar_count"].known and (candidate.fields["bar_count"].value or 0) >= 20

    @staticmethod
    def _fundamental_ready(candidate) -> bool:
        return any(name in candidate.fields and candidate.fields[name].known for name in ("revenue_growth_acceleration", "operating_cash_flow", "margin_delta"))

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
