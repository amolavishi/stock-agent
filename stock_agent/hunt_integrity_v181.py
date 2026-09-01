"""V8 HUNT V1.8.1 compatibility layer.

V1.8 correctly introduced candidate fault receipts, Step-18 qualification
requirements, conservation telemetry and bounded evidence selection.  It also
changed the low-level RunOutcome for every incomplete candidate, which broke
stable library contracts and obscured the intended authority boundary.

V1.8.1 keeps the low-level HUNT result contract unchanged while preserving the
new candidate receipts.  Non-evaluable semantics remain authoritative in the
Shadow/investment conclusion, where provider/engineering/data incompleteness
must never render as clean NO_TRADE.
"""
from __future__ import annotations

from typing import Any

from . import gates as gates_module
from . import hunt_integrity_v18 as v18
from . import runtime as runtime_module
from .models import RunMode, RunOutcome


HUNT_INTEGRITY_PATCH_VERSION = "V8_HUNT_INTEGRITY_V1.8.1"


def install_hunt_integrity_v181() -> None:
    if getattr(runtime_module, "_hunt_integrity_v181_installed", False):
        return

    v18_production = runtime_module.ProductionStockAgent
    pre_v18_production = v18_production.__mro__[1]
    current_risk_assess = gates_module.RiskEngine.assess

    class V181ProductionStockAgent(v18_production):
        HUNT_INTEGRITY_PATCH_VERSION = HUNT_INTEGRITY_PATCH_VERSION

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            # Call the pre-V1.8 orchestration directly.  Dynamic dispatch still
            # reaches V1.8's candidate-scoped _work_stage, SEC failure receipt,
            # Step-18 qualification guard and other integrity hooks.
            self._v18_candidate_failures = {}
            token = v18._ACTIVE_AGENT.set(self)
            try:
                outcome = pre_v18_production._run_strict(self, mode, data)
            finally:
                v18._ACTIVE_AGENT.reset(token)
            if outcome.run_id not in {"", "unstarted"}:
                self._candidate_conservation(outcome.run_id)
            return outcome

    def risk_assess_v181(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = dict(current_risk_assess(self, *args, **kwargs))
        # Preserve the long-standing contract label while carrying an explicit
        # version field for cohort/replay comparisons.
        result["arithmetic_source"] = "PYTHON_RISK_ENGINE"
        result["risk_engine_version"] = HUNT_INTEGRITY_PATCH_VERSION
        return result

    runtime_module.ProductionStockAgent = V181ProductionStockAgent
    gates_module.RiskEngine.assess = risk_assess_v181
    runtime_module._hunt_integrity_v181_installed = True
