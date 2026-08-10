from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .schemas import CompanyState, EvidenceItem, MarketSnapshot, UserRequest, now_iso


@dataclass
class MarketRegimeContext:
    regime: str
    as_of: str
    benchmark_returns: dict[str, float | None]
    relative_strength: dict[str, float | None]
    sector_regime: str = "UNKNOWN"
    regime_confidence: int = 0


@dataclass
class AnalysisContext:
    ticker: str
    built_at: str
    request: dict[str, Any]
    market: dict[str, Any]
    company_state: dict[str, Any]
    market_regime: dict[str, Any]
    evidence_index: list[dict[str, Any]]
    persistent_knowledge: dict[str, Any] = field(default_factory=dict)
    fresh_delta: dict[str, Any] = field(default_factory=dict)
    prior_analysis: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_analysis_context(request: UserRequest, market: MarketSnapshot,
                           state: CompanyState, evidence: list[EvidenceItem],
                           regime: MarketRegimeContext,
                           persistent_knowledge: dict[str, Any] | None = None,
                           fresh_delta: dict[str, Any] | None = None,
                           prior_analysis: dict[str, Any] | None = None) -> AnalysisContext:
    def priority(item: EvidenceItem) -> str:
        if (item.document_type in {"10-Q", "10-K"} and
                item.lifecycle_status != "READY_FOR_ANALYSIS"):
            return "P0"
        if item.query_request_id:
            return "P1"
        if item.category == "CONFLICT":
            return "P2"
        if item.source_type == "SEC" and item.document_type in {"10-Q", "10-K", "8-K", "EX-99"}:
            return "P3"
        if item.evidence_grade in {"A", "B"}:
            return "P4"
        return "P5"

    evidence_index = [{
        "evidence_id": item.evidence_id,
        "source_type": item.source_type,
        "form": item.document_type,
        "filed_at": item.filed_at or item.published_at,
        "event_at": item.event_at,
        "grade": item.evidence_grade,
        "grade_reason": item.grade_reason,
        "normalized_fact": item.normalized_fact or item.summary[:1200],
        "reliability": item.source_reliability,
        "freshness": item.freshness,
        "query_request_id": item.query_request_id,
        "priority": priority(item),
        "lifecycle_status": item.lifecycle_status,
        "source_domain": ("SEC_FILING" if item.source_type == "SEC" else item.source_type),
        "content_hash": item.raw_document_hash or item.content_hash,
    } for item in evidence]
    return AnalysisContext(
        ticker=market.ticker, built_at=now_iso(), request=asdict(request), market=asdict(market),
        company_state=asdict(state), market_regime=asdict(regime), evidence_index=evidence_index,
        persistent_knowledge=persistent_knowledge or {}, fresh_delta=fresh_delta or {},
        prior_analysis=prior_analysis or {})


class DebateContextBuilder:
    """Build bounded per-round context; full historical rounds stay in SQLite only."""

    def __init__(self, max_evidence_items: int = 12, max_snippet_chars: int = 1200,
                 max_history_items: int = 10):
        self.max_evidence_items = max_evidence_items
        self.max_snippet_chars = max_snippet_chars
        self.max_history_items = max_history_items

    def canonical(self, context: dict[str, Any], relevant_evidence_ids: set[str] | None = None
                  ) -> dict[str, Any]:
        allowed = relevant_evidence_ids or set()
        evidence = context.get("evidence_index", [])
        def sort_key(item: dict[str, Any]):
            priority = str(item.get("priority") or "P5")
            date = str(item.get("filed_at") or "")
            descending_date = tuple(-ord(char) for char in date)
            return priority, descending_date, str(item.get("evidence_id") or "")

        ordered = sorted(evidence, key=sort_key)
        pinned = [item for item in ordered if str(item.get("priority")) in {"P0", "P1"}
                  or item.get("evidence_id") in allowed]
        pinned_ids = {item.get("evidence_id") for item in pinned}
        remaining = [item for item in ordered if item.get("evidence_id") not in pinned_ids]
        selected = pinned + remaining[:max(0, self.max_evidence_items - len(pinned))]
        compact_evidence = []
        for item in selected:
            row = dict(item)
            if str(row.get("priority")) not in {"P0", "P1"}:
                row["normalized_fact"] = str(row.get("normalized_fact", ""))[:self.max_snippet_chars]
            compact_evidence.append(row)
        return {
            "ticker": context.get("ticker"), "built_at": context.get("built_at"),
            "request": context.get("request", {}), "market": context.get("market", {}),
            "company_state": context.get("company_state", {}),
            "market_regime": context.get("market_regime", {}),
            "evidence_index": compact_evidence,
            "persistent_knowledge": context.get("persistent_knowledge", {}),
            "fresh_delta": context.get("fresh_delta", {}),
        }

    def round_payload(self, context: dict[str, Any], issue_ledger: list[dict[str, Any]],
                      current_thesis: dict[str, Any], opponent_response: dict[str, Any] | None,
                      thesis_change_history: list[dict[str, Any]], round_no: int,
                      relevant_evidence_ids: set[str] | None = None) -> dict[str, Any]:
        open_issues = [item for item in issue_ledger if item.get("status", "OPEN") != "RESOLVED"]
        history = thesis_change_history[-self.max_history_items:]
        return {
            "canonical_analysis_context": self.canonical(context, relevant_evidence_ids),
            "round_no": int(round_no), "issue_ledger": open_issues,
            "current_thesis": self._compact_agent_response(current_thesis),
            "opponent_previous_response": self._compact_agent_response(opponent_response or {}),
            "thesis_change_history": history,
        }

    @classmethod
    def _compact_agent_response(cls, value: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "current_decision", "suggested_decision", "critic_decision", "verdict", "confidence",
            "bull_case", "bear_case", "accepted_points", "rejected_points", "modified_points",
            "unresolved_points", "new_claims", "withdrawn_claims", "evidence_requests",
            "evidence_that_would_change_my_view", "issue_updates", "critical_flaws",
            "failure_scenarios", "evidence_conflicts", "evidence_ids", "consensus_ready",
        )

        def compact(item: Any, depth: int = 0) -> Any:
            if depth >= 3:
                return str(item)[:350]
            if isinstance(item, str):
                return item[:350]
            if isinstance(item, list):
                return [compact(child, depth + 1) for child in item[:5]]
            if isinstance(item, dict):
                return {str(key): compact(child, depth + 1)
                        for key, child in list(item.items())[:12]}
            return item

        return {name: compact(value[name]) for name in allowed if name in value}
