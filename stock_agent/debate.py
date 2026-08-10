from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable

from .analysis_context import DebateContextBuilder
from .schemas import DebateStatus, UserRequest
from .validation import AnalysisIncompleteError


class BudgetLimitError(AnalysisIncompleteError):
    pass


@dataclass
class DebateIssue:
    issue_id: str
    topic: str
    severity: str
    research_position: str = ""
    critic_position: str = ""
    supporting_evidence_ids: list[str] = field(default_factory=list)
    opposing_evidence_ids: list[str] = field(default_factory=list)
    status: str = "OPEN"
    resolution_basis: str = ""
    opened_round: int = 1
    last_updated_round: int = 1
    issue_instance_id: str = ""
    semantic_issue_key: str = ""
    parent_issue_id: str = ""
    materiality: str = "MATERIAL"


@dataclass
class DebateState:
    run_id: str
    round_no: int
    min_rounds: int
    max_rounds: int
    status: str
    analysis_intensity: str = "NORMAL"
    research_stance: str = ""
    critic_stance: str = ""
    research_confidence: int = 0
    critic_confidence: int = 0
    issue_ledger: list[DebateIssue] = field(default_factory=list)
    confidence_history: list[dict[str, Any]] = field(default_factory=list)
    thesis_change_log: list[dict[str, Any]] = field(default_factory=list)
    evidence_request_history: list[dict[str, Any]] = field(default_factory=list)
    resolved_issue_count: int = 0
    open_issue_count: int = 0
    critical_open_issue_count: int = 0
    provisional_consensus: bool = False
    stress_test_completed: bool = False
    final_consensus: bool = False
    deadlock_reason: str = ""
    evidence_generation: int = 0
    material_evidence_review_required: bool = False
    unresolved_must_answer_count: int = 0
    no_progress_streak: int = 0
    round_information_gain: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConsensusEvaluator:
    @staticmethod
    def evaluate(state: DebateState, research: Any, critic: Any) -> dict[str, Any]:
        research_decision = str(getattr(research, "current_decision", "") or
                                getattr(research, "suggested_decision", ""))
        critic_decision = str(getattr(critic, "current_decision", "") or
                              getattr(critic, "critic_decision", ""))
        open_critical = [issue for issue in state.issue_ledger
                         if issue.status == "OPEN" and issue.severity == "CRITICAL"]
        open_high_conflict = [issue for issue in state.issue_ledger
                              if issue.status == "OPEN" and issue.severity == "HIGH"
                              and issue.research_position != issue.critic_position]
        explicit_ready = bool(getattr(research, "consensus_ready", False)
                              and getattr(critic, "consensus_ready", False))
        structured = bool(getattr(research, "issue_updates", []) or getattr(critic, "issue_updates", [])
                          or getattr(research, "accepted_points", []) or getattr(critic, "accepted_points", []))
        legacy_ready = not structured and research_decision == critic_decision
        same_decision = bool(research_decision and research_decision == critic_decision)
        uncertainty_consensus = (same_decision and research_decision in {"WAIT", "EXCLUDE"}
                                 and not open_critical)
        ready = (explicit_ready or legacy_ready or uncertainty_consensus)
        consensus = bool(same_decision and ready and not open_critical and not open_high_conflict)
        reasons = []
        if not same_decision:
            reasons.append("FINAL_ACTION_MISMATCH")
        if open_critical:
            reasons.append("CRITICAL_ISSUE_OPEN")
        if open_high_conflict:
            reasons.append("ACTION_CHANGING_HIGH_ISSUE_OPEN")
        if not ready:
            reasons.append("AGENTS_NOT_READY")
        if state.unresolved_must_answer_count:
            reasons.append("MUST_ANSWER_UNRESOLVED")
        if state.material_evidence_review_required:
            reasons.append("MATERIAL_EVIDENCE_REVIEW_REQUIRED")
        consensus = bool(consensus and not state.unresolved_must_answer_count
                         and not state.material_evidence_review_required)
        return {"consensus": consensus, "research_decision": research_decision,
                "critic_decision": critic_decision, "reasons": reasons,
                "critical_open": len(open_critical), "high_conflict_open": len(open_high_conflict)}


