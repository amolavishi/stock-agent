"""Exact failure-injection guard for V8 Discovery Recall forensic audit.

This patch implements the ten failure-injection expectations specified by
V8_DISCOVERY_RECALL_FORENSIC_AUDIT_2026-09-01 without changing Step15-20,
Research Grade thresholds, PRE-A authority, execution authority, or broker
write boundaries.
"""
from __future__ import annotations

import copy
from typing import Any

from . import discovery_recall_lite_v15 as recall

DISCOVERY_RECALL_FAILURE_GUARD_VERSION = "V8_DISCOVERY_RECALL_FAILURE_GUARD_V1.6"
_INSTALLED = False


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() in {"TRUE", "YES", "VERIFIED", "PRESENT", "TOXIC"}


def _explicit_no_event(row: dict[str, Any]) -> bool:
    direct = row.get("one_eight_week_event")
    if direct is False:
        return True
    status = str(
        row.get("one_eight_week_window")
        or row.get("catalyst_window_1_8w")
        or row.get("realization_window_1_8w")
        or ""
    ).strip().upper()
    return status in {"NONE", "ABSENT", "NO_EVENT", "OUTSIDE_WINDOW"}


def _toxic_discounted_vwap(row: dict[str, Any]) -> bool:
    if _truthy(row.get("toxic_discounted_vwap_convert")):
        return True
    structure = " ".join(
        str(row.get(key) or "")
        for key in ("financing_structure", "convertible_structure", "capital_structure_note")
    ).casefold()
    return "discounted" in structure and "vwap" in structure and ("convert" in structure or "note" in structure)


def _structural_fail(scanner_id: str, row: dict[str, Any], reason: str) -> dict[str, Any]:
    sid = recall._sid(row)
    return {
        "security_id": sid,
        "scanner_id": scanner_id,
        "scanner_name": recall.SCANNERS[scanner_id]["name"],
        "signal_strength": "STRONG",
        "research_value": "LOW",
        "disposition": "STRUCTURAL_HARD_FAIL",
        "unknowns": [],
        "missing_evidence": [],
        "verification_path": "already verified structural fatality",
        "recheck_trigger": "none",
        "rationale": reason,
        "fatal_fail": True,
        "research_route_allowed": False,
        "grade_authority": False,
    }


def evaluate_failure_guarded(scanner_id: str, row: dict[str, Any], tech: dict[str, Any]) -> dict[str, Any]:
    """Preserve UNKNOWN while keeping verified structural fatality fatal."""
    if _toxic_discounted_vwap(row):
        return _structural_fail(
            scanner_id,
            row,
            "verified toxic discounted-VWAP convertible structure",
        )

    item = copy.deepcopy(_BASE_EVALUATE(scanner_id, row, tech))
    item.setdefault("fatal_fail", False)
    item.setdefault("research_route_allowed", item.get("disposition") not in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"})

    # TEST 7: a viable company thesis without a 1-8W realization event is a
    # horizon mismatch, not negative evidence and not PRE-A eligibility.
    if _explicit_no_event(row) and item.get("disposition") not in {
        "STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL", "DATA_INTEGRITY_BLOCK"
    }:
        item["disposition"] = "TIME_HORIZON_MISMATCH"
        item["research_value"] = "MEDIUM" if item.get("signal_strength") in {"STRONG", "MODERATE"} else "LOW"
        item["rationale"] = (str(item.get("rationale") or "") + "; no verified 1-8W realization event").strip("; ")
        item["recheck_trigger"] = "new verified 1-8W event"
        item["research_route_allowed"] = False

    # TEST 1/2: UNKNOWN/PARTIAL fields are explicitly nonfatal.  The base
    # evaluator may route them to Secondary/Watch/Insufficient, but never hard
    # fail solely because these fields are unresolved.
    if str(row.get("catalyst_economics") or "").upper() in {"UNKNOWN", "PARTIAL", "VERIFICATION_REQUIRED"}:
        if item.get("disposition") in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"} and not item.get("fatal_fail"):
            item["disposition"] = "DISCOVERY_INSUFFICIENT"
    if str(row.get("consensus") or "").upper() == "UNKNOWN":
        if item.get("disposition") in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"} and not item.get("fatal_fail"):
            item["disposition"] = "DISCOVERY_INSUFFICIENT"
    return item


def aggregate_failure_guarded(evaluations: list[dict[str, Any]]):
    """Archive structural fails in Near-Miss ledger but never route research."""
    routed, secondary, near_miss = _BASE_AGGREGATE(evaluations)
    seen = {(str(item.get("security_id")), str(item.get("scanner_id"))) for item in near_miss}
    for item in evaluations:
        if item.get("disposition") not in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"}:
            continue
        key = (str(item.get("security_id")), str(item.get("scanner_id")))
        if key in seen:
            continue
        archived = copy.deepcopy(item)
        archived["fatal_fail"] = True
        archived["research_route_allowed"] = False
        archived["why_not_deep_dive"] = str(item.get("disposition"))
        archived["queue_status"] = "ARCHIVED_FATAL"
        near_miss.append(archived)
        seen.add(key)
    return routed, secondary, near_miss


def search_stop_allowed(
    *,
    scanner_execution_complete: bool,
    sentinel_complete: bool,
    systematic_misclassification: bool,
    strategy_eligible_signal_coverage: int,
    open_high_research_value_secondary: int,
    high_research_value_near_miss: int,
    last_rounds_low_signal_secondary_evidence_yield: bool,
    source_exhausted: bool,
    explicit_operational_ceiling_documented: bool,
) -> bool:
    """Pure TEST 4/5 stop invariant used by hostile regressions.

    Deep-dive yield is intentionally absent from the signature.  A shallow
    150-name sweep without scanner receipts can never satisfy this function.
    """
    if not scanner_execution_complete or not sentinel_complete or systematic_misclassification:
        return False
    if int(strategy_eligible_signal_coverage) < recall.MIN_SIGNAL_COVERAGE:
        return False
    if int(open_high_research_value_secondary) > 0 or int(high_research_value_near_miss) > 0:
        return False
    return bool(source_exhausted or (explicit_operational_ceiling_documented and last_rounds_low_signal_secondary_evidence_yield))


_BASE_EVALUATE = recall._evaluate
_BASE_AGGREGATE = recall._aggregate


def install_discovery_recall_failure_guard_v16() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    recall._evaluate = evaluate_failure_guarded
    recall._aggregate = aggregate_failure_guarded
    _INSTALLED = True
