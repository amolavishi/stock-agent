"""V1.8.2 selected-candidate allocation isolation.

Candidate-level research isolation is incomplete if an unrelated candidate's
provider/engineering failure can veto the independently qualified candidate's
Final Allocation.  This patch keeps failure attribution subject-scoped:

* the failed candidate remains NOT_EVALUATED / FAILED;
* the Daily/Shadow conclusion can still report a degraded universe;
* a different candidate that independently owns complete A/A- certification
  and all execution receipts is not vetoed merely because its peer failed.

No gate, grade, sizing, broker-write, or human-final-decision rule is relaxed.
"""
from __future__ import annotations

from typing import Any

from . import runtime as runtime_module
from . import store as store_module


ALLOCATION_GUARD_VERSION = "V8_HUNT_INTEGRITY_V1.8.2"
_FAILURE_STAGES = (
    "CANDIDATE_ENGINEERING_FAILURE",
    "RESEARCH_PROVIDER_FAILURE",
    "SEC_PROVIDER_FAILURE",
    "SEC_STALE_DATA",
)


def _selected_candidate_has_unresolved_failure(store: Any, run_id: str, subject_id: str) -> bool:
    if not str(subject_id or "").strip():
        return True
    placeholders = ",".join("?" for _ in _FAILURE_STAGES)
    row = store.connection.execute(
        f"SELECT 1 FROM stage_results WHERE run_id=? AND subject_id=? "
        f"AND stage IN ({placeholders}) LIMIT 1",
        (run_id, subject_id, *_FAILURE_STAGES),
    ).fetchone()
    return row is not None


def install_hunt_integrity_v182() -> None:
    if getattr(store_module, "_hunt_integrity_v182_installed", False):
        return
    base_commit = getattr(store_module, "_pre_v18_commit_final_allocation", None)
    if base_commit is None:
        raise RuntimeError("V1.8.2 requires bootstrap to preserve the pre-V1.8 Final Allocation writer")

    def commit_final_allocation_v182(
        self: Any,
        run: Any,
        action: str,
        allocation: dict[str, Any],
        positive_commitments: int | None = None,
    ) -> str:
        action_text = str(action)
        shares = int(allocation.get("shares", 0) or 0)
        capital_pct = float(allocation.get("capital_pct", 0) or 0)
        positive = action_text in {"STARTER", "ADD", "FULL"} and shares > 0 and capital_pct > 0
        subject_id = str(allocation.get("security_id") or "").strip()
        if positive and _selected_candidate_has_unresolved_failure(self, run.run_id, subject_id):
            raise ValueError("positive allocation blocked: selected candidate has an unresolved evaluation failure")
        # The preserved writer is the exact production writer immediately
        # before V1.8 added its over-broad run-level failure veto.  All
        # canonical qualification, lineage, freshness, issue, cardinality and
        # allocation checks therefore remain intact.
        return base_commit(self, run, action, allocation, positive_commitments)

    store_module.SQLiteStore.commit_final_allocation = commit_final_allocation_v182
    runtime_module.ProductionStockAgent.ALLOCATION_GUARD_VERSION = ALLOCATION_GUARD_VERSION
    store_module._hunt_integrity_v182_installed = True
