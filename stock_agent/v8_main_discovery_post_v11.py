"""System-wide post-Discovery validator for V8 MAIN.

Installed around the existing MAIN coach after the round executor. It owns no
candidate-generation logic and no Research Grade authority. It persists
Secondary/Near-Miss state and validates whether Discovery may cleanly stop.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from . import runtime as runtime_module
from . import v8_main_discovery_coach as coach
from .models import RunMode, RunOutcome, utc_now
from .v8_main_discovery_integrity import (
    SCANNER_REQUIRED_DIMENSIONS,
    V8_MAIN_DISCOVERY_INTEGRITY_VERSION,
    _expire_secondary,
    _merge_candidate,
    _parse_result,
    _provider_exhaustion,
    _upsert_secondary,
)

V8_MAIN_DISCOVERY_POST_VERSION = "V8_MAIN_DISCOVERY_POST_V1.1"
_INSTALLED = False


def _system_rounds(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for item in rounds:
        if not isinstance(item, dict):
            continue
        match = re.search(r"-R(\d+)$", str(item.get("round_id") or ""))
        if not match:
            continue
        sequence = int(match.group(1))
        row = grouped.setdefault(sequence, {
            "round_sequence": sequence,
            "scanner_ids": set(),
            "new_unique_tickers": 0,
            "new_signal": 0,
            "new_secondary": 0,
            "new_high_research_value": 0,
            "new_independent_evidence": 0,
            "new_deep_dive_now": 0,
        })
        row["scanner_ids"].add(str(item.get("scanner_id") or ""))
        # The same universe chunk is evaluated by all 13 scanners. Breadth is
        # therefore the maximum per-scanner unique count, never the sum.
        row["new_unique_tickers"] = max(int(row["new_unique_tickers"]), int(item.get("new_unique_tickers") or 0))
        for key in ("new_signal", "new_secondary", "new_high_research_value", "new_independent_evidence", "new_deep_dive_now"):
            row[key] += int(item.get(key) or 0)
    result: list[dict[str, Any]] = []
    cumulative = 0
    for sequence in sorted(grouped):
        row = grouped[sequence]
        cumulative += int(row["new_unique_tickers"])
        result.append({
            **{key: value for key, value in row.items() if key != "scanner_ids"},
            "scanner_ids": sorted(row["scanner_ids"]),
            "scanner_family_complete": row["scanner_ids"] == set(SCANNER_REQUIRED_DIMENSIONS),
            "cumulative_unique_tickers": cumulative,
            "marginal_candidate_yield": (int(row["new_deep_dive_now"]) + int(row["new_secondary"])) / max(1, int(row["new_unique_tickers"])),
            "marginal_evidence_yield": int(row["new_independent_evidence"]) / max(1, int(row["new_unique_tickers"])),
        })
    return result


def _two_complete_low_yield_system_rounds(rounds: list[dict[str, Any]]) -> bool:
    complete = [row for row in _system_rounds(rounds) if row.get("scanner_family_complete")]
    if len(complete) < 2:
        return False
    return all(
        int(row.get("new_signal") or 0) == 0
        and int(row.get("new_secondary") or 0) == 0
        and int(row.get("new_independent_evidence") or 0) == 0
        for row in complete[-2:]
    )


def install_v8_main_discovery_post_v11() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_main_discovery_post_version", None) == V8_MAIN_DISCOVERY_POST_VERSION:
        return current

    class V8MainDiscoveryPostV11ProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_main_discovery_post_version = V8_MAIN_DISCOVERY_POST_VERSION
        v8_main_discovery_integrity_version = V8_MAIN_DISCOVERY_INTEGRITY_VERSION

        def _work_stage(self, run, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
            result = super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            if stage != "STOCK_DISCOVERY" or prompt_id != "workflow.stock_scout" or not isinstance(result, dict):
                return result

            final_by_sid = {
                str(item.get("security_id") or "").upper(): item
                for item in (result.get("candidates") or [])
                if isinstance(item, dict) and item.get("security_id")
            }
            scanner_by_sid: dict[str, list[str]] = {}
            scanner_candidates: dict[str, dict[str, Any]] = {}
            structural: set[str] = set()
            thesis_fail: set[str] = set()
            for row in self.store.list_stage_results(run.run_id):
                stage_name = str(row.get("stage") or "")
                if not stage_name.startswith("V8_MAIN_SCANNER_") or "_R" not in stage_name or row.get("status") != "SUCCEEDED":
                    continue
                value = _parse_result(row)
                scanner_id = str(value.get("scanner_id") or "")
                for item in value.get("candidates") or []:
                    if not isinstance(item, dict) or not item.get("security_id"):
                        continue
                    sid = str(item["security_id"]).upper()
                    scanner_by_sid.setdefault(sid, []).append(scanner_id)
                    scanner_candidates[sid] = _merge_candidate(scanner_candidates.get(sid), item)
                    if item.get("failure_class") == "STRUCTURAL_HARD_FAIL":
                        structural.add(sid)
                    if item.get("failure_class") == "THESIS_HARD_FAIL":
                        thesis_fail.add(sid)

            secondary_ids: set[str] = set()
            near_miss_ids: set[str] = set()
            unresolved_high_near_miss: set[str] = set()
            for sid, item in scanner_candidates.items():
                scanner_action = str(item.get("recommended_discovery_action") or "")
                final_action = str((final_by_sid.get(sid) or {}).get("recommended_discovery_action") or scanner_action)
                if sid in structural or sid in thesis_fail or final_action == "EXCLUDE":
                    self.store.connection.execute(
                        "UPDATE discovery_secondary_queue SET status='CLOSED_REJECT',updated_at=? WHERE security_id=? AND status='OPEN'",
                        (utc_now(), sid),
                    )
                    continue
                if final_action == "DEEP_DIVE_NOW":
                    self.store.connection.execute(
                        "UPDATE discovery_secondary_queue SET status='RESOLVED_TO_DEEP_DIVE',updated_at=? WHERE security_id=? AND status='OPEN'",
                        (utc_now(), sid),
                    )
                    continue
                if final_action == "DEEP_DIVE_SECONDARY" or (item.get("research_value") == "HIGH" and final_action in {"WATCH_STAGE0", "WATCH_RESET"}):
                    _upsert_secondary(self.store, run.run_id, item, scanner_by_sid.get(sid, []))
                    secondary_ids.add(sid)
                if final_action in {"DEEP_DIVE_SECONDARY", "WATCH_STAGE0", "WATCH_RESET"}:
                    near_miss_ids.add(sid)
                    if item.get("research_value") == "HIGH":
                        unresolved_high_near_miss.add(sid)

            expired = _expire_secondary(self.store)
            self.store.record_funnel(run.run_id, "DISCOVERY_SECONDARY_QUEUE", len(secondary_ids), {
                "security_ids": sorted(secondary_ids)[:300],
                "persistent_table": "discovery_secondary_queue",
                "expired_to_watch_count": expired,
                "secondary_is_pre_a": False,
                "research_value_is_research_grade": False,
                "grade_authority": False,
            })
            self.store.record_funnel(run.run_id, "DISCOVERY_NEAR_MISS_LEDGER", len(near_miss_ids), {
                "security_ids": sorted(near_miss_ids)[:300],
                "unresolved_high_research_value": sorted(unresolved_high_near_miss)[:300],
                "structural_hard_fail_excluded": sorted(structural)[:300],
                "thesis_hard_fail_excluded": sorted(thesis_fail)[:300],
                "audit_status": "COMPLETE",
                "grade_authority": False,
            })
            self._v8_post_state = getattr(self, "_v8_post_state", {})
            self._v8_post_state[run.run_id] = {
                "unresolved_high_near_miss": sorted(unresolved_high_near_miss),
                "near_miss_audit_complete": True,
            }
            return result

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            integrity = getattr(self, "_v8_integrity_state", {}).get(run_id) or {}
            scanners = integrity.get("scanners") or {}
            rounds = list(integrity.get("rounds") or [])
            if not scanners:
                return outcome

            completed = {
                sid for sid, receipt in scanners.items()
                if receipt.get("execution_status") == "SIGNAL_SCAN_COMPLETE"
                and receipt.get("output_validated") is True
            }
            scanner_complete = completed == set(SCANNER_REQUIRED_DIMENSIONS)
            signal_coverage = min((int(receipt.get("evaluated_count") or 0) for receipt in scanners.values()), default=0)
            coach_state = getattr(self, "_v8_main_discovery_state", {}).get(run_id) or {}
            eligible = int(coach_state.get("strategy_eligible_unique") or signal_coverage)
            source_exhausted, universe = _provider_exhaustion(self.store, run_id, eligible)
            system_rounds = _system_rounds(rounds)
            low_tail = _two_complete_low_yield_system_rounds(rounds)

            post = getattr(self, "_v8_post_state", {}).get(run_id) or {}
            unresolved_high = set(post.get("unresolved_high_near_miss") or [])
            unresolved_high.update(str(item) for item in (coach_state.get("unresolved_high_research_value_near_miss") or []))
            open_high = int(self.store.connection.execute(
                "SELECT COUNT(*) n FROM discovery_secondary_queue WHERE status='OPEN' AND research_value='HIGH'"
            ).fetchone()["n"])
            sentinel_complete = bool(coach_state.get("sentinel_complete"))
            systematic_fn = bool(coach_state.get("systematic_false_negative_risk"))
            near_miss_audit_complete = bool(post.get("near_miss_audit_complete")) and sentinel_complete and not systematic_fn
            minimum_coverage = signal_coverage >= coach.V8_MAIN_MIN_UNIQUE

            stop_allowed = (
                scanner_complete
                and minimum_coverage
                and sentinel_complete
                and near_miss_audit_complete
                and not systematic_fn
                and open_high == 0
                and not unresolved_high
                and low_tail
                and source_exhausted
            )
            audit = {
                **universe,
                "scanner_signal_coverage": signal_coverage,
                "mandatory_scanners": sorted(SCANNER_REQUIRED_DIMENSIONS),
                "scanner_execution_complete": scanner_complete,
                "scanner_completed": sorted(completed),
                "minimum_signal_coverage_met": minimum_coverage,
                "system_search_rounds": system_rounds,
                "two_consecutive_complete_low_yield_rounds": low_tail,
                "open_high_research_value_secondary": open_high,
                "unresolved_high_value_near_miss": sorted(unresolved_high)[:300],
                "near_miss_audit_complete": near_miss_audit_complete,
                "rejection_sentinel_complete": sentinel_complete,
                "systematic_false_negative_risk": systematic_fn,
                "source_or_budget_exhaustion_documented": source_exhausted,
                "deep_dive_yield_zero_alone_proves_exhaustion": False,
                "search_stop_allowed": stop_allowed,
                "reason": "MAIN_V8_FORENSIC_STOP_COMPLETE" if stop_allowed else "MAIN_V8_SEARCH_DEBT_REMAINS",
                "grade_authority": False,
                "version": V8_MAIN_DISCOVERY_POST_VERSION,
            }
            self.store.record_funnel(run_id, "V8_MAIN_FORENSIC_SEARCH_STOP_AUDIT", signal_coverage, audit)

            current_outcome = str(getattr(outcome, "outcome", "") or "")
            if not stop_allowed and current_outcome in {
                "NO_QUALIFIED_CANDIDATE", "NO_TRADE", "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT",
                "NOT_EVALUABLE_DISCOVERY_COVERAGE", "QUALIFIED_CANDIDATE_POOL",
            }:
                terminal = "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT"
                reason = (
                    f"scanner_complete={scanner_complete}; signal_coverage={signal_coverage}; sentinel={sentinel_complete}; "
                    f"open_high_secondary={open_high}; unresolved_high_near_miss={len(unresolved_high)}; "
                    f"low_tail={low_tail}; source_exhausted={source_exhausted}"
                )
                with self.store.transaction() as db:
                    db.execute("UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?", (terminal, run_id))
                return replace(outcome, outcome=terminal, blocked_reason=reason)
            return outcome

    runtime_module.ProductionStockAgent = V8MainDiscoveryPostV11ProductionStockAgent
    _INSTALLED = True
    return V8MainDiscoveryPostV11ProductionStockAgent
