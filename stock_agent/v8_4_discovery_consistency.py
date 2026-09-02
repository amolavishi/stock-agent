"""V8.4 Discovery semantic-consistency validator.

V8.4 source text is authoritative for scanner semantics while the legacy MAIN
runtime owns orchestration and final DiscoveryCandidateSetV2. This module
eliminates contract drift between those layers without becoming a new runtime
owner and without weakening any grade, SEC, PRE-A, execution, sizing, or broker
gate.

The final V8PreLiveSentinelProductionStockAgent remains the outermost runtime
class. This module patches that composed class in place after all legacy/v20
schema extensions are installed.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Any

from . import runtime as runtime_module
from . import v8_main_discovery_coach as coach
from . import v8_main_discovery_integrity as integrity
from . import v8_main_source_fidelity as source_fidelity
from .models import RunMode, RunOutcome

V8_4_DISCOVERY_CONSISTENCY_VERSION = "V8_4_DISCOVERY_CONSISTENCY_V1.1"
V8_4_SIGNAL_STRENGTH = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")
V8_4_SCANNER_ACTIONS = (
    "DEEP_DIVE_NOW",
    "DEEP_DIVE_SECONDARY",
    "WATCH_STAGE0",
    "WATCH_RESET",
    "EARLY_TRAJECTORY",
    "EXCLUDE",
)
_INSTALLED = False
_BASE_SCHEMA = None
_BASE_ROUND_METRICS = None


def _patch_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(schema)
    candidate = (((result.get("properties") or {}).get("candidates") or {}).get("items"))
    if isinstance(candidate, dict):
        props = candidate.get("properties") if isinstance(candidate.get("properties"), dict) else {}
        if isinstance(props.get("signal_strength"), dict):
            props["signal_strength"]["enum"] = list(V8_4_SIGNAL_STRENGTH)
        if isinstance(props.get("recommended_discovery_action"), dict):
            props["recommended_discovery_action"]["enum"] = list(V8_4_SCANNER_ACTIONS)
    ledger = (result.get("properties") or {}).get("coverage_ledger")
    ledger_item = ledger.get("items") if isinstance(ledger, dict) else None
    ledger_props = ledger_item.get("properties") if isinstance(ledger_item, dict) and isinstance(ledger_item.get("properties"), dict) else {}
    if isinstance(ledger_props.get("signal_strength"), dict):
        ledger_props["signal_strength"]["enum"] = list(V8_4_SIGNAL_STRENGTH)
    return result


def scanner_schema_v84() -> dict[str, Any]:
    if _BASE_SCHEMA is None:
        raise RuntimeError("V8.4 consistency schema base not installed")
    return _patch_schema(_BASE_SCHEMA())


def _round_metrics_v84(scanner_id: str, round_id: str, chunk: list[dict[str, Any]], result: dict[str, Any], cumulative: int, prior_evidence: set[str]):
    if _BASE_ROUND_METRICS is None:
        raise RuntimeError("V8.4 consistency metrics base not installed")
    metrics, evidence = _BASE_ROUND_METRICS(scanner_id, round_id, chunk, result, cumulative, prior_evidence)
    candidates = [item for item in (result.get("candidates") or []) if isinstance(item, dict)]
    signal_ids = sorted({
        str(item.get("security_id") or "").upper()
        for item in candidates
        if item.get("security_id") and str(item.get("signal_strength") or "") in {"HIGH", "MEDIUM", "LOW", "STRONG", "MODERATE", "WEAK"}
    })
    metrics["new_signal"] = len(signal_ids)
    metrics["new_signal_security_ids"] = signal_ids
    metrics["new_partial_signal"] = sum(
        1 for item in candidates
        if bool(item.get("partial_signal")) or str(item.get("signal_strength") or "") == "UNKNOWN"
    )
    return metrics, evidence


def _validated_full_scope_manifest(raw: dict[str, Any]) -> tuple[bool, list[str]]:
    manifest = raw.get("universe_manifest") if isinstance(raw, dict) else None
    manifest = manifest if isinstance(manifest, dict) else {}
    failures: list[str] = []
    required_true = (
        "authoritative_listing_source_coverage",
        "identity_reconciliation_complete",
        "security_type_classification_complete",
        "price_filter_reconciled",
        "market_cap_filter_reconciled",
        "mdv20_filter_reconciled",
        "eligibility_count_reconciled",
    )
    if str(manifest.get("scope_code") or "") != "FULL_STRATEGY_UNIVERSE_SCAN":
        failures.append("SCOPE_CODE")
    if str(manifest.get("validation_status") or "") != "PASS":
        failures.append("VALIDATION_STATUS")
    for key in required_true:
        if manifest.get(key) is not True:
            failures.append(key.upper())
    if int(manifest.get("material_unresolved_eligibility_count") or 0) != 0:
        failures.append("MATERIAL_UNRESOLVED_ELIGIBILITY")
    return not failures, failures


def _scanner_early_trajectory(store: Any, run_id: str) -> tuple[set[str], set[str]]:
    all_ids: set[str] = set()
    high_ids: set[str] = set()
    for row in store.list_stage_results(run_id):
        stage = str(row.get("stage") or "")
        if not stage.startswith("V8_MAIN_SCANNER_") or "_R" not in stage or row.get("status") != "SUCCEEDED":
            continue
        try:
            payload = json.loads(row.get("result_json") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for item in payload.get("candidates") or []:
            if not isinstance(item, dict) or item.get("recommended_discovery_action") != "EARLY_TRAJECTORY":
                continue
            sid = str(item.get("security_id") or "").upper().strip()
            if not sid:
                continue
            all_ids.add(sid)
            if item.get("research_value") == "HIGH":
                high_ids.add(sid)
    return all_ids, high_ids


def install_v8_4_discovery_consistency() -> type:
    global _INSTALLED, _BASE_SCHEMA, _BASE_ROUND_METRICS
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_4_discovery_consistency_version", None) == V8_4_DISCOVERY_CONSISTENCY_VERSION:
        return current

    # V8.4 source lock must have overwritten all legacy SHA compatibility
    # values before this validator can become active.
    source_fidelity.prepare_v8_4_source_lock()
    for scanner_id, entry in source_fidelity._scanner_entries().items():
        if coach.V8_SCANNERS[scanner_id]["sha256"] != entry["sha256"]:
            raise RuntimeError(f"V8.4 source identity drift after source lock: {scanner_id}")

    # Capture the fully hardened V2.0.1 schema/metrics after all legacy
    # compatibility patches, then change only V8.4 semantic mismatches.
    _BASE_SCHEMA = coach._scanner_schema
    _BASE_ROUND_METRICS = integrity._round_metrics
    coach._scanner_schema = scanner_schema_v84  # type: ignore[assignment]
    integrity._round_metrics = _round_metrics_v84  # type: ignore[assignment]
    integrity._ACTION_RANK["EARLY_TRAJECTORY"] = 3.5

    if "V8_4_SEMANTIC_COMPATIBILITY" not in source_fidelity._RUNTIME_ADDENDUM:
        source_fidelity._RUNTIME_ADDENDUM += r"""

