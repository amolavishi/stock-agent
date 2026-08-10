from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from enum import Enum


class Decision(str, Enum):
    BUY = "BUY"
    CONDITIONAL_BUY = "CONDITIONAL_BUY"
    HOLD = "HOLD"
    TRIM = "TRIM"
    SELL = "SELL"
    WAIT = "WAIT"
    EXCLUDE = "EXCLUDE"


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    ANALYSIS_INCOMPLETE = "ANALYSIS_INCOMPLETE"
    DATA_STALE = "DATA_STALE"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    CANCELLED = "CANCELLED"
    BUDGET_LIMIT_REACHED = "BUDGET_LIMIT_REACHED"


class ExecutionStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AnalysisStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DEADLOCK = "DEADLOCK"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CertificationStatus(str, Enum):
    CERTIFIED = "CERTIFIED"
    BLOCKED_DATA_MISSING = "BLOCKED_DATA_MISSING"
    BLOCKED_DATA_CONFLICT = "BLOCKED_DATA_CONFLICT"
    BLOCKED_EVIDENCE = "BLOCKED_EVIDENCE"
    BLOCKED_DEBATE = "BLOCKED_DEBATE"
    BLOCKED_MARKET_DATA = "BLOCKED_MARKET_DATA"
    BLOCKED_SYSTEM_INTEGRITY = "BLOCKED_SYSTEM_INTEGRITY"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class SideEffectStatus(str, Enum):
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    AUTHORIZED_PENDING = "AUTHORIZED_PENDING"
    COMMITTED = "COMMITTED"
    WITHHELD = "WITHHELD"
    CANCELLED_BEFORE_COMMIT = "CANCELLED_BEFORE_COMMIT"
    COMMITTED_BEFORE_CANCEL_REQUEST = "COMMITTED_BEFORE_CANCEL_REQUEST"


class UnknownStatus(str, Enum):
    UNKNOWN_NOT_AVAILABLE = "UNKNOWN_NOT_AVAILABLE"
    UNKNOWN_NOT_FETCHED = "UNKNOWN_NOT_FETCHED"
    UNKNOWN_FETCH_FAILED = "UNKNOWN_FETCH_FAILED"
    UNKNOWN_PARSE_FAILED = "UNKNOWN_PARSE_FAILED"
    UNKNOWN_CONFLICTED = "UNKNOWN_CONFLICTED"
    UNKNOWN_NOT_APPLICABLE = "UNKNOWN_NOT_APPLICABLE"


class AnalysisIntensity(str, Enum):
    MINIMUM = "MINIMUM"
    NORMAL = "NORMAL"
    MAXIMUM = "MAXIMUM"


class DebateStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    ROUND_ACTIVE = "ROUND_ACTIVE"
    WAITING_FOR_EVIDENCE = "WAITING_FOR_EVIDENCE"
    EVIDENCE_REVIEW_REQUIRED = "EVIDENCE_REVIEW_REQUIRED"
    BLOCKED_BY_MATERIAL_ISSUE = "BLOCKED_BY_MATERIAL_ISSUE"
    PROVISIONAL_CONSENSUS = "PROVISIONAL_CONSENSUS"
    STRESS_TEST_REQUIRED = "STRESS_TEST_REQUIRED"
    FINAL_CONSENSUS = "FINAL_CONSENSUS"
    CONSENSUS_REACHED = "CONSENSUS_REACHED"
    DEADLOCK = "DEADLOCK"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class IssueStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    DEFERRED = "DEFERRED"


class IssueSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Intent(str, Enum):
    ANALYZE = "ANALYZE"
    COMPARE = "COMPARE"
    REANALYZE = "REANALYZE"
    PRICE = "PRICE"
    PORTFOLIO = "PORTFOLIO"
    REPORT = "REPORT"
    STATUS = "STATUS"
    COST = "COST"
    CANCEL = "CANCEL"
    HELP = "HELP"
    PAPER_BUY = "PAPER_BUY"
    PAPER_SELL = "PAPER_SELL"
    PAPER_TRIM = "PAPER_TRIM"


