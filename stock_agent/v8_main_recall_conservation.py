"""False-negative conservation for MAIN Discovery technical-data gaps.

The legacy strict loop silently skipped discovered candidates that lacked a
usable technical feature object.  That is not an investment rejection.  This
layer preserves such names as explicit Discovery Secondary/evidence debt and
prevents a clean search-stop conclusion until the data gap is resolved.

No technical gate is weakened.  Missing technical evidence still cannot pass
Stage/Execution; it simply cannot erase a fundamentally interesting candidate.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Any

from . import runtime as runtime_module
from .models import RunMode, RunOutcome

V8_MAIN_RECALL_CONSERVATION_VERSION = "V8_MAIN_RECALL_CONSERVATION_V1.0"
_INSTALLED = False


def _record_debt(agent: Any, run: Any, sid: str, action: str) -> None:
    payload = {
        "security_id": sid,
        "status": "EVIDENCE_DEBT",
        "missing_evidence": ["TECHNICAL_FEATURE_SNAPSHOT"],
        "discovery_action_before_debt": action,
        "discovery_action_after_debt": "DEEP_DIVE_SECONDARY",
        "research_grade_authority": False,
        "execution_authority": False,
        "version": V8_MAIN_RECALL_CONSERVATION_VERSION,
    }
    dep_hash = agent.store.dependency_hash([], run.rule_set.rule_set_hash, run.context_manifest_hash)
    agent.store.record_stage_result(run.run_id, None, "DISCOVERY_TECHNICAL_EVIDENCE_DEBT", sid, payload, [], dep_hash, 0)


def install_v8_main_recall_conservation() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_main_recall_conservation_version", None) == V8_MAIN_RECALL_CONSERVATION_VERSION:
        return current

    class V8MainRecallConservationProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_main_recall_conservation_version = V8_MAIN_RECALL_CONSERVATION_VERSION

        def _work_stage(self, run, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
            result = super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            if stage != "STOCK_DISCOVERY" or prompt_id != "workflow.stock_scout" or not isinstance(result, dict):
                return result
            raw = payload.get("raw_input") if isinstance(payload, dict) else {}
            technical = (raw or {}).get("technical_features") if isinstance(raw, dict) else {}
            technical_ids = {str(key).upper() for key in technical} if isinstance(technical, dict) else set()
            candidates = [copy.deepcopy(item) for item in (result.get("candidates") or []) if isinstance(item, dict)]
            debt_ids: list[str] = []
            for item in candidates:
                sid = str(item.get("security_id") or "").upper()
                action = str(item.get("recommended_discovery_action") or "")
                if not sid or sid in technical_ids or action not in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY"}:
                    continue
                debt_ids.append(sid)
                _record_debt(self, run, sid, action)
                item["recommended_discovery_action"] = "DEEP_DIVE_SECONDARY"
                rationale = str(item.get("rationale") or "").strip()
                item["rationale"] = (rationale + " | " if rationale else "") + "technical evidence unresolved; retained for secondary verification"
            if not debt_ids:
                return result
            updated = dict(result)
            updated["candidates"] = candidates
            self.store.record_funnel(run.run_id, "DISCOVERY_TECHNICAL_EVIDENCE_DEBT", len(debt_ids), {
                "security_ids": sorted(set(debt_ids))[:300],
                "semantics": "UNKNOWN_TECHNICAL_IS_NOT_INVESTMENT_REJECT",
                "version": V8_MAIN_RECALL_CONSERVATION_VERSION,
                "grade_authority": False,
            })
            return updated

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            rows = [row for row in self.store.list_stage_results(run_id) if row.get("stage") == "DISCOVERY_TECHNICAL_EVIDENCE_DEBT" and row.get("subject_id")]
            unresolved: list[str] = []
            for row in rows:
                sid = str(row.get("subject_id"))
                # A later completed technical/stage receipt resolves the debt;
                # absent that, the run cannot claim search exhaustion.
                stage_row = self.store.get_stage_result(run_id, "STAGE_GATE", sid)
                if not stage_row:
                    unresolved.append(sid)
            if not unresolved:
                return outcome
            self.store.record_funnel(run_id, "DISCOVERY_SEARCH_STOP_AUDIT", len(unresolved), {
                "search_stop_allowed": False,
                "reason": "UNRESOLVED_TECHNICAL_EVIDENCE_DEBT",
                "security_ids": sorted(unresolved)[:300],
                "deep_dive_yield_zero_alone_proves_exhaustion": False,
                "grade_authority": False,
            })
            current_outcome = str(getattr(outcome, "outcome", "") or "")
            if current_outcome.startswith("NOT_EVALUABLE"):
                return replace(outcome, blocked_reason=(outcome.blocked_reason or "") + f"; technical_debt={len(unresolved)}")
            if current_outcome in {"NO_QUALIFIED_CANDIDATE", "NO_TRADE", "BLOCKED_BY_EVIDENCE_GAP", "QUALIFIED_CANDIDATE_POOL"}:
                terminal = "NOT_EVALUABLE_DISCOVERY_DATA_INTEGRITY"
                with self.store.transaction() as db:
                    db.execute("UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?", (terminal, run_id))
                return replace(outcome, outcome=terminal, blocked_reason=f"unresolved technical evidence debt for {len(unresolved)} discovered candidate(s)")
            return outcome

    runtime_module.ProductionStockAgent = V8MainRecallConservationProductionStockAgent
    _INSTALLED = True
    return V8MainRecallConservationProductionStockAgent
