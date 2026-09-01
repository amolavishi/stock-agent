"""V8 Discovery Recall forensic contract completion V1.5.1.

This patch tightens the Lite runtime against DR-001..DR-013 from the forensic
audit without changing Step15-20 certification logic.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from . import discovery_recall_lite_v15 as recall
from . import runtime as runtime_module

DISCOVERY_RECALL_CONTRACT_VERSION = "V8_DISCOVERY_RECALL_CONTRACT_V1.5.1"
DISCOVERY_RECALL_LEDGER_VERSION = "V8_DISCOVERY_RECALL_LEDGER_V1.5.1"
_CONTRACT_INSTALLED = False
_LEDGER_INSTALLED = False


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    return str(value or "").strip().upper() in {"TRUE", "YES", "1", "TOXIC", "VARIABLE_PRICE"}


def install_discovery_recall_contract_v151() -> None:
    """Patch the routing/receipt functions used by the already-defined runtime."""
    global _CONTRACT_INSTALLED
    if _CONTRACT_INSTALLED:
        return
    original_evaluate = recall._evaluate
    original_receipt = recall._scanner_receipt

    def evaluate(scanner_id: str, row: dict[str, Any], tech: dict[str, Any]) -> dict[str, Any]:
        item = dict(original_evaluate(scanner_id, row, tech))
        # Only an explicit structural fact may receive structural hard-fail.
        # UNKNOWN/missing evidence is never promoted into this state.
        if _truthy(row.get("toxic_variable_convert")) or _truthy(row.get("toxic_convertible")):
            item.update({
                "signal_strength": "NONE",
                "research_value": "LOW",
                "disposition": "STRUCTURAL_HARD_FAIL",
                "unknowns": [],
                "missing_evidence": [],
                "verification_path": "already verified toxic capital-structure fact",
                "recheck_trigger": "none",
                "rationale": "explicit toxic variable-price capital structure is a structural veto",
            })
        else:
            event_state = str(row.get("near_term_event_status") or "").strip().upper()
            if event_state in {"NONE", "NO_EVENT", "OUTSIDE_8W", "NO_1_8W_EVENT"} and item.get("disposition") not in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL", "DATA_INTEGRITY_BLOCK"}:
                item["disposition"] = "TIME_HORIZON_MISMATCH"
                item["rationale"] = str(item.get("rationale") or "") + "; explicit evidence says no 1-8W information event"
        item["expected_resolution"] = "resolve the listed decision-critical evidence path in the current research cycle"
        item["expiry"] = "END_OF_CURRENT_SEARCH_CYCLE_OR_NEXT_MATERIAL_EVENT"
        item["secondary_is_pre_a"] = False
        item["research_value_is_research_grade"] = False
        return item

    def receipt(scanner_id: str, rows: list[dict[str, Any]], technical: dict[str, Any]):
        value, evaluations, rounds = original_receipt(scanner_id, rows, technical)
        value = dict(value)
        ids = [recall._sid(row) for row in rows if recall._sid(row)]
        missing_technical = [sid for sid in ids if not isinstance(technical.get(sid), dict) or not technical.get(sid)]
        reasons = Counter(
            str(item.get("disposition") or "UNKNOWN")
            for item in evaluations
            if str(item.get("disposition") or "") not in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY"}
        )
        value["watch_count"] = sum(1 for item in evaluations if str(item.get("disposition") or "").startswith("WATCH") or item.get("disposition") in {"TIME_HORIZON_MISMATCH", "PRICE_STAGE_MISMATCH"})
        value["top_rejection_reasons"] = [{"reason": reason, "count": count} for reason, count in reasons.most_common(5)]
        value["missing_technical_count"] = len(missing_technical)
        value["missing_technical_sample"] = missing_technical[:30]
        if not rows:
            value["status"] = "BREADTH_ONLY"
            value["output_contract_complete"] = False
        elif len(missing_technical) == len(ids):
            value["status"] = "DATA_BLOCKED"
            value["output_contract_complete"] = False
        elif missing_technical:
            value["status"] = "SIGNAL_SCAN_PARTIAL"
            value["output_contract_complete"] = False
        value["allowed_statuses"] = ["BREADTH_ONLY", "SIGNAL_SCAN_PARTIAL", "SIGNAL_SCAN_COMPLETE", "SOURCE_EXHAUSTED", "DATA_BLOCKED"]
        value["lane_touched_is_scanner_executed"] = False
        return value, evaluations, rounds

    recall._evaluate = evaluate
    recall._scanner_receipt = receipt
    _CONTRACT_INSTALLED = True


def _funnel(store: Any, run_id: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in store.list_funnel(run_id):
        result[str(row.get("funnel_stage") or "")] = int(row.get("count") or 0)
    return result


def _resolution(store: Any, run_id: str, sid: str) -> tuple[str, str]:
    if store.get_stage_result(run_id, "V8_CERTIFICATION", sid):
        return "RESOLVED_CERTIFIED_RESEARCH", "candidate reached authoritative certification"
    if store.get_stage_result(run_id, "DEEP_RESEARCH", sid):
        return "RESOLVED_DEEP_RESEARCH", "candidate reached deep research"
    for stage in ("STAGE_GATE", "CAPITAL_PRESCREEN_GATE", "CATALYST_GATE"):
        row = store.get_stage_result(run_id, stage, sid)
        if not row:
            continue
        try:
            value = json.loads(row.get("result_json") or "{}")
        except (TypeError, ValueError):
            value = {}
        if str(value.get("decision") or "") == "REJECT":
            if stage == "CATALYST_GATE":
                return "CLOSED_TIME_OR_THESIS_REJECTION", f"{stage} explicit reject"
            return "CLOSED_VERIFIED_REJECTION", f"{stage} explicit reject"
        if str(value.get("evaluation_status") or "").startswith("NOT_EVALUATED"):
            return "OPEN_RESEARCH_DEBT", f"{stage} not evaluable"
    for stage in ("CANDIDATE_ENGINEERING_FAILURE", "RESEARCH_PROVIDER_FAILURE", "SEC_PROVIDER_FAILURE", "SEC_STALE_DATA"):
        if store.get_stage_result(run_id, stage, sid):
            return "OPEN_DATA_BLOCKED", f"{stage} requires retry/new evidence"
    return "WATCH_EXPIRED_UNRESOLVED", "current search cycle ended without a resolving authoritative receipt"


def install_discovery_recall_ledger_v151() -> type:
    """Persist DR-003/006/009/015 state after the composed run completes."""
    global _LEDGER_INSTALLED
    current = runtime_module.ProductionStockAgent
    if _LEDGER_INSTALLED or getattr(current, "discovery_recall_ledger_version", None) == DISCOVERY_RECALL_LEDGER_VERSION:
        return current

    class DiscoveryRecallLedgerProductionStockAgent(current):  # type: ignore[misc,valid-type]
        discovery_recall_contract_version = DISCOVERY_RECALL_CONTRACT_VERSION
        discovery_recall_ledger_version = DISCOVERY_RECALL_LEDGER_VERSION

        def _run_strict(self, mode, data):
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            state = getattr(self, "_discovery_recall_state", {}).get(run_id)
            if not isinstance(state, dict):
                return outcome
            funnel = _funnel(self.store, run_id)
            receipts = state.get("receipts") or {}
            secondary = list(state.get("secondary") or [])
            near_miss = list(state.get("near_miss") or [])
            signal_events = sum(int(item.get("raw_signal_count") or 0) for item in receipts.values())
            partial_events = sum(int(item.get("partial_signal_count") or 0) for item in receipts.values())
            structural_events = sum(int(item.get("structural_fail_count") or 0) for item in receipts.values())
            high_secondary = sum(1 for item in secondary if item.get("research_value") == "HIGH")
            high_near = sum(1 for item in near_miss if item.get("research_value") == "HIGH")
            raw = int(funnel.get("RAW_UNIVERSE", 0))
            eligible = int(funnel.get("ADV_FILTER", 0))
            signal_coverage = len(state.get("evaluated") or [])
            deep = int(funnel.get("DEEP_RESEARCH", 0))
            cheap_survived = int(funnel.get("V8_RESEARCH_QUEUE", funnel.get("CAPITAL_PRESCREEN_PASS", 0)))
            catalyst_confirmed = int(funnel.get("CATALYST_PASS", 0))
            catalyst_estimated = int(funnel.get("CATALYST_NOT_EVALUATED", 0))
            verification_required = len(secondary) + len(near_miss)
            ledger = {
                "version": DISCOVERY_RECALL_LEDGER_VERSION,
                "raw_unique": raw,
                "eligible_unique": eligible,
                "scanner_signal_coverage": signal_coverage,
                "cheap_gate_survived": cheap_survived,
                "signal_detected": signal_events,
                "partial_signal": partial_events,
                "catalyst_confirmed": catalyst_confirmed,
                "catalyst_estimated": catalyst_estimated,
                "verification_required": verification_required,
                "research_value_high": high_secondary + high_near,
                "secondary": len(secondary),
                "deep": deep,
                "watch": len(near_miss),
                "hard_fail": structural_events,
                "signal_event_yield_per_eligible": signal_events / max(1, eligible),
                "secondary_yield_per_signal_coverage": len(secondary) / max(1, signal_coverage),
                "deep_yield_per_signal_coverage": deep / max(1, signal_coverage),
                "marginal_yield_has_denominator": True,
                "grade_authority": False,
            }
            self.store.record_funnel(run_id, "DISCOVERY_MULTI_STAGE_FUNNEL", signal_coverage, ledger)
            self.store.record_funnel(run_id, "DISCOVERY_UNIVERSE_QUALITY", signal_coverage, {
                "raw_unique_ticker_coverage": raw,
                "strategy_eligible_unique_coverage": eligible,
                "scanner_signal_coverage": signal_coverage,
                "preferred_universe_count": signal_coverage,
                "eligible_universe_count": eligible,
                "context_only_count": max(0, raw - eligible),
                "out_of_preferred_range_count": max(0, eligible - signal_coverage),
                "mega_cap_context_cannot_satisfy_target_coverage": True,
                "grade_authority": False,
            })
            zero_dep = self.store.dependency_hash([], self.store.get_run(run_id).rule_set.rule_set_hash, self.store.get_run(run_id).context_manifest_hash)
            for item in secondary:
                sid = str(item.get("security_id") or "")
                if not sid:
                    continue
                status, reason = _resolution(self.store, run_id, sid)
                self.store.record_stage_result(run_id, None, "DISCOVERY_SECONDARY_RESOLUTION", sid, {
                    "security_id": sid,
                    "queue_status": status,
                    "reason": reason,
                    "expected_resolution": item.get("expected_resolution"),
                    "expiry": item.get("expiry"),
                    "secondary_is_pre_a": False,
                    "grade_authority": False,
                }, [], zero_dep, 0)
            return outcome

    runtime_module.ProductionStockAgent = DiscoveryRecallLedgerProductionStockAgent
    _LEDGER_INSTALLED = True
    return DiscoveryRecallLedgerProductionStockAgent
