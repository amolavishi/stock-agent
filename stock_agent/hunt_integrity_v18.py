"""V8 HUNT V1.8 adversarial integrity guardrails.

This layer does not relax investment thresholds.  It hardens the current
V1.3->V1.7 composition while the repository migrates toward explicit runtime
composition:

* candidate-scoped model failures are terminalized as engineering failures
  instead of aborting the remaining universe;
* a failed Full-SEC fetch/validation is candidate-scoped and cannot become a
  successful SEC result;
* V8 Step-18 A/A- certification is mandatory before a strict candidate can be
  qualified or receive a positive allocation;
* every discovery candidate receives a deterministic conservation state;
* any incomplete candidate evaluation makes the run non-evaluable rather than
  a clean NO_TRADE;
* model-wire source selection is capability-aware, adverse-evidence aware, and
  position-stratified instead of blindly taking the first N sources;
* retrieved canonical source bodies are no longer truncated by the V1.6 packet
  deduper before SQLite persistence;
* Shadow output is rendered from a structured conclusion and carries a strategy
  version vector/cohort hash.

No grade is created here.  The repository still needs the canonical V8 Step-18
writer before A/A- execution can become evaluable.
"""
from __future__ import annotations

import json
import math
import re
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from . import gates as gates_module
from . import hunt_pipeline_v16 as v16
from . import hunt_resilience_v17 as v17
from . import runtime as runtime_module
from . import shadow as shadow_module
from . import store as store_module
from .models import GateDecision, RawArtifact, RunMode, RunOutcome, canonical_hash, utc_now


HUNT_INTEGRITY_VERSION = "V8_HUNT_INTEGRITY_V1.8"
SHADOW_INTEGRITY_VERSION = "SHADOW_V1.3"
STEP18_SOURCE_SHA256 = "26fddaa0b0ddec166427d89a50ad0f272d06ee6d43a6b91995f45fefaa039528"

_ACTIVE_AGENT: ContextVar[Any | None] = ContextVar("stock_agent_v18_active_agent", default=None)
_SELECTOR_STAGE: ContextVar[str] = ContextVar("stock_agent_v18_selector_stage", default="GENERIC")
_BASE_TARGET: ContextVar[float | None] = ContextVar("stock_agent_v18_base_target", default=None)

_CANDIDATE_MODEL_STAGES = {
    "CAPITAL_PRESCREEN",
    "CAP_FUNDAMENTAL_CHANGE",
    "CAP_CATALYST_EXPECTATION_RESEARCH",
    "CAP_DIRECTIONAL_PROBABILITY",
    "DEEP_RESEARCH",
    "FULL_SEC_FORENSIC",
    "STANDARD_AUDIT",
    "ADVERSARIAL_AUDIT",
}

_SOURCE_LIST_KEYS = {"evidence_items", "sources", "source_documents", "sec_artifacts", "articles"}
_AUTHORITY_SCORES = {
    "SEC": 80,
    "SEC_EDGAR": 80,
    "GOVERNMENT": 75,
    "REGULATOR": 75,
    "COMPANY_IR": 65,
    "CUSTOMER": 60,
    "PARTNER": 55,
    "INDUSTRY": 50,
    "MEDIA": 30,
}
_COMMON_ADVERSE_TERMS = (
    "going concern", "material weakness", "restatement", "default", "covenant",
    "convertible", "warrant", "dilution", "offering", "at-the-market", " atm ",
    "shelf", "termination", "cancel", "concentration", "cash burn", "liquidity",
    "bankruptcy", "insider sale", "form 144", "impairment", "investigation",
)
_STAGE_TERMS = {
    "CAP_FUNDAMENTAL_CHANGE": ("revenue", "margin", "ebitda", "cash flow", "guidance", "bookings", "backlog", "customer"),
    "CAP_CATALYST_EXPECTATION_RESEARCH": ("contract", "award", "guidance", "approval", "earnings", "customer", "refinancing", "capacity"),
    "CAP_DIRECTIONAL_PROBABILITY": ("bear", "base", "bull", "probability", "downside", "upside", "failure"),
    "DEEP_RESEARCH": ("revenue", "margin", "guidance", "contract", "customer", "cash flow", "backlog", "risk"),
    "FULL_SEC_FORENSIC": ("offering", "atm", "shelf", "warrant", "convertible", "debt", "covenant", "form 144", "stock compensation"),
    "STANDARD_AUDIT": _COMMON_ADVERSE_TERMS,
    "ADVERSARIAL_AUDIT": _COMMON_ADVERSE_TERMS,
    "GENERIC": ("contract", "guidance", "revenue", "margin", "customer", "debt", "offering"),
}


def _stage_from_context(context: dict[str, Any]) -> str:
    for entry in context.get("entries") or []:
        if not isinstance(entry, dict) or str(entry.get("id")) != "stage":
            continue
        content = entry.get("content")
        if isinstance(content, dict) and "value" in content:
            content = content["value"]
        if content not in (None, ""):
            return str(content)
    return "GENERIC"


