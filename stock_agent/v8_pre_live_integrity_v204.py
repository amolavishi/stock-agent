"""Coverage/sentinel semantic hardening for V8 pre-live validation.

A full coverage ledger is useful only if its routing semantics are internally
consistent, and a rejection sentinel is complete only if it actually audits
the exact requested sample.  This layer validates both properties and remains
fully grade/action/broker non-authoritative.
"""
from __future__ import annotations

import json
from typing import Any

from . import runtime as runtime_module
from . import v8_main_discovery_integrity as integrity
from . import v8_pre_live_integrity_v20 as v20

V8_PRE_LIVE_SENTINEL_PATCH_VERSION = "V8_PRE_LIVE_SENTINEL_V2.0.4"
_INSTALLED = False
_BASE_CONTRACT = v20._contract_complete_v20

_ACTION_TO_DISPOSITION = {
    "DEEP_DIVE_NOW": "RETAINED",
    "DEEP_DIVE_SECONDARY": "RETAINED",
    "WATCH_STAGE0": "WATCH",
    "WATCH_RESET": "WATCH",
    "EXCLUDE": "EXCLUDE",
}


def contract_complete_v204(scanner_id: str, result: dict[str, Any], expected_count: int) -> tuple[bool, list[str]]:
    complete, failures = _BASE_CONTRACT(scanner_id, result, expected_count)
    ledger = [item for item in (result.get("coverage_ledger") or []) if isinstance(item, dict)]
    by_sid = {str(item.get("security_id") or "").upper(): item for item in ledger if item.get("security_id")}
    candidates = [item for item in (result.get("candidates") or []) if isinstance(item, dict)]

    for candidate in candidates:
        sid = str(candidate.get("security_id") or "").upper()
        action = str(candidate.get("recommended_discovery_action") or "")
        row = by_sid.get(sid)
        if not row:
            failures.append(f"CANDIDATE_MISSING_COVERAGE_ROW:{sid}")
            continue
        expected_disposition = _ACTION_TO_DISPOSITION.get(action)
        if expected_disposition and str(row.get("disposition") or "") != expected_disposition:
            failures.append(f"CANDIDATE_COVERAGE_ROUTING_MISMATCH:{sid}:{action}")

    candidate_ids = {str(item.get("security_id") or "").upper() for item in candidates if item.get("security_id")}
    for row in ledger:
        sid = str(row.get("security_id") or "").upper()
        disposition = str(row.get("disposition") or "")
        failure = str(row.get("failure_class") or "")
        cheap = str(row.get("cheap_hard_gate_status") or "")
        evidence = [str(item) for item in (row.get("evidence_ids") or []) if str(item)]

        if disposition == "RETAINED" and sid not in candidate_ids:
            failures.append(f"RETAINED_LEDGER_ROW_MISSING_CANDIDATE:{sid}")
        if disposition == "EXCLUDE":
            if failure not in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"}:
                failures.append(f"EXCLUDE_WITHOUT_HARD_FAIL:{sid}")
            if not evidence:
                failures.append(f"EXCLUDE_WITHOUT_EVIDENCE:{sid}")
        if failure == "STRUCTURAL_HARD_FAIL":
            if disposition != "EXCLUDE":
                failures.append(f"STRUCTURAL_FAIL_NOT_EXCLUDED:{sid}")
            if cheap != "FAIL":
                failures.append(f"STRUCTURAL_FAIL_WITHOUT_CHEAP_GATE_FAIL:{sid}")
            if not evidence:
                failures.append(f"STRUCTURAL_FAIL_WITHOUT_EVIDENCE:{sid}")
        if failure == "THESIS_HARD_FAIL" and disposition != "EXCLUDE":
            failures.append(f"THESIS_FAIL_NOT_EXCLUDED:{sid}")
        if disposition == "DATA_BLOCK" and failure != "DATA_INTEGRITY_BLOCK":
            failures.append(f"DATA_BLOCK_FAILURE_CLASS_MISMATCH:{sid}")
        if failure == "DATA_INTEGRITY_BLOCK" and disposition != "DATA_BLOCK":
            failures.append(f"DATA_INTEGRITY_BLOCK_ROUTING_MISMATCH:{sid}")
        if disposition in {"NO_SIGNAL", "WATCH", "RETAINED"} and failure in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"}:
            failures.append(f"HARD_FAIL_RETAINED_OR_WATCHED:{sid}")

    source_exhausted = bool(result.get("source_exhaustion"))
    expansion = [str(item) for item in (result.get("search_expansion_questions") or []) if str(item).strip()]
    source_reason = str(result.get("source_exhaustion_reason") or "").strip().upper()
    if source_exhausted and expansion:
        failures.append("SOURCE_EXHAUSTED_WITH_OPEN_EXPANSION_QUESTIONS")
    if source_exhausted and source_reason in {"", "NOT_PROVEN", "UNKNOWN"}:
        failures.append("SOURCE_EXHAUSTED_WITHOUT_REASON")

    return complete and not failures, sorted(set(failures))


