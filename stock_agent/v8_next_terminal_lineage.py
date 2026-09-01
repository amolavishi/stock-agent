"""Preserve pre-V8-NEXT HUNT terminal state across coverage post-processing.

The V8 NEXT breadth floor is a research-coverage guard, not an error classifier.
If a provider/contract/pipeline failure occurs before breadth can be measured,
that upstream failure must remain the authoritative terminal reason.  Coverage
may add telemetry, but it must not overwrite root cause.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from . import runtime as runtime_module
from .models import RunMode, RunOutcome

CAPTURE_STAGE = "V8_NEXT_UPSTREAM_TERMINAL"
CAPTURE_VERSION = "V8_NEXT_TERMINAL_LINEAGE_V1.0"
_CAPTURE_INSTALLED = False
_RESTORE_INSTALLED = False


def _is_upstream_failure(outcome: str) -> bool:
    value = str(outcome or "")
    return value.startswith("BLOCKED") or value.startswith("NOT_EVALUABLE_")


def _latest_capture(store: Any, run_id: str) -> dict[str, Any] | None:
    rows = [
        row for row in store.list_funnel(run_id)
        if str(row.get("funnel_stage") or "") == CAPTURE_STAGE
    ]
    if not rows:
        return None
    try:
        value = json.loads(rows[-1].get("details_json") or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def install_pre_successor_terminal_capture() -> type:
    """Install immediately before V8 NEXT successor coverage logic."""
    global _CAPTURE_INSTALLED
    current = runtime_module.ProductionStockAgent
    if _CAPTURE_INSTALLED or getattr(current, "v8_next_terminal_capture_version", None) == CAPTURE_VERSION:
        return current

    class V8NextTerminalCaptureProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_next_terminal_capture_version = CAPTURE_VERSION

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if run_id and run_id != "unstarted":
                self.store.record_funnel(run_id, CAPTURE_STAGE, 1, {
                    "version": CAPTURE_VERSION,
                    "outcome": str(getattr(outcome, "outcome", "") or ""),
                    "blocked_reason": str(getattr(outcome, "blocked_reason", "") or ""),
                    "upstream_failure": _is_upstream_failure(str(getattr(outcome, "outcome", "") or "")),
                })
            return outcome

    runtime_module.ProductionStockAgent = V8NextTerminalCaptureProductionStockAgent
    _CAPTURE_INSTALLED = True
    return V8NextTerminalCaptureProductionStockAgent


def install_post_successor_terminal_restore() -> type:
    """Install after V8 NEXT runtime wiring and restore upstream root cause."""
    global _RESTORE_INSTALLED
    current = runtime_module.ProductionStockAgent
    if _RESTORE_INSTALLED or getattr(current, "v8_next_terminal_restore_version", None) == CAPTURE_VERSION:
        return current

    class V8NextTerminalRestoreProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_next_terminal_restore_version = CAPTURE_VERSION

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            captured = _latest_capture(self.store, run_id)
            if not isinstance(captured, dict) or not bool(captured.get("upstream_failure")):
                return outcome
            upstream_outcome = str(captured.get("outcome") or "")
            upstream_reason = str(captured.get("blocked_reason") or upstream_outcome)
            if not _is_upstream_failure(upstream_outcome):
                return outcome
            # Coverage remains recorded in discovery_funnel for audit, but the
            # authoritative terminal root cause returns to the actual upstream
            # failure instead of being mislabeled as low discovery recall.
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?",
                    (upstream_outcome, run_id),
                )
            return replace(
                outcome,
                outcome=upstream_outcome,
                blocked_reason=upstream_reason,
            )

    runtime_module.ProductionStockAgent = V8NextTerminalRestoreProductionStockAgent
    _RESTORE_INSTALLED = True
    return V8NextTerminalRestoreProductionStockAgent
