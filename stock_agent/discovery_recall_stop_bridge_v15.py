"""Bridge legacy V8 NEXT breadth guard to the forensic Discovery stop audit.

The old successor guard used one aggregate 150-name coverage number. It may
record telemetry, but after Discovery Recall V1.5 it no longer owns the final
search-stop decision. A no-candidate outcome is evaluable only when the
scanner receipts, rejection sentinel, Secondary/Near-Miss debt and
marginal-yield audit say search may stop.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from . import runtime as runtime_module
from .models import RunMode, RunOutcome

DISCOVERY_RECALL_STOP_BRIDGE_VERSION = "V8_DISCOVERY_RECALL_STOP_BRIDGE_V1.5"
_INSTALLED = False


def _latest_funnel_payload(store: Any, run_id: str, stage: str) -> dict[str, Any] | None:
    for row in reversed(store.list_funnel(run_id)):
        if str(row.get("funnel_stage") or "") != stage:
            continue
        try:
            value = json.loads(row.get("details_json") or "{}")
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    return None


def _captured_upstream(store: Any, run_id: str) -> dict[str, Any] | None:
    return _latest_funnel_payload(store, run_id, "V8_NEXT_UPSTREAM_TERMINAL")


def _write_terminal(store: Any, run_id: str, outcome: str) -> None:
    failed = outcome.startswith(("BLOCKED", "NOT_EVALUABLE"))
    with store.transaction() as db:
        db.execute("UPDATE runs SET status=?, outcome=? WHERE run_id=?", ("FAILED" if failed else "SUCCEEDED", outcome, run_id))


def _high_value_near_miss_count(agent: Any, run_id: str) -> int:
    """Count unresolved high-information candidates outside full research.

    Near-Miss is not Grade/PRE-A authority, but it is search debt. A zero
    deep-dive result cannot establish exhaustion while an information-rich
    nonfatal candidate remains parked here.
    """
    state = getattr(agent, "_discovery_recall_state", {}).get(run_id)
    if not isinstance(state, dict):
        return 0
    count = 0
    for item in state.get("near_miss") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("research_value") or "").upper() != "HIGH":
            continue
        if bool(item.get("fatal_fail")) or item.get("research_route_allowed") is False:
            # Structural/Thesis hard fails are retained for audit only and are
            # not open research debt under forensic TEST 10.
            continue
        count += 1
    return count


def install_discovery_recall_stop_bridge_v15() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "discovery_recall_stop_bridge_version", None) == DISCOVERY_RECALL_STOP_BRIDGE_VERSION:
        return current

    class DiscoveryRecallStopBridgeProductionStockAgent(current):  # type: ignore[misc,valid-type]
        discovery_recall_stop_bridge_version = DISCOVERY_RECALL_STOP_BRIDGE_VERSION

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            stop = _latest_funnel_payload(self.store, run_id, "DISCOVERY_SEARCH_STOP_AUDIT")
            coverage = _latest_funnel_payload(self.store, run_id, "DISCOVERY_SIGNAL_COVERAGE")
            if not isinstance(stop, dict) or not isinstance(coverage, dict):
                return outcome
            current_outcome = str(getattr(outcome, "outcome", "") or "")
            if current_outcome not in {"NO_QUALIFIED_CANDIDATE", "NOT_EVALUABLE_DISCOVERY_COVERAGE"}:
                return outcome

            high_near = _high_value_near_miss_count(self, run_id)
            if high_near > 0:
                terminal = "NOT_EVALUABLE_SEARCH_DEBT_OPEN"
                reason = (
                    "forensic Discovery search-stop forbidden while HIGH research-value Near-Miss debt remains; "
                    f"open_high_near_miss={high_near}; deep-dive yield alone cannot prove exhaustion"
                )
                self.store.record_funnel(run_id, "DISCOVERY_SEARCH_STOP_NEAR_MISS_GUARD", high_near, {
                    "search_stop_allowed": False,
                    "open_high_research_value_near_miss": high_near,
                    "deep_dive_yield_zero_alone_proves_exhaustion": False,
                    "grade_authority": False,
                })
                _write_terminal(self.store, run_id, terminal)
                return replace(outcome, outcome=terminal, blocked_reason=reason)

            if stop.get("search_stop_allowed") is not True:
                terminal = "NOT_EVALUABLE_SEARCH_DEBT_OPEN"
                reason = (
                    "forensic Discovery search-stop conditions are not satisfied; "
                    f"reason={stop.get('reason')}; open_high_secondary={stop.get('open_high_research_value_secondary')}; "
                    f"open_high_near_miss={high_near}; adv_not_evaluated={stop.get('adv_not_evaluated')}"
                )
                _write_terminal(self.store, run_id, terminal)
                return replace(outcome, outcome=terminal, blocked_reason=reason)

            # New scanner execution has satisfied the research stop contract.
            # The old stage-ready-only coverage failure is now telemetry only.
            if current_outcome == "NOT_EVALUABLE_DISCOVERY_COVERAGE":
                captured = _captured_upstream(self.store, run_id)
                if isinstance(captured, dict):
                    upstream = str(captured.get("outcome") or "")
                    reason = str(captured.get("blocked_reason") or upstream)
                    if upstream:
                        _write_terminal(self.store, run_id, upstream)
                        return replace(outcome, outcome=upstream, blocked_reason=reason)
            return outcome

    runtime_module.ProductionStockAgent = DiscoveryRecallStopBridgeProductionStockAgent
    _INSTALLED = True
    return DiscoveryRecallStopBridgeProductionStockAgent