def sentinel_sample_v204(results: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    # Start from the V2.0 disposition-stratified pool, then guarantee scanner
    # family representation wherever that scanner has a non-retained row.
    pool = v20._sentinel_sample_v20(results, limit=max(10000, int(limit)))
    by_scanner: dict[str, list[dict[str, Any]]] = {}
    for item in pool:
        by_scanner.setdefault(str(item.get("scanner_id") or ""), []).append(item)
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for scanner_id in sorted(by_scanner):
        if len(selected) >= limit:
            break
        item = by_scanner[scanner_id][0]
        key = (str(item.get("security_id") or ""), scanner_id)
        selected.append(item)
        seen.add(key)
    for item in pool:
        if len(selected) >= limit:
            break
        key = (str(item.get("security_id") or ""), str(item.get("scanner_id") or ""))
        if key in seen:
            continue
        selected.append(item)
        seen.add(key)
    return selected[:limit]


def _parse_stage_payload(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    try:
        value = json.loads(row.get("result_json") or "{}")
    except (AttributeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def install_v8_pre_live_integrity_v204() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_pre_live_sentinel_patch_version", None) == V8_PRE_LIVE_SENTINEL_PATCH_VERSION:
        return current

    integrity._contract_complete = contract_complete_v204  # type: ignore[assignment]
    v20._contract_complete_v20 = contract_complete_v204  # type: ignore[assignment]
    v20._sentinel_sample_v20 = sentinel_sample_v204  # type: ignore[assignment]

    class V8PreLiveSentinelProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_pre_live_sentinel_patch_version = V8_PRE_LIVE_SENTINEL_PATCH_VERSION

        def _work_stage(self, run: Any, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None):
            result = super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            if stage != "STOCK_DISCOVERY" or prompt_id != "workflow.stock_scout":
                return result

            round_results: list[dict[str, Any]] = []
            for row in self.store.list_stage_results(run.run_id):
                stage_name = str(row.get("stage") or "")
                if not stage_name.startswith("V8_MAIN_SCANNER_") or "_R" not in stage_name or row.get("status") != "SUCCEEDED":
                    continue
                value = _parse_stage_payload(row)
                if value:
                    round_results.append(value)
            expected = sentinel_sample_v204(round_results, 30)
            expected_pairs = {(str(item.get("security_id") or ""), str(item.get("scanner_id") or "")) for item in expected}

            sentinel_row = self.store.get_stage_result(run.run_id, "V8_MAIN_REJECTION_SENTINEL", None)
            sentinel = _parse_stage_payload(sentinel_row)
            audits = [item for item in (sentinel.get("audits") or []) if isinstance(item, dict)]
            actual_pairs = [(str(item.get("security_id") or ""), str(item.get("scanner_id") or "")) for item in audits]
            exact = (
                str(sentinel.get("status") or "") == "COMPLETE"
                and sentinel.get("grade_authority") is False
                and len(actual_pairs) == len(expected_pairs)
                and len(set(actual_pairs)) == len(actual_pairs)
                and set(actual_pairs) == expected_pairs
            )
            state = getattr(self, "_v8_main_discovery_state", {}).get(run.run_id)
            if isinstance(state, dict):
                state["sentinel_complete"] = bool(exact)
                if not exact:
                    state["systematic_false_negative_risk"] = True
            self.store.record_funnel(run.run_id, "V8_MAIN_SENTINEL_COVERAGE_VALIDATION", len(expected_pairs), {
                "expected_sample_size": len(expected_pairs),
                "returned_audit_count": len(actual_pairs),
                "exact_sample_coverage": exact,
                "missing_pairs": sorted(expected_pairs - set(actual_pairs))[:100],
                "unexpected_pairs": sorted(set(actual_pairs) - expected_pairs)[:100],
                "duplicate_audit_rows": len(actual_pairs) - len(set(actual_pairs)),
                "scanner_ids_expected": sorted({scanner for _, scanner in expected_pairs}),
                "scanner_ids_returned": sorted({scanner for _, scanner in actual_pairs}),
                "grade_authority": False,
                "version": V8_PRE_LIVE_SENTINEL_PATCH_VERSION,
            })
            return result

    runtime_module.ProductionStockAgent = V8PreLiveSentinelProductionStockAgent
    _INSTALLED = True
    return V8PreLiveSentinelProductionStockAgent
