from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RunMode(str, Enum):
    HUNT_ONLY = "HUNT_ONLY"
    HUNT_AND_EXECUTION_REVIEW = "HUNT_AND_EXECUTION_REVIEW"


class DiscoveryDecision(str, Enum):
    DEEP_DIVE_NOW = "DEEP_DIVE_NOW"
    DEEP_DIVE_SECONDARY = "DEEP_DIVE_SECONDARY"
    WATCH_STAGE0 = "WATCH_STAGE0"
    WATCH_RESET = "WATCH_RESET"
    EXCLUDE = "EXCLUDE"


class ExecutionAction(str, Enum):
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"
    STARTER = "STARTER"
    ADD = "ADD"
    FULL = "FULL"
    TRIM = "TRIM"
    EXIT = "EXIT"


class GateDecision(str, Enum):
    PASS = "PASS"
    PASS_WITH_PARTIAL = "PASS_WITH_PARTIAL"
    PASS_WITH_CONSTRAINTS = "PASS_WITH_CONSTRAINTS"
    REJECT = "REJECT"
    RETRY_WITH_NEW_EVIDENCE = "RETRY_WITH_NEW_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class WorkStatus(str, Enum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    STALE_ON_ARRIVAL = "STALE_ON_ARRIVAL"
    CANCELLED = "CANCELLED"


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EvidenceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


class FreshnessDelta(str, Enum):
    NEW = "NEW"
    STRENGTHENED = "STRENGTHENED"
    UNCHANGED = "UNCHANGED"
    WEAKENED = "WEAKENED"
    STALE = "STALE"


@dataclass(frozen=True)
class EffectiveRuleSet:
    rule_set_id: str = "investment-rules-v2.0"
    version: str = "2.0"
    strategy_min_days: int = 7
    strategy_max_days: int = 56
    fresh_money_max_positive_commitments: int = 1
    default_starter_capital_pct: float = 1.0
    # Python-owned execution risk policy. Caller input may only reduce this budget.
    per_position_risk_budget_pct: float = 1.0
    max_per_position_risk_budget_pct: float = 1.0
    override_id: str | None = None
    override_content_hash: str | None = None
    authorization: str | None = None
    max_age_market_context_hours: int = 24
    # Freshness is evaluated per asset; synchronization is a separate
    # cross-asset policy so the two controls cannot silently drift together.
    max_market_context_sync_spread_hours: int = 24
    # Market Context contains assets with different observation clocks.  These
    # are Python-owned policy values (never provider supplied) and keep
    # exchange-session, official-daily, FX and 24/7 crypto observations from
    # being incorrectly compared as if they shared one clock.
    max_age_market_context_exchange_hours: int = 72
    max_age_market_context_daily_hours: int = 168
    max_age_market_context_fx_hours: int = 120
    max_age_market_context_crypto_hours: int = 36
    max_market_context_sync_exchange_hours: int = 24
    max_market_context_sync_daily_hours: int = 168
    max_market_context_sync_fx_hours: int = 120
    max_market_context_sync_crypto_hours: int = 36
    max_age_market_execution_minutes: int = 15
    max_age_portfolio_minutes: int = 15
    max_age_sec_hours: int = 24 * 31
    max_age_research_hours: int = 24 * 7
    max_age_universe_hours: int = 24 * 2
    universe_min_price: float = 3.0
    universe_min_market_cap: float = 300_000_000.0
    universe_min_average_dollar_volume: float = 10_000_000.0
    hunt_min_upside_pct: float = 0.30
    hunt_strong_upside_pct: float = 0.60
    # Provider clocks are not authoritative when they report impossible
    # future observations.  A small tolerance covers normal clock skew while
    # keeping freshness fail-closed.
    max_future_skew_seconds: int = 300

    @property
    def rule_set_hash(self) -> str:
        return canonical_hash(self.__dict__)


@dataclass
class Run:
    run_id: str
    mode: RunMode
    rule_set: EffectiveRuleSet
    context_manifest_hash: str
    evidence_epoch: int
    status: str = "RUNNING"
    outcome: str | None = None
    created_at: str = field(default_factory=utc_now)


@dataclass
class WorkItem:
    work_item_id: str
    run_id: str
    stage: str
    payload: dict[str, Any]
    status: WorkStatus = WorkStatus.QUEUED
    attempt: int = 0
    lease_token: str | None = None
    leased_by: str | None = None
    lease_until: str | None = None
    dependency_hash: str = ""
    evidence_epoch: int = 0
    rule_set_hash: str = ""
    context_manifest_hash: str = ""


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    subject_id: str
    source_class: str
    observed_at: str
    epoch: int
    payload_hash: str
    grade: str = "UNKNOWN"
    status: str = EvidenceStatus.ACTIVE.value
    # Optional explicit RawArtifact receipt.  Legacy/derived Evidence may not
    # have one, while external-source Evidence must bind one when persisted.
    raw_artifact_id: str | None = None


@dataclass(frozen=True)
class Claim:
    claim_id: str
    subject_id: str
    statement: str
    status: str = "UNKNOWN"


@dataclass(frozen=True)
class ClaimEvidenceLink:
    claim_id: str
    evidence_id: str
    support_status: str


@dataclass(frozen=True)
class ResearchResult:
    result_id: str
    security_id: str
    status: str
    claims: tuple[str, ...]
    failure_paths: tuple[FailurePath, ...] = ()
    dependency_hash: str = ""
    evidence_epoch: int = 0


@dataclass(frozen=True)
class AuditResult:
    result_id: str
    security_id: str
    recommendation: str
    issue_ids: tuple[str, ...] = ()
    dependency_hash: str = ""
    evidence_epoch: int = 0