def _v18_excerpt(text: str, limit: int = v17.SOURCE_EXCERPT_CHARS) -> str:
    """Keyword windows plus deterministic structural sampling.

    Structural sampling is deliberate: a decisive disclosure with no expected
    keyword must still have a chance to enter the working context.
    """
    value = str(text or "")
    if len(value) <= limit:
        return value
    lowered = value.casefold()
    stage = _SELECTOR_STAGE.get()
    terms = tuple(dict.fromkeys((*_COMMON_ADVERSE_TERMS, *_STAGE_TERMS.get(stage, _STAGE_TERMS["GENERIC"]))))
    windows: list[tuple[int, int]] = []
    half = 520
    for term in terms:
        start = 0
        while len(windows) < 8:
            index = lowered.find(term, start)
            if index < 0:
                break
            windows.append((max(0, index - half), min(len(value), index + half)))
            start = index + max(1, len(term))
        if len(windows) >= 8:
            break
    # First/quarter/middle/three-quarter/last samples protect keywordless facts.
    anchor_width = max(360, min(900, limit // 7))
    for ratio in (0.0, 0.25, 0.50, 0.75, 1.0):
        center = int((len(value) - 1) * ratio)
        if ratio == 0.0:
            start = 0
        elif ratio == 1.0:
            start = max(0, len(value) - anchor_width)
        else:
            start = max(0, center - anchor_width // 2)
        windows.append((start, min(len(value), start + anchor_width)))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1] + 80:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    parts: list[str] = []
    used = 0
    for start, end in merged:
        remaining = limit - used
        if remaining <= 0:
            break
        chunk = value[start:end].strip()[:remaining]
        if chunk:
            parts.append(chunk)
            used += len(chunk)
    return "\n...[SOURCE_EXCERPT_BREAK]...\n".join(parts)[:limit]


def _source_score(source: dict[str, Any]) -> int:
    source_class = str(source.get("source_class") or source.get("provider") or "").upper()
    score = max((_AUTHORITY_SCORES.get(key, 0) for key in _AUTHORITY_SCORES if key in source_class), default=10)
    text = " ".join(str(source.get(key) or "") for key in ("title", "content", "document", "text", "body")).casefold()
    stage = _SELECTOR_STAGE.get()
    for term in _COMMON_ADVERSE_TERMS:
        if term.strip() and term in text:
            score += 18
    for term in _STAGE_TERMS.get(stage, _STAGE_TERMS["GENERIC"]):
        if term in text:
            score += 8
    if source.get("catalysts"):
        score += 20
    return score


def _select_source_indices(values: list[Any], limit: int) -> list[int]:
    count = len(values)
    if count <= limit:
        return list(range(count))
    selected: set[int] = {0, count - 1}
    ranked = sorted(
        range(count),
        key=lambda index: (-_source_score(values[index]) if isinstance(values[index], dict) else 0, index),
    )
    for index in ranked:
        if len(selected) >= max(2, limit // 2):
            break
        selected.add(index)
    # Fill the remaining budget with an even positional sample.  This prevents
    # late sources from disappearing solely because they were source #25/#30.
    slots = max(1, limit - len(selected))
    for slot in range(slots * 3 + 2):
        if len(selected) >= limit:
            break
        ratio = slot / max(1, slots * 3 + 1)
        selected.add(min(count - 1, int(round(ratio * (count - 1)))))
    if len(selected) < limit:
        for index in range(count):
            selected.add(index)
            if len(selected) >= limit:
                break
    return sorted(selected)[:limit]


def _v18_project_source(source: dict[str, Any], *, aggressive: bool = False) -> dict[str, Any]:
    keep = (
        "security_id", "source_class", "source_url", "source_observed_at", "title",
        "provider", "form", "content_depth", "artifact_id", "origin_artifact_id",
        "evidence_id", "content_hash", "lane_score", "article_fetch_error",
    )
    result = {key: deepcopy(source[key]) for key in keep if key in source}
    content = source.get("content") or source.get("document") or source.get("text") or source.get("body")
    if content not in (None, "", [], {}):
        raw = str(content)
        result["content"] = _v18_excerpt(raw, 3_000 if aggressive else v17.SOURCE_EXCERPT_CHARS)
        result["full_content_char_count"] = len(raw)
        result["full_content_hash"] = canonical_hash(raw)
        result["wire_projection_only"] = len(raw) > len(result["content"])
    if isinstance(source.get("catalysts"), list):
        result["catalysts"] = [_v18_project_value(item, key="catalysts", aggressive=aggressive) for item in source["catalysts"][:40]]
    return result


def _v18_project_value(value: Any, *, key: str = "", aggressive: bool = False, depth: int = 0) -> Any:
    if depth > 10:
        return {"wire_projection": "DEPTH_LIMIT", "full_value_hash": canonical_hash(value)}
    if isinstance(value, str):
        limit = 3_000 if aggressive else v17.GENERIC_STRING_LIMIT
        if key.casefold() in {"content", "document", "text", "body", "filing_text", "article_text"}:
            limit = 3_000 if aggressive else v17.SOURCE_EXCERPT_CHARS
            return _v18_excerpt(value, limit)
        return value if len(value) <= limit else value[:limit] + f"...[TRUNCATED:{len(value)} chars;hash={canonical_hash(value)}]"
    if isinstance(value, list):
        lowered = key.casefold()
        if lowered in _SOURCE_LIST_KEYS:
            limit = 12 if aggressive else v17.SOURCE_ITEM_LIMIT
            selected_indices = _select_source_indices(value, limit)
            selected = set(selected_indices)
            projected = [
                _v18_project_source(value[index], aggressive=aggressive)
                if isinstance(value[index], dict)
                else _v18_project_value(value[index], key=key, aggressive=aggressive, depth=depth + 1)
                for index in selected_indices
            ]
            if len(value) > limit:
                omitted = []
                for index, item in enumerate(value):
                    if index in selected or not isinstance(item, dict):
                        continue
                    body = item.get("content") or item.get("document") or item.get("text") or item.get("body") or ""
                    omitted.append({
                        "source_index": index,
                        "source_class": item.get("source_class"),
                        "source_url": item.get("source_url"),
                        "source_observed_at": item.get("source_observed_at"),
                        "title": item.get("title"),
                        "full_content_char_count": len(str(body)),
                        "full_content_hash": canonical_hash(str(body)),
                    })
                projected.append({
                    "wire_projection": "LIST_BOUNDED_WITH_OMITTED_MANIFEST",
                    "full_count": len(value),
                    "selected_indices": selected_indices,
                    "omitted_sources": omitted,
                    "full_value_hash": canonical_hash(value),
                })
            return projected
        limit = 120 if aggressive else v17.GENERIC_LIST_LIMIT
        projected = [_v18_project_value(item, key=key, aggressive=aggressive, depth=depth + 1) for item in value[:limit]]
        if len(value) > limit:
            projected.append({"wire_projection": "LIST_TRUNCATED", "full_count": len(value), "full_value_hash": canonical_hash(value)})
        return projected
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child in value.items():
            lowered = str(child_key).casefold()
            if lowered in {"candles", "prices", "volumes", "intraday_bars"} and isinstance(child, list) and len(child) > 40:
                result[child_key] = {"wire_projection": "TIME_SERIES_OMITTED", "full_count": len(child), "full_value_hash": canonical_hash(child)}
            else:
                result[child_key] = _v18_project_value(child, key=str(child_key), aggressive=aggressive, depth=depth + 1)
        return result
    return deepcopy(value)


def _v18_provider_messages(prompt_body: str, schema: dict[str, Any], context: dict[str, Any], repair: dict[str, Any] | None = None) -> list[dict[str, str]]:
    token = _SELECTOR_STAGE.set(_stage_from_context(context))
    try:
        return v17._v17_provider_messages(prompt_body, schema, context, repair)
    finally:
        _SELECTOR_STAGE.reset(token)


def _v18_dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate retrieved sources without deleting/truncating canonical text."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in sources:
        if not isinstance(raw, dict):
            continue
        source = dict(raw)
        content = str(source.get("content") or "")
        source["content"] = content
        key = canonical_hash({
            "url": source.get("source_url"),
            "time": source.get("source_observed_at"),
            "title": source.get("title"),
            "content": content,
        })
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
    return result


def _certification_payload(store: Any, run_id: str, subject_id: str) -> dict[str, Any] | None:
    row = store.get_stage_result(run_id, "V8_CERTIFICATION", subject_id)
    if not row or row.get("status") != "SUCCEEDED":
        return None
    try:
        value = json.loads(row.get("result_json") or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _certification_grade(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    if str(payload.get("source_sha256") or "") != STEP18_SOURCE_SHA256:
        return None
    if payload.get("grade_authority") not in {True, "V8_STEP18_CANONICAL"}:
        return None
    if payload.get("discovery_score_used") not in {False, "NO", "FALSE"}:
        return None
    grade = str(payload.get("research_grade") or payload.get("grade") or "").upper()
    return grade if grade in {"A", "A-", "B+", "B"} else None


def _safe_error(exc: Exception) -> str:
    return re.sub(r"\s+", " ", str(exc)).strip()[:360]


def _candidate_sentinel(stage: str, evidence_ids: list[str]) -> dict[str, Any]:
    if stage == "CAPITAL_PRESCREEN":
        result: dict[str, Any] = {
            "extraction_status": "INCOMPLETE",
            "identity_status": "UNKNOWN",
            "evidence_ids": list(evidence_ids),
            "unknowns": ["ENGINEERING_FAILURE"],
        }
        for field in gates_module.CapitalPrescreenGate.CANONICAL_FIELDS:
            result[field] = {"state": "UNKNOWN", "details": {"summary": "engineering failure", "evidence_ids": list(evidence_ids), "unknowns": ["ENGINEERING_FAILURE"]}, "evidence_ids": list(evidence_ids)}
        return result
    if stage == "DEEP_RESEARCH":
        return {"research_status": "INCOMPLETE", "failure_paths": [], "evidence_ids": list(evidence_ids), "engineering_failure": True}
    if stage == "FULL_SEC_FORENSIC":
        return {"status": "INCOMPLETE", "engineering_failure": True}
    if stage in {"STANDARD_AUDIT", "ADVERSARIAL_AUDIT"}:
        return {"status": "INCOMPLETE", "audit_recommendation": "AUDIT_EVIDENCE_INCOMPLETE", "engineering_failure": True, "unresolved_critical": ["ENGINEERING_FAILURE"]}
    return {"status": "INCOMPLETE", "engineering_failure": True}


class _V18SECProviderProxy:
    """Convert a Full-SEC transport exception into an explicit invalid artifact.

    The artifact is an engineering receipt, not issuer evidence.  The original
    SEC validator is still run and the V1.8 validation wrapper marks the
    candidate failed; no failed WorkItem is changed to SUCCEEDED.
    """

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.provider_name = getattr(delegate, "provider_name", delegate.__class__.__name__)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def fetch_filings(self, identity: dict[str, Any]) -> RawArtifact:
        try:
            return self.delegate.fetch_filings(identity)
        except Exception as exc:
            sid = str(identity.get("security_id") or "UNKNOWN")
            observed = utc_now()
            payload = {
                "__candidate_engineering_failure__": True,
                "stage": "FULL_SEC_FORENSIC",
                "error_type": type(exc).__name__,
                "error": _safe_error(exc),
            }
            return RawArtifact(
                f"artifact-sec-engineering-failure-{canonical_hash([sid, payload, observed])}",
                "engineering-failure",
                "SEC_PROVIDER_FAILURE_ARTIFACT",
                sid,
                observed,
                payload,
                canonical_hash(payload),
                observed,
                observed,
            )


def _version_vector() -> dict[str, str]:
    try:
        from . import alpha_bootstrap, alpha_coverage_v14, catalyst_acquisition_v15, v8_primary
        from . import pre_a_sidecar
        return {
            "investment_policy_version": str(getattr(v8_primary, "V8_PRIMARY_POLICY_VERSION", "V8_PRIMARY")),
            "discovery_runtime_version": "+".join(filter(None, [str(getattr(alpha_bootstrap, "ALPHA_DISCOVERY_VERSION", "V1.3")), str(getattr(alpha_coverage_v14, "ALPHA_COVERAGE_VERSION", "V1.4"))])),
            "catalyst_acquisition_version": str(getattr(catalyst_acquisition_v15, "CATALYST_ACQUISITION_VERSION", "V1.5")),
            "research_pipeline_version": str(getattr(v16, "HUNT_PIPELINE_VERSION", "V1.6")),
            "evidence_projection_version": HUNT_INTEGRITY_VERSION,
            "pre_a_version": str(getattr(pre_a_sidecar, "PRE_A_VERSION", getattr(pre_a_sidecar, "SIDECAR_VERSION", "UNKNOWN"))),
            "shadow_schema_version": SHADOW_INTEGRITY_VERSION,
        }
    except Exception:
        return {
            "investment_policy_version": "UNKNOWN",
            "discovery_runtime_version": "UNKNOWN",
            "catalyst_acquisition_version": "UNKNOWN",
            "research_pipeline_version": "UNKNOWN",
            "evidence_projection_version": HUNT_INTEGRITY_VERSION,
            "pre_a_version": "UNKNOWN",
            "shadow_schema_version": SHADOW_INTEGRITY_VERSION,
        }


def install_hunt_integrity_v18() -> None:
    if getattr(runtime_module, "_hunt_integrity_v18_installed", False):
        return

    base_production = runtime_module.ProductionStockAgent
    base_shadow = shadow_module.DailyShadowRunner
    base_reproducibility = shadow_module.reproducibility_metadata
    base_qualified = store_module.SQLiteStore.qualified_candidate_status
    base_commit = store_module.SQLiteStore.commit_final_allocation
    base_validate_sec = runtime_module.validate_sec_artifacts

    # V1.6 may project later, but canonical retrieved source bodies are no longer
    # cut here.  Network providers may still reject an over-size response
    # explicitly; they must never silently label a partial body as complete.
    v16._dedupe_sources = _v18_dedupe_sources
    v16.MAX_PACKET_SOURCES = 10_000
    v16.MAX_SOURCE_CONTENT = 2_147_483_647

    # V1.7 wire transport remains bounded; only the selector/excerpt policy is
    # replaced.  Canonical context hashes continue to refer to the full value.
    v17._excerpt = _v18_excerpt
    v17._project_source = _v18_project_source
    v17._project_value = _v18_project_value
    v17.HUNT_RESILIENCE_VERSION = HUNT_INTEGRITY_VERSION
    v17.PromptRuntime._provider_messages = staticmethod(_v18_provider_messages)

    def qualified_candidate_status_v18(self: Any, run_id: str, subject_id: str, strict: bool = True) -> tuple[bool, list[str]]:
        qualified, missing = base_qualified(self, run_id, subject_id, strict=strict)
        if not strict:
            return qualified, missing
        payload = _certification_payload(self, run_id, subject_id)
        grade = _certification_grade(payload)
        if grade not in {"A", "A-"}:
            if "V8_CERTIFICATION_A_OR_A_MINUS" not in missing:
                missing = [*missing, "V8_CERTIFICATION_A_OR_A_MINUS"]
            qualified = False
        return qualified, missing

    def commit_final_allocation_v18(self: Any, run: Any, action: str, allocation: dict[str, Any], positive_commitments: int | None = None) -> str:
        action_text = str(action)
        if action_text in {"STARTER", "ADD", "FULL"} and int(allocation.get("shares", 0) or 0) > 0:
            unresolved = self.connection.execute(
                "SELECT 1 FROM stage_results WHERE run_id=? AND stage IN "
                "('CANDIDATE_ENGINEERING_FAILURE','RESEARCH_PROVIDER_FAILURE','SEC_PROVIDER_FAILURE','SEC_STALE_DATA') LIMIT 1",
                (run.run_id,),
            ).fetchone()
            if unresolved:
                raise ValueError("positive allocation blocked: candidate universe has an unresolved evaluation failure")
        return base_commit(self, run, action, allocation, positive_commitments)

    store_module.SQLiteStore.qualified_candidate_status = qualified_candidate_status_v18
    store_module.SQLiteStore.commit_final_allocation = commit_final_allocation_v18

    def validate_sec_artifacts_v18(artifacts: Any) -> None:
        try:
            return base_validate_sec(artifacts)
        except Exception as exc:
            agent = _ACTIVE_AGENT.get()
            items = list(artifacts or [])
            sid = next((str(getattr(item, "subject_id", "") or "") for item in items if getattr(item, "subject_id", None)), "")
            if agent is None or not sid:
                raise
            agent._v18_register_external_failure(sid, "FULL_SEC_FORENSIC", exc)
            return None

    runtime_module.validate_sec_artifacts = validate_sec_artifacts_v18

    base_risk_assess = gates_module.RiskEngine.assess

    def risk_assess_v18(self: Any, current_price: float, execution_stop: float, structural_asymmetry: float, probability_weighted_ev: float, account_equity: float, risk_budget_pct: float = 1.0, worst_plausible_gap: float = 0.0, event_risk_pct: float = 0.0, max_position_shares: int | None = None) -> dict[str, Any]:
        # Preserve the sizing contract but stop dividing a dimensionless
        # Structural Asymmetry by dollar risk and calling that Execution R:R.
        if current_price <= 0 or execution_stop <= 0 or current_price <= execution_stop:
            raise gates_module.ContractViolation("execution stop must be below current price")
        if account_equity <= 0 or risk_budget_pct <= 0:
            raise gates_module.ContractViolation("account equity and risk budget must be positive")
        execution_risk = current_price - execution_stop
        gap = max(0.0, float(worst_plausible_gap))
        sizing_risk = execution_risk + gap
        risk_budget = account_equity * risk_budget_pct / 100.0
        if event_risk_pct > 0:
            risk_budget *= max(0.0, 1.0 - min(float(event_risk_pct) / 100.0, 1.0))
        shares = max(0, int(risk_budget // sizing_risk))
        if max_position_shares is not None:
            shares = min(shares, int(max_position_shares))
        base_target = _BASE_TARGET.get()
        rr = 0.0
        rr_status = "NOT_EVALUATED_BASE_TARGET_REQUIRED"
        if isinstance(base_target, (int, float)) and not isinstance(base_target, bool) and math.isfinite(float(base_target)):
            rr = (float(base_target) - current_price) / execution_risk
            rr_status = "EVALUATED_FROM_BASE_TARGET_AND_EXECUTION_STOP"
        return {
            "arithmetic_source": "PYTHON_RISK_ENGINE_V1.8",
            "risk_per_share": sizing_risk,
            "execution_risk_per_share": execution_risk,
            "gap_adjusted_sizing_risk_per_share": sizing_risk,
            "risk_budget": risk_budget,
            "shares": shares,
            "risk_target_position_shares": shares,
            "execution_rr": rr,
            "execution_rr_status": rr_status,
            "structural_asymmetry": structural_asymmetry,
            "probability_weighted_ev": probability_weighted_ev,
            "worst_plausible_gap": gap,
            "event_risk_pct": event_risk_pct,
            "max_position_shares": max_position_shares,
        }

    gates_module.RiskEngine.assess = risk_assess_v18

    class V18ProductionStockAgent(base_production):
        HUNT_INTEGRITY_VERSION = HUNT_INTEGRITY_VERSION

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._v18_candidate_failures: dict[str, dict[str, Any]] = {}
            provider = getattr(self.config, "sec_provider", None)
            recorded_type = getattr(runtime_module, "RecordedSECProvider", ())
            if provider is not None and not isinstance(provider, _V18SECProviderProxy) and not (recorded_type and isinstance(provider, recorded_type)):
                self.config.sec_provider = _V18SECProviderProxy(provider)

        def _v18_register_external_failure(self, subject_id: str, stage: str, exc: Exception) -> None:
            self._v18_candidate_failures.setdefault(subject_id, {
                "stage": stage,
                "error_type": type(exc).__name__,
                "error": _safe_error(exc),
            })

        def _v18_mark_candidate_failure(self, run: Any, stage: str, subject_id: str, exc: Exception, dependency_ids: list[str]) -> None:
            self._v18_register_external_failure(subject_id, stage, exc)
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE work_items SET status='FAILED', lease_token=NULL, leased_by=NULL, lease_until=NULL, "
                    "last_error=?, updated_at=? WHERE run_id=? AND stage=? AND status IN ('QUEUED','LEASED')",
                    (f"CANDIDATE_ENGINEERING_FAILURE:{type(exc).__name__}", utc_now(), run.run_id, stage),
                )
            dep_hash = self.store.dependency_hash(dependency_ids, run.rule_set.rule_set_hash, run.context_manifest_hash)
            self.store.record_stage_result(
                run.run_id,
                None,
                "CANDIDATE_ENGINEERING_FAILURE",
                subject_id,
                {"status": "ENGINEERING_FAILURE", "failed_stage": stage, "error_type": type(exc).__name__, "error": _safe_error(exc)},
                list(dependency_ids),
                dep_hash,
                self.store.current_evidence_epoch_for(dependency_ids),
                status="FAILED",
            )

        def _work_stage(self, run: Any, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
            if subject_id and stage in _CANDIDATE_MODEL_STAGES and subject_id in self._v18_candidate_failures:
                return _candidate_sentinel(stage, dependency_ids)
            try:
                return super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            except Exception as exc:
                if not subject_id or stage not in _CANDIDATE_MODEL_STAGES:
                    raise
                self._v18_mark_candidate_failure(run, stage, subject_id, exc, dependency_ids)
                return _candidate_sentinel(stage, dependency_ids)

        def _strict_execution_review(self, run: Any, candidate: dict[str, Any], data: dict[str, Any]) -> RunOutcome:
            scenario = candidate.get("economic_scenario") if isinstance(candidate.get("economic_scenario"), dict) else {}
            base_value = scenario.get("base_value")
            token = _BASE_TARGET.set(float(base_value) if isinstance(base_value, (int, float)) and not isinstance(base_value, bool) else None)
            try:
                return super()._strict_execution_review(run, candidate, data)
            finally:
                _BASE_TARGET.reset(token)

        def _candidate_conservation(self, run_id: str) -> list[dict[str, Any]]:
            discovery_row = self.store.get_stage_result(run_id, "STOCK_DISCOVERY", None)
            if not discovery_row or discovery_row.get("status") != "SUCCEEDED":
                return []
            try:
                discovery = json.loads(discovery_row.get("result_json") or "{}")
            except (TypeError, ValueError):
                return []
            candidates = discovery.get("candidates") if isinstance(discovery, dict) else []
            ledger: list[dict[str, Any]] = []
            for candidate in candidates or []:
                if not isinstance(candidate, dict) or not candidate.get("security_id"):
                    continue
                sid = str(candidate["security_id"])
                action = str(candidate.get("recommended_discovery_action") or "EXCLUDE")
                rows = self.store.list_stage_results(run_id, sid)
                values: dict[str, dict[str, Any]] = {}
                dependencies: set[str] = set()
                for row in rows:
                    try:
                        value = json.loads(row.get("result_json") or "{}")
                    except (TypeError, ValueError):
                        value = {}
                    if isinstance(value, dict):
                        values[str(row.get("stage"))] = value
                    try:
                        dependencies.update(str(item) for item in json.loads(row.get("dependency_ids_json") or "[]"))
                    except (TypeError, ValueError):
                        pass
                state = "NOT_EVALUATED"
                reason = "NO_TERMINAL_STATE"
                if action == "EXCLUDE":
                    state, reason = "REJECT", "DISCOVERY_EXCLUDE"
                elif action in {"WATCH_STAGE0", "WATCH_RESET"}:
                    state, reason = "NEXT_STAGE", action
                elif "CANDIDATE_ENGINEERING_FAILURE" in values or sid in self._v18_candidate_failures:
                    state, reason = "ENGINEERING_FAILURE", str((values.get("CANDIDATE_ENGINEERING_FAILURE") or self._v18_candidate_failures.get(sid) or {}).get("failed_stage") or "CANDIDATE_STAGE")
                elif any(stage in values for stage in ("RESEARCH_PROVIDER_FAILURE", "SEC_PROVIDER_FAILURE", "SEC_STALE_DATA")):
                    stage = next(stage for stage in ("RESEARCH_PROVIDER_FAILURE", "SEC_PROVIDER_FAILURE", "SEC_STALE_DATA") if stage in values)
                    state, reason = "PROVIDER_FAILURE", stage
                else:
                    rejected = False
                    for stage in ("STAGE_GATE", "CAPITAL_PRESCREEN_GATE", "CATALYST_GATE", "EXPECTATION_GAP_GATE"):
                        value = values.get(stage) or {}
                        decision = str(value.get("decision") or "")
                        if decision == GateDecision.REJECT.value:
                            state, reason, rejected = "REJECT", stage, True
                            break
                        if decision == GateDecision.INSUFFICIENT_EVIDENCE.value and stage in {"CATALYST_GATE", "EXPECTATION_GAP_GATE"}:
                            if values.get("SOURCE_EXHAUSTED"):
                                state, reason = "SOURCE_EXHAUSTED", stage
                            else:
                                state, reason = "NOT_EVALUATED", stage
                            rejected = True
                            break
                    if not rejected:
                        audit = values.get("ADVERSARIAL_AUDIT") or {}
                        audit_recommendation = str(audit.get("audit_recommendation") or "")
                        audit_status = str(audit.get("status") or "")
                        if bool(audit.get("engineering_failure")):
                            state, reason = "ENGINEERING_FAILURE", "ADVERSARIAL_AUDIT"
                        elif audit_recommendation == "AUDIT_EVIDENCE_INCOMPLETE" or audit_status in {"INCOMPLETE", "CONTEXT_INCOMPLETE", "BLOCKED"}:
                            state, reason = "EVIDENCE_DEBT", "ADVERSARIAL_AUDIT"
                        elif audit_recommendation == "CHALLENGES_CONTINUATION":
                            state, reason = "REJECT", "ADVERSARIAL_AUDIT"
                        else:
                            cert = values.get("V8_CERTIFICATION") or _certification_payload(self.store, run_id, sid)
                            grade = _certification_grade(cert)
                            if grade in {"A", "A-"}:
                                state, reason = "PASS", f"V8_CERTIFICATION_{grade}"
                            elif str((values.get("EXPECTATION_GAP_GATE") or {}).get("decision") or "") == GateDecision.PASS.value:
                                state, reason = "NOT_EVALUATED", "V8_CERTIFICATION_MISSING_OR_NONEXECUTABLE_GRADE"
                            elif values.get("EVIDENCE_DEBT"):
                                state, reason = "EVIDENCE_DEBT", "UNRESOLVED_EVIDENCE_DEBT"
                receipt = {"state": state, "reason": reason, "discovery_action": action, "security_id": sid, "version": HUNT_INTEGRITY_VERSION}
                dep_ids = sorted(dependencies)
                self.store.record_stage_result(
                    run_id, None, "CANDIDATE_CONSERVATION", sid, receipt, dep_ids,
                    self.store.dependency_hash(dep_ids, self.store.get_run(run_id).rule_set.rule_set_hash, self.store.get_run(run_id).context_manifest_hash),
                    self.store.current_evidence_epoch_for(dep_ids),
                )
                ledger.append(receipt)
            counts: dict[str, int] = {}
            for item in ledger:
                counts[item["state"]] = counts.get(item["state"], 0) + 1
            self.store.record_funnel(run_id, "CANDIDATE_CONSERVATION_TOTAL", len(ledger), {"states": counts})
            for state, count in counts.items():
                self.store.record_funnel(run_id, f"CONSERVATION_{state}", count)
            return ledger

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            self._v18_candidate_failures = {}
            token = _ACTIVE_AGENT.set(self)
            try:
                outcome = super()._run_strict(mode, data)
            finally:
                _ACTIVE_AGENT.reset(token)
            if outcome.run_id in {"", "unstarted"}:
                return outcome
            ledger = self._candidate_conservation(outcome.run_id)
            incomplete = [item for item in ledger if item["state"] in {"ENGINEERING_FAILURE", "PROVIDER_FAILURE", "NOT_EVALUATED", "EVIDENCE_DEBT", "SOURCE_EXHAUSTED"}]
            cert_missing = [item for item in incomplete if "V8_CERTIFICATION" in str(item.get("reason"))]
            if cert_missing:
                terminal = "NOT_EVALUABLE_V8_CERTIFICATION_MISSING"
                reason = "V8_STEP18_CERTIFICATION_NOT_AVAILABLE"
            elif incomplete:
                terminal = "NOT_EVALUABLE_PIPELINE_FAILURE"
                reason = "CANDIDATE_EVALUATION_INCOMPLETE"
            else:
                return outcome
            with self.store.transaction() as db:
                db.execute("UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?", (terminal, outcome.run_id))
            return RunOutcome(
                outcome.run_id, outcome.mode, terminal, outcome.qualified_candidates,
                outcome.recommendation, None, None, reason,
            )

    class V18DailyShadowRunner(base_shadow):
        def __init__(self, *args: Any, shadow_version: str = SHADOW_INTEGRITY_VERSION, **kwargs: Any) -> None:
            super().__init__(*args, shadow_version=shadow_version, **kwargs)

        def _decisions(self, shadow_run_id: str, hunt_run_id: str, execution_run_id: str | None) -> list[dict[str, Any]]:
            decisions = super()._decisions(shadow_run_id, hunt_run_id, execution_run_id)
            failures = {
                str(row.get("subject_id")): json.loads(row.get("result_json") or "{}")
                for row in self.store.list_stage_results(hunt_run_id)
                if row.get("stage") == "CANDIDATE_ENGINEERING_FAILURE" and row.get("subject_id")
            }
            for decision in decisions:
                sid = str(decision.get("ticker") or "")
                cert = _certification_payload(self.store, hunt_run_id, sid)
                decision["grade"] = _certification_grade(cert)
                if sid in failures:
                    decision.update({
                        "decision": "NOT_EVALUATED_ENGINEERING_FAILURE",
                        "qualified": False,
                        "watch": False,
                        "rejected": False,
                        "not_evaluated": True,
                        "not_evaluated_stage": failures[sid].get("failed_stage"),
                        "not_evaluated_reason": failures[sid].get("error_type") or "ENGINEERING_FAILURE",
                    })
            return decisions

        def _run_log(self, shadow_run_id: str, hunt_run_id: str, execution_run_id: str | None, health: dict[str, Any], status: str) -> dict[str, Any]:
            log = super()._run_log(shadow_run_id, hunt_run_id, execution_run_id, health, status)
            versions = _version_vector()
            log["shadow_version"] = SHADOW_INTEGRITY_VERSION
            log["strategy_versions"] = versions
            log["strategy_cohort_hash"] = canonical_hash(versions)
            funnel = {str(row["funnel_stage"]): int(row["count"]) for row in self.store.list_funnel(hunt_run_id)}
            if funnel.get("CONSERVATION_ENGINEERING_FAILURE", 0) or funnel.get("CONSERVATION_PROVIDER_FAILURE", 0) or funnel.get("CONSERVATION_NOT_EVALUATED", 0) or funnel.get("CONSERVATION_EVIDENCE_DEBT", 0) or funnel.get("CONSERVATION_SOURCE_EXHAUSTED", 0):
                if funnel.get("CONSERVATION_NOT_EVALUATED", 0):
                    cert_rows = self.store.connection.execute("SELECT result_json FROM stage_results WHERE run_id=? AND stage='CANDIDATE_CONSERVATION'", (hunt_run_id,)).fetchall()
                    missing_cert = any("V8_CERTIFICATION" in str(row["result_json"] or "") for row in cert_rows)
                else:
                    missing_cert = False
                log["investment_conclusion"] = "NOT_EVALUABLE_V8_CERTIFICATION_MISSING" if missing_cert else "NOT_EVALUABLE_PIPELINE_FAILURE"
                log["pipeline_health"] = "DEGRADED"
            return log

        @staticmethod
        def _report(log: dict[str, Any], decisions: list[dict[str, Any]]) -> str:
            qualified = [row for row in decisions if row.get("qualified")]
            watch = [row for row in decisions if row.get("watch")]
            rejected = [row for row in decisions if row.get("rejected")]
            not_evaluated = [row for row in decisions if row.get("not_evaluated")]
            final_actions = [row.get("final_allocation_action") for row in decisions if row.get("final_allocation_action")]
            providers = log.get("providers") or {}
            conclusion = str(log.get("investment_conclusion") or (final_actions[0] if final_actions else "NO_TRADE"))
            allocation_text = ", ".join(str(value) for value in final_actions) if final_actions else conclusion
            lines = [
                "# Daily Stock Agent Report", "",
                f"Date: {str(log.get('started_at'))[:10]}",
                f"Run ID: {log.get('run_id')}",
                f"Shadow Version: {log.get('shadow_version')}",
                f"Git SHA: {log.get('code_git_sha')}", "",
                f"Git Diff Hash: `{log.get('git_diff_hash', 'UNKNOWN')}`",
                f"Source Tree Hash: `{log.get('source_tree_hash', 'UNKNOWN')}`",
                f"Git Dirty: `{log.get('git_dirty', 'UNKNOWN')}`",
                f"Strategy Cohort Hash: `{log.get('strategy_cohort_hash', 'UNKNOWN')}`", "",
                "## 1. Runtime Health", "",
            ]
            for name in ("market", "sec", "research", "luna", "portfolio", "evidence", "gate_integrity"):
                lines.append(f"- {name}: {(providers.get(name) or {}).get('status', 'UNKNOWN')}")
            lines.extend(["", "## 2. Market Regime", "", "```json", json.dumps((log.get("market_context") or {}).get("analysis"), ensure_ascii=False, sort_keys=True, indent=2), "```", "", "## 3. Sector Ranking", "", "SQLite Sector StageResult를 기준으로 기록했습니다.", "", "## 4. Discovery Funnel", ""])
            for key, value in (log.get("universe") or {}).items():
                lines.append(f"- {key}: {value}")
            lines.extend(["", "## 5. Qualified Candidates", ""])
            lines.extend([f"- {row['ticker']}: {row.get('decision')} / grade={row.get('grade')} / evidence={len(row.get('evidence_ids') or [])}" for row in qualified] or ["- NONE"])
            lines.extend(["", "## 6. Watch Candidates", ""])
            lines.extend([f"- {row['ticker']}: {row.get('rejection_reason') or 'insufficient execution-grade evidence'}" for row in watch] or ["- NONE"])
            lines.extend(["", "## 7. Not Evaluated Candidates", ""])
            lines.extend([f"- {row['ticker']}: {row.get('not_evaluated_stage')} / {row.get('not_evaluated_reason')}" for row in not_evaluated] or ["- NONE"])
            lines.extend(["", "## 8. Important Rejected Candidates", ""])
            lines.extend([f"- {row['ticker']}: {row.get('rejected_stage')} / {row.get('rejection_reason')}" for row in rejected] or ["- NONE"])
            lines.extend(["", "## 9. Existing Portfolio Review", "", f"- status: {(providers.get('portfolio') or {}).get('status', 'UNKNOWN')}", "", "## 10. FinalAllocation", "", f"- {allocation_text}", "", "## 11. Today's Conclusion", "", f"- {conclusion}", "- ORDER_EXECUTED = NO", ""])
            return "\n".join(lines)

    def reproducibility_metadata_v18(*args: Any, **kwargs: Any) -> dict[str, Any]:
        metadata = base_reproducibility(*args, **kwargs)
        versions = _version_vector()
        metadata["shadow_version"] = SHADOW_INTEGRITY_VERSION
        metadata["schema_version"] = "shadow-log-v2"
        metadata["strategy_versions"] = versions
        metadata["strategy_cohort_hash"] = canonical_hash(versions)
        return metadata

    runtime_module.ProductionStockAgent = V18ProductionStockAgent
    shadow_module.DailyShadowRunner = V18DailyShadowRunner
    shadow_module.SHADOW_VERSION = SHADOW_INTEGRITY_VERSION
    shadow_module.reproducibility_metadata = reproducibility_metadata_v18
    runtime_module._hunt_integrity_v18_installed = True