class RequestStatus(str, Enum):
    PARSED = "PARSED"
    WAITING_CLARIFICATION = "WAITING_CLARIFICATION"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MarketRegime(str, Enum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"
    UNKNOWN = "UNKNOWN"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MarketSnapshot:
    ticker: str
    timestamp: str
    current: float
    change_1d_pct: float
    return_5d_pct: float
    return_20d_pct: float
    volume: int
    avg_20d_volume: int
    market_cap_usd: float
    ma20: float
    ma50: float
    atr_14: float
    sector_name: str = "UNKNOWN"
    stage: str = "UNKNOWN"
    source: str = "mock"
    data_quality: str = "OK"
    observed_at: str = ""
    ingested_at: str = ""
    is_mock: bool = True
    turnover_usd: float = 0.0
    snapshot_id: str = ""
    return_60d_pct: float | None = None
    ma200: float | None = None
    distance_ma20_pct: float | None = None
    distance_ma50_pct: float | None = None
    distance_ma200_pct: float | None = None
    distance_52w_high_pct: float | None = None
    relative_strength_20d_vs_qqq: float | None = None
    relative_strength_20d_vs_iwm: float | None = None
    relative_strength_20d_vs_sector: float | None = None
    candle_as_of: str = ""
    transport_status: str = ""
    quote_freshness: str = ""
    candle_freshness: str = ""
    market_session: str = "UNKNOWN"
    bar_completeness: str = ""
    volume_validity: str = ""
    indicator_readiness: str = ""
    api_received_at: str = ""
    provider_observed_at: str = ""
    bar_end_at: str = ""
    market_session_date: str = ""
    relative_volume_certified: bool = False

    def __post_init__(self) -> None:
        self.observed_at = self.observed_at or self.timestamp
        self.ingested_at = self.ingested_at or now_iso()
        self.snapshot_id = self.snapshot_id or f"{self.ticker}:{self.observed_at}"
        self.turnover_usd = self.turnover_usd or self.current * self.volume
        self.candle_as_of = self.candle_as_of or self.observed_at
        quality_ok = self.data_quality in {"OK", "COMPLETE"}
        self.transport_status = self.transport_status or ("OK" if quality_ok else "UNKNOWN")
        self.quote_freshness = self.quote_freshness or ("FRESH" if quality_ok else "UNKNOWN")
        self.candle_freshness = self.candle_freshness or ("FRESH" if quality_ok else "UNKNOWN")
        self.bar_completeness = self.bar_completeness or ("COMPLETE" if quality_ok else "UNKNOWN")
        self.volume_validity = self.volume_validity or ("VALID" if quality_ok else "UNVERIFIED")
        self.indicator_readiness = self.indicator_readiness or ("READY" if quality_ok else "UNCERTIFIED")
        self.api_received_at = self.api_received_at or self.ingested_at
        self.provider_observed_at = self.provider_observed_at or self.observed_at
        self.bar_end_at = self.bar_end_at or self.candle_as_of
        self.market_session_date = self.market_session_date or self.bar_end_at[:10]
        self.relative_volume_certified = bool(
            self.bar_completeness == "COMPLETE" and self.volume_validity == "VALID")
        if self.indicator_readiness != "READY":
            self.stage = "UNCERTIFIED"
        if self.current > 0:
            if self.distance_ma20_pct is None and self.ma20 > 0:
                self.distance_ma20_pct = round((self.current / self.ma20 - 1) * 100, 4)
            if self.distance_ma50_pct is None and self.ma50 > 0:
                self.distance_ma50_pct = round((self.current / self.ma50 - 1) * 100, 4)
            if self.distance_ma200_pct is None and self.ma200 and self.ma200 > 0:
                self.distance_ma200_pct = round((self.current / self.ma200 - 1) * 100, 4)

    @property
    def relative_volume(self) -> float:
        return round(self.volume / max(self.avg_20d_volume, 1), 2)

    @property
    def atr_pct(self) -> float:
        return round(self.atr_14 / max(self.current, 0.01) * 100, 2)


@dataclass
class EvidenceItem:
    evidence_id: str
    ticker: str
    source_type: str
    document_type: str
    published_at: str
    title: str
    source_url: str
    evidence_grade: str
    category: str
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    source_reliability: str = "UNKNOWN"
    data_quality: str = "OK"
    ingested_at: str = field(default_factory=now_iso)
    is_mock: bool = False
    accession: str = ""
    filed_at: str = ""
    event_at: str = ""
    content_hash: str = ""
    normalized_fact: str = ""
    grade_reason: str = ""
    freshness: str = "UNKNOWN"
    extraction_method: str = ""
    query_request_id: str = ""
    lifecycle_status: str = "DISCOVERED"
    raw_document_hash: str = ""
    parsed_at: str = ""
    exhibits_resolved: bool = False
    semantic_classification: str = "UNCLASSIFIED"
    validated_at: str = ""
    ready_for_analysis_at: str = ""
    parent_evidence_id: str = ""
    source_span: str = ""


@dataclass
class CompanyState:
    ticker: str
    last_updated: str
    revenue_growth: float
    gross_margin: float
    market_cap_usd: float
    atm_active: bool
    dilution_risk: int
    catalysts: list[str]
    known_risks: list[str]
    previous_decision: str = "HOLD"
    cash_usd: float | None = None
    debt_usd: float | None = None
    shares_outstanding: float | None = None
    cash_burn_usd: float | None = None
    runway_months: float | None = None
    sector: str = "UNKNOWN"
    sic: str = ""
    companyfacts_as_of: str = ""


@dataclass
class TradePlan:
    entry_price: float
    preferred_price_min: float
    preferred_price_max: float
    stop_price: float
    target_1: float
    target_2: float
    expected_reward: float
    expected_risk: float
    reward_risk: float
    heuristic: bool = True

    @property
    def risk_per_share(self) -> float:
        return round(max(0.0, self.entry_price - self.stop_price), 4)

    @property
    def reward_1(self) -> float:
        return round(max(0.0, self.target_1 - self.entry_price), 4)

    @property
    def reward_2(self) -> float:
        return round(max(0.0, self.target_2 - self.entry_price), 4)

    @property
    def rr_1(self) -> float:
        return round(self.reward_1 / max(self.risk_per_share, 0.0001), 2)

    @property
    def rr_2(self) -> float:
        return round(self.reward_2 / max(self.risk_per_share, 0.0001), 2)


@dataclass
class PositionSize:
    quantity: int
    notional_usd: float
    loss_budget_usd: float
    portfolio_weight_pct: float
    limiting_rule: str
    account_id: str = "PAPER_DEFAULT"
    available_cash_usd: float = 0.0
    current_exposure_usd: float = 0.0
    sector_exposure_usd: float = 0.0
    initial_capital_at_risk_usd: float = 0.0
    current_mark_to_stop_risk_usd: float = 0.0
    pending_committed_risk_usd: float = 0.0
    gross_exposure_usd: float = 0.0
    risk_rule_version: str = "portfolio_heat_v1"
    portfolio_risk_used_usd: float = 0.0
    risk_budget_remaining_usd: float = 0.0


@dataclass
class RunManifest:
    run_id: str
    ticker: str
    market_snapshot_id: str
    evidence_ids: list[str]
    company_state_version: str
    research_prompt_version: str
    critic_prompt_version: str
    chairman_prompt_version: str
    risk_rule_version: str
    provider: str
    model: str
    started_at: str
    finished_at: str = ""
    final_decision: str = ""
    code_version: str = "v1.1"
    db_schema_version: int = 22
    prompt_hashes: dict[str, str] = field(default_factory=dict)
    risk_config_hash: str = ""
    analysis_intensity: str = "NORMAL"
    market_as_of: str = ""
    evidence_cutoff: str = ""
    companyfacts_as_of: str = ""
    debate_status: str = ""
    round_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_cost_usd: float = 0.0
    total_latency_ms: int = 0


@dataclass
class ResearchAnalysis:
    ticker: str
    market_regime: str
    sector: str
    signal_strength: int
    catalyst_quality: int
    expectation_gap: int
    surge_elasticity: int
    entry_readiness: int
    capital_structure_risk: int
    strategy_fit: int
    bull_case: list[str]
    bear_case: list[str]
    suggested_decision: str
    confidence: int
    evidence_ids: list[str]
    claims: list[dict[str, Any]] = field(default_factory=list)
    provider: str = "mock"
    model: str = "deterministic-v0.2"
    prompt_version: str = "mock_research_v0.2"
    current_decision: str = ""
    accepted_points: list[str] = field(default_factory=list)
    rejected_points: list[str] = field(default_factory=list)
    modified_points: list[str] = field(default_factory=list)
    unresolved_points: list[str] = field(default_factory=list)
    new_claims: list[dict[str, Any]] = field(default_factory=list)
    withdrawn_claims: list[str] = field(default_factory=list)
    evidence_requests: list[dict[str, Any]] = field(default_factory=list)
    evidence_that_would_change_my_view: list[str] = field(default_factory=list)
    issue_updates: list[dict[str, Any]] = field(default_factory=list)
    consensus_ready: bool = False
    score_details: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class CriticReview:
    ticker: str
    research_decision: str
    verdict: str
    critical_flaws: list[dict[str, str]]
    failure_scenarios: list[dict[str, Any]]
    evidence_conflicts: list[str]
    critic_decision: str
    confidence: int
    provider: str = "mock"
    model: str = "deterministic-v0.2"
    prompt_version: str = "mock_critic_v0.2"
    need_more_evidence: bool = False
    evidence_requests: list[dict[str, Any]] = field(default_factory=list)
    current_decision: str = ""
    accepted_points: list[str] = field(default_factory=list)
    rejected_points: list[str] = field(default_factory=list)
    modified_points: list[str] = field(default_factory=list)
    unresolved_points: list[str] = field(default_factory=list)
    new_claims: list[dict[str, Any]] = field(default_factory=list)
    withdrawn_claims: list[str] = field(default_factory=list)
    evidence_that_would_change_my_view: list[str] = field(default_factory=list)
    issue_updates: list[dict[str, Any]] = field(default_factory=list)
    consensus_ready: bool = False


@dataclass
class UserRequest:
    request_id: str
    discord_message_id: str
    discord_user_id: str
    received_at: str
    original_text: str
    intent: str
    tickers: list[str]
    time_horizon: str = "1-2M"
    focus: list[str] = field(default_factory=list)
    comparison_mode: str = "NONE"
    use_prior_analysis: bool = False
    need_debate: bool = True
    need_report: bool = True
    parser_type: str = "LIGHTWEIGHT"
    parser_confidence: float = 1.0
    missing_fields: list[str] = field(default_factory=list)
    status: str = RequestStatus.PARSED.value
    analysis_intensity: str = AnalysisIntensity.NORMAL.value
    min_debate_rounds: int = 3
    max_debate_rounds: int = 5
    intensity_explicit: bool = False
    reasoning_profile: str = "high"
    evidence_depth: str = "STANDARD"
    max_evidence_refreshes: int = 2
    consensus_stress_test_required: bool = False
    paper_action_enabled: bool = False


@dataclass(frozen=True)
class CertificationResult:
    run_id: str
    execution_status: str
    analysis_status: str
    certification_status: str
    side_effect_status: str
    action: str
    reason_codes: list[str]
    required_data_failures: list[str] = field(default_factory=list)
    important_data_warnings: list[str] = field(default_factory=list)
    decision_confidence: int | None = None
    trade_plan_status: str = "WITHHELD"
    position_sizing_status: str = "WITHHELD"
    evaluated_at: str = field(default_factory=now_iso)

    @property
    def certified(self) -> bool:
        return self.certification_status == CertificationStatus.CERTIFIED.value


@dataclass
class RiskResult:
    ticker: str
    hard_filter_pass: bool
    warnings: list[str]
    failures: list[str]
    trade_plan: TradePlan
    risk_decision: str
    rule_version: str = "risk_rules_v0.2"

    @property
    def reward_risk_ratio(self) -> float:
        return self.trade_plan.reward_risk


@dataclass
class InvestmentDecision:
    ticker: str
    timestamp: str
    decision: str
    confidence: int
    entry_status: str
    trade_plan: TradePlan
    top_reasons: list[str]
    top_risks: list[str]
    run_id: str

    @property
    def preferred_price_min(self) -> float:
        return self.trade_plan.preferred_price_min

    @property
    def preferred_price_max(self) -> float:
        return self.trade_plan.preferred_price_max

    @property
    def invalidation_price(self) -> float:
        return self.trade_plan.stop_price

    @property
    def target_1(self) -> float:
        return self.trade_plan.target_1

    @property
    def target_2(self) -> float:
        return self.trade_plan.target_2


@dataclass
class ChairmanDecision:
    decision: str
    confidence: int
    rationale: list[str]
    risk_acknowledgements: list[str]
    debate_resolution: list[str] = field(default_factory=list)
    invalidation_conditions: list[str] = field(default_factory=list)
    minority_opinion: list[str] = field(default_factory=list)


@dataclass
class PaperAccount:
    account_id: str
    cash: float
    equity: float
    reserved_cash: float
    current_exposure: float
    sector_exposure: dict[str, float]
    risk_budget: float
    risk_budget_used: float
    open_positions: int
    pending_conditional_orders: int


@dataclass
class EvidenceRequest:
    request_id: str
    issue_id: str
    question: str
    severity: str
    source_scope: list[str] = field(default_factory=lambda: ["SEC"])
    target_forms: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    date_from: str = ""
    date_to: str = ""
    company_fact_targets: list[str] = field(default_factory=list)
    must_answer: bool = False
    requesting_role: str = "CRITIC"
    requested_round: int = 1


def to_dict(value: Any) -> dict[str, Any]:
    return asdict(value)