# V8_4_SEMANTIC_COMPATIBILITY
Use signal_strength only as HIGH|MEDIUM|LOW|UNKNOWN. Do not emit the obsolete
STRONG|MODERATE|WEAK|NONE vocabulary. Scanner-level recommended_discovery_action
may be EARLY_TRAJECTORY. The legacy final workflow.stock_scout schema may map a
scanner EARLY_TRAJECTORY to WATCH_STAGE0 only at the final aggregation boundary;
the original scanner state must remain visible in scanner receipts and may not
be silently converted to EXCLUDE. This compatibility mapping has zero grade,
PRE-A, execution, sizing, or broker authority.
"""

    base_work_stage = current._work_stage
    base_run_strict = current._run_strict

    def work_stage_v84(self: Any, run: Any, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None):
        result = base_work_stage(self, run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
        if stage != "STOCK_DISCOVERY" or prompt_id != "workflow.stock_scout" or not isinstance(result, dict):
            return result
        raw = payload.get("raw_input") if isinstance(payload, dict) else {}
        raw = raw if isinstance(raw, dict) else {}
        full_ok, failures = _validated_full_scope_manifest(raw)
        scope = "FULL_STRATEGY_UNIVERSE_SCAN" if full_ok else (
            "BOUNDED_STRATEGY_UNIVERSE_SCAN" if bool(raw.get("universe")) else "PARTIAL_STRATEGY_UNIVERSE_SCAN"
        )
        early_ids, high_early = _scanner_early_trajectory(self.store, run.run_id)
        final_ids = {
            str(item.get("security_id") or "").upper()
            for item in (result.get("candidates") or [])
            if isinstance(item, dict) and item.get("security_id")
        }
        unresolved_high = sorted(high_early - final_ids)
        state = {
            "scope_claim": scope,
            "full_scope_validated": full_ok,
            "full_scope_failures": failures,
            "early_trajectory_ids": sorted(early_ids)[:500],
            "high_early_trajectory_ids": sorted(high_early)[:500],
            "unresolved_high_early_trajectory_ids": unresolved_high[:500],
            "grade_authority": False,
            "version": V8_4_DISCOVERY_CONSISTENCY_VERSION,
        }
        states = getattr(self, "_v8_4_consistency_state", None)
        if not isinstance(states, dict):
            states = {}
            setattr(self, "_v8_4_consistency_state", states)
        states[run.run_id] = state
        self.store.record_funnel(run.run_id, "V8_4_UNIVERSE_SCOPE", len(raw.get("universe") or []), state)
        self.store.record_funnel(run.run_id, "V8_4_EARLY_TRAJECTORY_LEDGER", len(early_ids), {
            "security_ids": sorted(early_ids)[:500],
            "high_research_value_ids": sorted(high_early)[:500],
            "unresolved_high_research_value_ids": unresolved_high[:500],
            "legacy_final_schema_mapping": "WATCH_STAGE0_ONLY_AT_FINAL_AGGREGATION_BOUNDARY",
            "silent_exclude_forbidden": True,
            "grade_authority": False,
            "version": V8_4_DISCOVERY_CONSISTENCY_VERSION,
        })
        return result

    def run_strict_v84(self: Any, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
        outcome = base_run_strict(self, mode, data)
        run_id = str(getattr(outcome, "run_id", "") or "")
        state = getattr(self, "_v8_4_consistency_state", {}).get(run_id) if run_id else None
        if not isinstance(state, dict):
            return outcome
        unresolved = list(state.get("unresolved_high_early_trajectory_ids") or [])
        current_outcome = str(getattr(outcome, "outcome", "") or "")
        if unresolved and current_outcome in {
            "NO_QUALIFIED_CANDIDATE",
            "NO_TRADE",
            "QUALIFIED_CANDIDATE_POOL",
            "NOT_EVALUABLE_DISCOVERY_COVERAGE",
            "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT",
        }:
            terminal = "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT"
            reason = f"unresolved HIGH EARLY_TRAJECTORY research debt={len(unresolved)}"
            with self.store.transaction() as db:
                db.execute("UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?", (terminal, run_id))
            return replace(outcome, outcome=terminal, blocked_reason=reason)
        return outcome

    current._work_stage = work_stage_v84  # type: ignore[assignment]
    current._run_strict = run_strict_v84  # type: ignore[assignment]
    current.v8_4_discovery_consistency_version = V8_4_DISCOVERY_CONSISTENCY_VERSION
    _INSTALLED = True
    return current
