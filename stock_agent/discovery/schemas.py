from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar


class DiscoveryStatus(str, Enum):
    QUEUED = "QUEUED"
    BOOTSTRAP_REQUIRED = "BOOTSTRAP_REQUIRED"
    UNIVERSE_LOADING = "UNIVERSE_LOADING"
    MARKET_INGESTING = "MARKET_INGESTING"
    DATA_VALIDATING = "DATA_VALIDATING"
    REGIME_ANALYSIS = "REGIME_ANALYSIS"
    SECTOR_RANKING = "SECTOR_RANKING"
    SCREENING = "SCREENING"
    FEATURE_SCORING = "FEATURE_SCORING"
    SHORTLISTING = "SHORTLISTING"
    EVIDENCE_PREFLIGHT = "EVIDENCE_PREFLIGHT"
    DEEP_ANALYSIS = "DEEP_ANALYSIS"
    TOURNAMENT = "TOURNAMENT"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    COMPLETED_SHADOW_MARKET_ONLY = "COMPLETED_SHADOW_MARKET_ONLY"
    COMPLETED_SHADOW_ENRICHED = "COMPLETED_SHADOW_ENRICHED"
    READY_FOR_DEEP_HANDOFF = "READY_FOR_DEEP_HANDOFF"
    FINAL_NONE = "FINAL_NONE"
    BLOCKED_DATA = "BLOCKED_DATA"
    BLOCKED_COVERAGE = "BLOCKED_COVERAGE"
    BLOCKED_MARKET_DATA = "BLOCKED_MARKET_DATA"
    BLOCKED_COST = "BLOCKED_COST"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class UnknownState(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN_NOT_AVAILABLE = "UNKNOWN_NOT_AVAILABLE"
    UNKNOWN_NOT_FETCHED = "UNKNOWN_NOT_FETCHED"
    UNKNOWN_FETCH_FAILED = "UNKNOWN_FETCH_FAILED"
    UNKNOWN_PARSE_FAILED = "UNKNOWN_PARSE_FAILED"
    UNKNOWN_CONFLICTED = "UNKNOWN_CONFLICTED"
    UNKNOWN_NOT_APPLICABLE = "UNKNOWN_NOT_APPLICABLE"
    STALE = "STALE"


T = TypeVar("T")


@dataclass(frozen=True)
class FieldValue(Generic[T]):
    value: T | None
    state: str = UnknownState.KNOWN.value
    source: str = ""
    observed_at: str = ""
    filed_at: str = ""
    event_at: str = ""
    ingested_at: str = ""
    calculation_version: str = ""
    source_ids: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.state == UnknownState.KNOWN.value

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_ids"] = list(self.source_ids)
        return payload


@dataclass(frozen=True)
class SecurityMasterRecord:
    security_id: str
    ticker: str
    company_name: str
    cik: str = ""
    exchange: str = ""
    security_type: str = "COMMON_STOCK"
    country: str = "US"
    is_adr: bool | None = False
    is_etf: bool | None = False
    is_unit: bool | None = False
    is_warrant: bool | None = False
    is_preferred: bool | None = False
    is_common_stock: bool | None = True
    listing_date: str = ""
    delisting_date: str = ""
    sector_canonical: str = "UNKNOWN"
    industry_canonical: str = "UNKNOWN"
    sic: str = ""
    sic_description: str = ""
    active_status: str = "ACTIVE"
    source: str = ""
    source_as_of: str = ""
    ingested_at: str = ""
    themes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["themes"] = list(self.themes)
        return payload


@dataclass(frozen=True)
class DailyBar:
    ticker: str
    session_date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adjusted_close: float | None
    volume: int | None
    source: str
    observed_at: str
    ingested_at: str
    quality_status: str = "OK"

    @property
    def usable(self) -> bool:
        return (self.quality_status in {"OK", "COMPLETE"}
                and self.close is not None and self.close > 0
                and self.volume is not None and self.volume >= 0)


@dataclass(frozen=True)
class MarketQuote:
    ticker: str
    current: FieldValue[float]
    market_cap_usd: FieldValue[float]
    observed_at: str
    source: str
    market_session: str = "UNKNOWN"


@dataclass
class CandidateFeatureSnapshot:
    security: SecurityMasterRecord
    as_of: str
    discovery_run_id: str
    feature_version: str
    fields: dict[str, FieldValue[Any]] = field(default_factory=dict)
    fuel_events: list[dict[str, Any]] = field(default_factory=list)
    scanner_hits: list[str] = field(default_factory=list)
    signal_families: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    stage: str = "DISCOVERY_STAGE_UNKNOWN"
    eligibility: str = "REVIEW_REQUIRED"
    discovery_bucket: str = "REJECT"
    gate_results: dict[str, str] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    score_coverage_pct: float = 0.0
    data_confidence: float = 0.0
    composite_score: float = 0.0
    created_at: str = ""
    last_validated_at: str = ""
    expires_at: str = ""
    promotion_conditions: list[str] = field(default_factory=list)
    invalidation_conditions: list[str] = field(default_factory=list)
    # Enrichment ordering is deliberately separate from the final investment
    # score.  Market cap is retained only as a size/liquidity context field.
    preliminary_priority_score: float | None = None
    size_bucket: str = "UNKNOWN"
    fundamental_rank: int | None = None
    capital_preflight_rank: int | None = None
    promotion_status: str = "NOT_REQUESTED"
    promotion_reason_codes: list[str] = field(default_factory=list)
    first_seen_at: str = ""
    last_seen_at: str = ""
    fuel_changed: bool = False
    stage_changed: bool = False
    score_changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["security"] = self.security.to_dict()
        payload["fields"] = {key: value.to_dict() for key, value in self.fields.items()}
        return payload

    def canonical_hash(self) -> str:
        raw = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CandidateFeatureSnapshot":
        security_payload = dict(payload.get("security") or {})
        security_payload["themes"] = tuple(security_payload.get("themes") or ())
        security = SecurityMasterRecord(**security_payload)
        fields = {}
        for name, raw in (payload.get("fields") or {}).items():
            raw = dict(raw)
            raw["source_ids"] = tuple(raw.get("source_ids") or ())
            fields[name] = FieldValue(**raw)
        values = {key: value for key, value in payload.items()
                  if key not in {"security", "fields"}}
        return cls(security=security, fields=fields, **values)


@dataclass(frozen=True)
class CoverageMetrics:
    eligible_universe_count: int
    market_data_loaded_count: int
    feature_ready_count: int
    sector_mapped_count: int
    fundamental_ready_count: int
    market_coverage_pct: float
    feature_coverage_pct: float
    sector_coverage_pct: float
    fundamental_coverage_pct: float
    identity_coverage_pct: float = 0.0
    fundamental_enrichment_coverage_pct: float = 0.0
    capital_preflight_coverage_pct: float = 0.0
    capital_preflight_scope_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryContext:
    discovery_run_id: str
    mode: str
    requested_sector: str
    intensity: str
    discovery_as_of: str
    quote_batch_cutoff: str
    completed_bar_cutoff: str
    fundamental_cutoff: str
    evidence_cutoff: str
    rule_version: str
    feature_version: str
    code_sha: str
    universe_snapshot_id: str
    market_session: str = "UNKNOWN"
    shadow: bool = True


@dataclass(frozen=True)
class ScannerResult:
    scanner_name: str
    scanner_version: str
    hit: bool
    strength_0_100: float
    required_pass: bool
    vetoed: bool
    reason_codes: tuple[str, ...] = ()
    signal_families: tuple[str, ...] = ()
    fuel_events: tuple[dict[str, Any], ...] = ()
    risk_flags: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("reason_codes", "signal_families", "fuel_events", "risk_flags", "unknown_fields"):
            if isinstance(payload[key], tuple):
                payload[key] = list(payload[key])
        return payload


@dataclass
class DiscoveryResult:
    run_id: str
    status: str
    certification_status: str
    context: DiscoveryContext
    coverage: CoverageMetrics
    regime: dict[str, Any]
    sector_snapshots: list[dict[str, Any]]
    candidates: list[CandidateFeatureSnapshot]
    all_candidates: list[CandidateFeatureSnapshot] = field(default_factory=list)
    rejection_counts: dict[str, int] = field(default_factory=dict)
    scanner_counts: dict[str, int] = field(default_factory=dict)
    api_telemetry: dict[str, Any] = field(default_factory=dict)
    report_path: str = ""
    error_code: str = ""
    deep_analysis_results: list[dict[str, Any]] = field(default_factory=list)
    certified_candidates: list[dict[str, Any]] = field(default_factory=list)
    blocked_candidates: list[dict[str, Any]] = field(default_factory=list)
    watch_candidates: list[dict[str, Any]] = field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = field(default_factory=list)
    analysis_links: list[dict[str, Any]] = field(default_factory=list)
    final_selection: str = "NONE"
    final_selection_status: str = "NONE"
    final_selection_reason_codes: list[str] = field(default_factory=list)
    budget_status: dict[str, Any] = field(default_factory=dict)
    market_scan_status: str = "UNKNOWN"
    enrichment_status: str = "UNKNOWN"
    deep_handoff_status: str = "UNKNOWN"
    actual_llm_calls: int = 0
    actual_input_tokens: int = 0
    actual_output_tokens: int = 0
    actual_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "status": self.status,
                "certification_status": self.certification_status,
                "context": asdict(self.context), "coverage": self.coverage.to_dict(),
                "regime": self.regime, "sector_snapshots": self.sector_snapshots,
                "candidates": [candidate.to_dict() for candidate in self.candidates],
                "all_candidates": [candidate.to_dict() for candidate in self.all_candidates],
                "rejection_counts": self.rejection_counts, "scanner_counts": self.scanner_counts,
                "api_telemetry": self.api_telemetry, "report_path": self.report_path,
                "error_code": self.error_code,
                "deep_analysis_results": self.deep_analysis_results,
                "certified_candidates": self.certified_candidates,
                "blocked_candidates": self.blocked_candidates,
                "watch_candidates": self.watch_candidates,
                "rejected_candidates": self.rejected_candidates,
                "analysis_links": self.analysis_links,
                "final_selection": self.final_selection,
                "final_selection_status": self.final_selection_status,
                "final_selection_reason_codes": self.final_selection_reason_codes,
                "budget_status": self.budget_status,
                "market_scan_status": self.market_scan_status,
                "enrichment_status": self.enrichment_status,
                "deep_handoff_status": self.deep_handoff_status,
                "actual_llm_calls": self.actual_llm_calls,
                "actual_input_tokens": self.actual_input_tokens,
                "actual_output_tokens": self.actual_output_tokens,
                "actual_cost_usd": self.actual_cost_usd}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DiscoveryResult":
        context = DiscoveryContext(**payload["context"])
        coverage = CoverageMetrics(**payload["coverage"])
        candidates = [CandidateFeatureSnapshot.from_dict(item) for item in payload.get("candidates", [])]
        all_candidates = [CandidateFeatureSnapshot.from_dict(item) for item in payload.get("all_candidates", [])]
        values = {key: value for key, value in payload.items()
                  if key not in {"context", "coverage", "candidates", "all_candidates"}}
        return cls(context=context, coverage=coverage, candidates=candidates,
                   all_candidates=all_candidates, **values)