@dataclass(frozen=True)
class DebateIssue:
    issue_id: str
    security_id: str
    severity: str
    finding: str
    status: str = "OPEN"


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    gate_type: str
    decision: GateDecision
    subject_id: str | None
    receipt: dict[str, Any]
    dependency_hash: str
    evidence_epoch: int


@dataclass(frozen=True)
class ContextManifest:
    manifest_hash: str
    included_context_ids: tuple[str, ...]
    omitted_required: tuple[str, ...] = ()


@dataclass(frozen=True)
class DependencyState:
    dependency_hash: str
    evidence_epoch: int
    rule_set_hash: str
    context_manifest_hash: str


@dataclass(frozen=True)
class PositionSnapshot:
    subject_id: str
    position_exists: bool
    shares: int
    average_cost: float
    as_of: str
    snapshot_hash: str
    currency: str = "UNKNOWN"


@dataclass(frozen=True)
class SecurityIdentity:
    """Canonical security identity; provider symbols never become authority."""

    security_id: str
    ticker: str
    issuer_name: str
    venue: str
    cik: str | None = None
    identity_hash: str = ""

    def __post_init__(self) -> None:
        if not self.security_id or not self.ticker or not self.issuer_name:
            raise ValueError("security identity requires security_id, ticker and issuer_name")
        if not self.identity_hash:
            object.__setattr__(self, "identity_hash", canonical_hash(self.__dict__))


@dataclass(frozen=True)
class MarketContextSnapshot:
    snapshot_id: str
    observed_at: str
    as_of: str
    regime: str
    breadth: str
    volatility: str
    sector_rotation: dict[str, Any]
    source_artifact_ids: tuple[str, ...]
    payload_hash: str


@dataclass(frozen=True)
class MarketExecutionSnapshot:
    snapshot_id: str
    security_id: str
    current_price: float
    execution_stop: float
    observed_at: str
    core_input_complete: bool
    gap_risk: float | None
    event_risk_pct: float | None
    account_equity: float | None
    source_artifact_ids: tuple[str, ...]
    payload_hash: str
    currency: str = "UNKNOWN"


@dataclass(frozen=True)
class PortfolioSnapshot:
    snapshot_id: str
    as_of: str
    cash: float
    total_equity: float
    positions: tuple[PositionSnapshot, ...]
    read_only: bool = True
    payload_hash: str = ""
    currency: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if not self.read_only:
            raise ValueError("PortfolioSnapshot is read-only")


@dataclass(frozen=True)
class TechnicalFeatures:
    security_id: str
    as_of: str
    features: dict[str, float | str | None]
    calculator_version: str
    source_artifact_ids: tuple[str, ...]
    payload_hash: str


@dataclass(frozen=True)
class QualifiedCandidate:
    security_id: str
    identity_hash: str
    stage_gate: str
    capital_gate: str
    deep_research_result_id: str
    full_sec_result_id: str
    audit_result_id: str
    failure_path_count: int
    evidence_ids: tuple[str, ...]
    dependency_hash: str
    evidence_epoch: int


@dataclass(frozen=True)
class RiskAssessment:
    security_id: str
    current_price: float
    execution_stop: float
    risk_per_share: float
    per_position_budget: float
    portfolio_budget: float
    maximum_allowed_position: int
    actual_position_size: int
    gap_risk: float
    event_risk_pct: float
    calculation_hash: str


@dataclass(frozen=True)
class EconomicAssessment:
    """Evidence-linked economic inputs consumed by the Python RiskEngine.

    LLMs may propose the underlying scenarios, but the receipt is created by
    deterministic Python code and must reference the evidence/result lineage
    used for the arithmetic.
    """

    security_id: str
    current_price: float
    bull_value: float
    base_value: float
    bear_value: float
    bull_probability: float
    base_probability: float
    bear_probability: float
    probability_weighted_ev: float
    structural_asymmetry: float
    opportunity_cost_score: float
    evidence_ids: tuple[str, ...]
    source_result_ids: tuple[str, ...]
    calculation_hash: str


@dataclass(frozen=True)
class RawArtifact:
    artifact_id: str
    provider: str
    artifact_type: str
    subject_id: str | None
    observed_at: str
    payload: dict[str, Any]
    payload_hash: str
    source_observed_at: str | None = None
    retrieved_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class EvidenceRequest:
    request_key: str
    subject_id: str
    evidence_type: str
    query: str
    priority: str = "MEDIUM"


@dataclass(frozen=True)
class FailurePath:
    category: str
    scenario: str
    causal_path: str
    probability_direction: str
    impact: dict[str, Any]
    observable_trigger: str
    source_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ActionRecommendation:
    security_id: str
    action: ExecutionAction
    status: str = "READY"
    action_scope: str = "CANDIDATE"
    starter_plan: dict[str, Any] | None = None
    add_plan: dict[str, Any] | None = None
    position_snapshot_receipt: dict[str, Any] | None = None
    prior_add_trigger_receipt: dict[str, Any] | None = None
    fresh_evidence_delta_receipt: dict[str, Any] | None = None
    strengthening_evidence_receipt: dict[str, Any] | None = None
    unresolved_critical: bool = False
    dependency_hash: str = ""


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    mode: RunMode
    outcome: str
    qualified_candidates: tuple[str, ...] = ()
    recommendation: ActionRecommendation | None = None
    authoritative_action: ExecutionAction | None = None
    allocation: dict[str, Any] | None = None
    blocked_reason: str | None = None