class DebateEngine:
    def __init__(self, context_builder: DebateContextBuilder | None = None):
        self.context_builder = context_builder or DebateContextBuilder()

    def run(self, run_id: str, request: UserRequest, analysis_context: dict[str, Any],
            research_call: Callable[[int, dict[str, Any], str], Any],
            critic_call: Callable[[int, Any, dict[str, Any], str], Any],
            refresh_call: Callable[[list[dict[str, Any]], int], dict[str, Any] | None],
            persist_call: Callable[[DebateState, Any, Any, dict[str, Any], str], None] | None = None,
            progress_call: Callable[[str, dict[str, Any]], None] | None = None,
            cost_check: Callable[[int], str] | None = None,
            must_answer_check: Callable[[], int] | None = None
            ) -> tuple[Any, Any, DebateState, dict[str, Any]]:
        state = DebateState(run_id, 0, request.min_debate_rounds, request.max_debate_rounds,
                            DebateStatus.IN_PROGRESS.value, request.analysis_intensity)
        previous_research = None
        previous_critic = None
        refreshes = 0
        final_consensus: dict[str, Any] = {"consensus": False, "reasons": ["NOT_EVALUATED"]}
        stress_phase = False

        for round_no in range(1, request.max_debate_rounds + 1):
            state.round_no = round_no
            state.status = DebateStatus.ROUND_ACTIVE.value
            prior_research_stance = state.research_stance
            prior_critic_stance = state.critic_stance
            prior_resolved = state.resolved_issue_count
            phase = "CONSENSUS_STRESS_TEST" if stress_phase else "DEBATE"
            research_payload = self.context_builder.round_payload(
                analysis_context, [asdict(issue) for issue in state.issue_ledger],
                self._object(previous_research), self._object(previous_critic),
                state.thesis_change_log, round_no)
            if stress_phase:
                research_payload["stress_instruction"] = (
                    "Re-evaluate whether the strongest new falsification destroys the provisional consensus.")
            research = research_call(round_no, research_payload, phase)

            critic_payload = self.context_builder.round_payload(
                analysis_context, [asdict(issue) for issue in state.issue_ledger],
                {}, self._object(research), state.thesis_change_log, round_no)
            if stress_phase:
                critic_payload["stress_instruction"] = (
                    "Assume the provisional consensus is wrong and produce the strongest evidence-based falsification.")
            critic = critic_call(round_no, research, critic_payload, phase)

            self._update_state(state, research, critic, round_no)
            state.unresolved_must_answer_count = int(must_answer_check() if must_answer_check else 0)
            final_consensus = ConsensusEvaluator.evaluate(state, research, critic)
            if persist_call:
                persist_call(state, research, critic, final_consensus, phase)
            if progress_call:
                progress_call("DEBATE_ROUND_COMPLETED", {
                    "round": round_no, "max_rounds": request.max_debate_rounds,
                    "phase": phase, "research_decision": state.research_stance,
                    "critic_decision": state.critic_stance,
                    "research_confidence": state.research_confidence,
                    "critic_confidence": state.critic_confidence,
                    "resolved_issues": state.resolved_issue_count,
                    "open_issues": state.open_issue_count,
                    "critical_open_issues": state.critical_open_issue_count,
                })
            previous_research, previous_critic = research, critic

            requests = list(getattr(critic, "evidence_requests", []) or [])
            new_material_evidence = 0
            if requests and refreshes < request.max_evidence_refreshes:
                state.evidence_request_history.extend(requests)
                updated = refresh_call(requests, round_no)
                if updated:
                    analysis_context = updated
                    state.evidence_generation += 1
                    state.material_evidence_review_required = True
                    state.provisional_consensus = False
                    state.final_consensus = False
                    state.status = DebateStatus.EVIDENCE_REVIEW_REQUIRED.value
                    final_consensus = {"consensus": False,
                                       "reasons": ["NEW_MATERIAL_EVIDENCE_REVIEW_REQUIRED"]}
                    new_material_evidence = 1
                refreshes += 1

            thesis_change = int(bool(prior_research_stance and
                (prior_research_stance != state.research_stance or
                 prior_critic_stance != state.critic_stance)))
            issue_closed = max(0, state.resolved_issue_count - prior_resolved)
            information_gain = {
                "round": round_no, "new_material_evidence": new_material_evidence,
                "material_thesis_change": thesis_change,
                "material_issue_closed": issue_closed,
            }
            state.round_information_gain.append(information_gain)
            if not any((new_material_evidence, thesis_change, issue_closed)):
                state.no_progress_streak += 1
            else:
                state.no_progress_streak = 0

            if cost_check:
                action = cost_check(round_no)
                if action == "STOP_INCOMPLETE":
                    raise BudgetLimitError("BUDGET_LIMIT_REACHED before minimum debate rounds")
                if action == "STOP_COMPLETE" and round_no >= request.min_debate_rounds:
                    state.deadlock_reason = "BUDGET_LIMIT_REACHED"
                    break

            if round_no >= request.min_debate_rounds and final_consensus["consensus"]:
                if state.material_evidence_review_required:
                    state.material_evidence_review_required = False
                if stress_phase:
                    state.stress_test_completed = True
                    state.status = DebateStatus.FINAL_CONSENSUS.value
                    state.final_consensus = True
                    break
                if request.consensus_stress_test_required and not state.stress_test_completed:
                    state.status = DebateStatus.PROVISIONAL_CONSENSUS.value
                    state.provisional_consensus = True
                    stress_phase = True
                    continue
                state.status = DebateStatus.FINAL_CONSENSUS.value
                state.final_consensus = True
                break

            if round_no >= request.min_debate_rounds and state.no_progress_streak >= 2:
                state.deadlock_reason = "NO_MATERIAL_PROGRESS"
                break

            if stress_phase:
                state.stress_test_completed = True
                state.provisional_consensus = False
                state.status = DebateStatus.IN_PROGRESS.value
                stress_phase = False
        else:
            state.deadlock_reason = "MAX_ROUNDS_REACHED_WITH_UNRESOLVED_DISAGREEMENT"

        if not state.final_consensus:
            state.status = DebateStatus.DEADLOCK.value
            state.deadlock_reason = state.deadlock_reason or "UNRESOLVED_MATERIAL_ISSUES"
        return previous_research, previous_critic, state, final_consensus

    @staticmethod
    def _object(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        return asdict(value) if is_dataclass(value) else dict(getattr(value, "__dict__", value))

    def _update_state(self, state: DebateState, research: Any, critic: Any, round_no: int) -> None:
        previous_research = state.research_stance
        previous_critic = state.critic_stance
        previous_research_conf = state.research_confidence
        previous_critic_conf = state.critic_confidence
        state.research_stance = str(getattr(research, "current_decision", "") or
                                    getattr(research, "suggested_decision", ""))
        state.critic_stance = str(getattr(critic, "current_decision", "") or
                                  getattr(critic, "critic_decision", ""))
        state.research_confidence = int(getattr(research, "confidence", 0))
        state.critic_confidence = int(getattr(critic, "confidence", 0))
        state.confidence_history.append({"round": round_no,
            "research": state.research_confidence, "critic": state.critic_confidence})
        for role, old_decision, new_decision, old_conf, new_conf in (
            ("RESEARCH", previous_research, state.research_stance,
             previous_research_conf, state.research_confidence),
            ("CRITIC", previous_critic, state.critic_stance,
             previous_critic_conf, state.critic_confidence)):
            if old_decision and (old_decision != new_decision or old_conf != new_conf):
                state.thesis_change_log.append({"round": round_no, "role": role,
                    "from_decision": old_decision, "to_decision": new_decision,
                    "from_confidence": old_conf, "to_confidence": new_conf,
                    "reason": "Evidence/logic update reported by agent"})
        updates = list(getattr(research, "issue_updates", []) or []) + list(
            getattr(critic, "issue_updates", []) or [])
        explicit_topics = {str(item.get("topic") or item.get("issue") or "").strip().lower()
                           for item in updates if isinstance(item, dict)}
        for flaw in getattr(critic, "critical_flaws", []) or []:
            if isinstance(flaw, dict):
                topic = str(flaw.get("issue", "Unspecified critic flaw"))
                if topic.strip().lower() in explicit_topics:
                    continue
                updates.append({"topic": topic,
                                "severity": flaw.get("severity", "HIGH"), "status": "OPEN",
                                "critic_position": flaw.get("issue", "")})
        self._merge_issues(state, updates, round_no)
        state.resolved_issue_count = sum(issue.status == "RESOLVED" for issue in state.issue_ledger)
        state.open_issue_count = sum(issue.status == "OPEN" for issue in state.issue_ledger)
        state.critical_open_issue_count = sum(
            issue.status == "OPEN" and issue.severity == "CRITICAL" for issue in state.issue_ledger)

    @staticmethod
    def _merge_issues(state: DebateState, updates: list[dict[str, Any]], round_no: int) -> None:
        by_id = {issue.issue_id: issue for issue in state.issue_ledger}
        by_semantic = {issue.semantic_issue_key: issue for issue in state.issue_ledger}
        for raw in updates:
            if not isinstance(raw, dict):
                continue
            topic = str(raw.get("topic") or raw.get("issue") or "").strip()
            if not topic:
                continue
            supplied_id = str(raw.get("issue_id") or "")
            semantic_key = DebateEngine.semantic_issue_key(topic)
            issue = by_id.get(supplied_id) or by_semantic.get(semantic_key)
            if issue is None:
                instance_id = f"ISSUE_INSTANCE_{uuid.uuid4()}"
                issue = DebateIssue(instance_id, topic, str(raw.get("severity") or "MEDIUM"),
                                    opened_round=round_no, last_updated_round=round_no,
                                    issue_instance_id=instance_id,
                                    semantic_issue_key=semantic_key,
                                    parent_issue_id=str(raw.get("parent_issue_id") or supplied_id),
                                    materiality=str(raw.get("materiality") or "MATERIAL"))
                state.issue_ledger.append(issue)
                by_id[instance_id] = issue
                if supplied_id:
                    by_id[supplied_id] = issue
                by_semantic[semantic_key] = issue
            issue.severity = str(raw.get("severity") or issue.severity).upper()
            issue.research_position = str(raw.get("research_position") or issue.research_position)
            issue.critic_position = str(raw.get("critic_position") or issue.critic_position)
            issue.supporting_evidence_ids = list(raw.get("supporting_evidence_ids") or
                                                 issue.supporting_evidence_ids)
            issue.opposing_evidence_ids = list(raw.get("opposing_evidence_ids") or
                                               issue.opposing_evidence_ids)
            issue.status = str(raw.get("status") or issue.status).upper()
            issue.resolution_basis = str(raw.get("resolution_basis") or issue.resolution_basis)
            issue.last_updated_round = round_no

    @staticmethod
    def semantic_issue_key(topic: str) -> str:
        lowered = topic.lower()
        categories = (
            ("CAPITAL_STRUCTURE_ATM", ("atm", "at-the-market", "equity distribution")),
            ("Q2_FINANCIALS", ("q2", "10-q", "10q", "gross margin", "cash burn")),
            ("EARNINGS_EXHIBIT", ("exhibit 99", "item 2.02", "earnings release")),
            ("MARKET_DATA_QUALITY", ("stale market", "relative volume", "session volume", "stage")),
            ("STATE_INTEGRITY_ATM", ("atm_active", "persisted state", "state conflict")),
        )
        for key, terms in categories:
            if any(term in lowered for term in terms):
                return key
        normalized = re.sub(r"[^a-z0-9가-힣]+", " ", lowered)
        tokens = [token for token in normalized.split()
                  if token not in {"the", "a", "an", "is", "are", "has", "have", "unknown"}]
        return "ISSUE_" + hashlib.sha256(" ".join(sorted(set(tokens))).encode()).hexdigest()[:16]
