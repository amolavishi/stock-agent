"""Shadow V1.4 fail-closed projection for non-evaluable HUNT outcomes.

Live validation exposed a critical orchestration mismatch: the authoritative
HUNT row could be FAILED/NOT_EVALUABLE while the Shadow wrapper still returned
SUCCEEDED and rendered a clean NO_TRADE.  This layer keeps the investment
conclusion aligned with the authoritative HUNT state without changing any
investment gate or execution authority.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from . import shadow as shadow_module
from .models import RunMode

SHADOW_NON_EVALUABLE_GUARD_VERSION = "SHADOW_V1.4_NON_EVALUABLE_GUARD"
_INSTALLED = False


def _error_is_incomplete(error: Any) -> bool:
    if not isinstance(error, dict):
        return False
    classification = str(error.get("classification") or "").upper()
    component = str(error.get("component") or "").upper()
    return (
        classification in {"PRE_DISCOVERY_BLOCK", "NON_EVALUABLE_HUNT", "PIPELINE_FAILURE"}
        or component in {"HUNT", "MARKET", "RESEARCH", "SEC", "EVIDENCE", "ORCHESTRATOR"}
    )


def classify_hunt_conclusion(hunt_outcome: str, errors: list[Any] | None = None) -> tuple[str, bool]:
    """Return (investment_conclusion, is_clean_no_trade).

    A clean NO_TRADE is allowed only after an evaluable HUNT completes without
    pipeline/provider/pre-discovery errors.  Operational incompleteness is not
    an investment rejection and can never be relabeled NO_TRADE.
    """
    outcome = str(hunt_outcome or "UNKNOWN")
    values = list(errors or [])
    if outcome.startswith("NOT_EVALUABLE_"):
        return outcome, False
    if outcome.startswith("BLOCKED"):
        return "NOT_EVALUABLE_PIPELINE_FAILURE", False
    if any(_error_is_incomplete(item) for item in values):
        return "NOT_EVALUABLE_PIPELINE_FAILURE", False
    if outcome == "NO_QUALIFIED_CANDIDATE":
        return "NO_TRADE", True
    if outcome == "QUALIFIED_CANDIDATE_POOL":
        return "QUALIFIED_CANDIDATE_POOL", False
    return "NOT_EVALUABLE_PIPELINE_FAILURE", False


def _provider_implies_broad_live(provider: Any) -> bool:
    name = str(getattr(provider, "provider_name", "") or "").lower()
    return name.startswith("composite-live-market") or name.startswith("composite-live-market-alpha")


def install_shadow_non_evaluable_guard() -> type:
    global _INSTALLED
    current = shadow_module.DailyShadowRunner
    if _INSTALLED or getattr(current, "shadow_non_evaluable_guard_version", None) == SHADOW_NON_EVALUABLE_GUARD_VERSION:
        return current

    class ShadowNonEvaluableDailyRunner(current):  # type: ignore[misc,valid-type]
        shadow_non_evaluable_guard_version = SHADOW_NON_EVALUABLE_GUARD_VERSION

        def run(self, *args: Any, **kwargs: Any) -> Any:
            # Base Shadow historically recognizes BLOCKED_* as degraded but did
            # not recognize the newer NOT_EVALUABLE_* terminal vocabulary.
            # Adapt only the in-process return value seen by Shadow; the
            # authoritative runs table keeps the original NOT_EVALUABLE outcome.
            original_agent_run = self.agent.run

            def guarded_agent_run(mode: RunMode, data: dict[str, Any]) -> Any:
                outcome = original_agent_run(mode, data)
                terminal = str(getattr(outcome, "outcome", "") or "")
                if mode == RunMode.HUNT_ONLY and terminal.startswith("NOT_EVALUABLE_"):
                    reason = str(getattr(outcome, "blocked_reason", "") or terminal)
                    return replace(
                        outcome,
                        outcome=f"BLOCKED_{terminal}",
                        blocked_reason=f"{terminal}:{reason}",
                    )
                return outcome

            self.agent.run = guarded_agent_run
            try:
                return super().run(*args, **kwargs)
            finally:
                self.agent.run = original_agent_run

        def _run_log(
            self,
            shadow_run_id: str,
            hunt_run_id: str,
            execution_run_id: str | None,
            health: dict[str, Any],
            status: str,
        ) -> dict[str, Any]:
            log = super()._run_log(shadow_run_id, hunt_run_id, execution_run_id, health, status)
            hunt = self.store.get_run(hunt_run_id)
            errors = list(log.get("errors") or [])
            conclusion, clean = classify_hunt_conclusion(str(hunt.outcome or ""), errors)
            log["investment_conclusion"] = conclusion
            log["investment_conclusion_is_clean_no_trade"] = clean
            if not clean and conclusion.startswith("NOT_EVALUABLE_"):
                log["pipeline_health"] = "DEGRADED"
                if str(log.get("status") or "") == "SUCCEEDED":
                    log["status"] = "DEGRADED"
                providers = log.get("providers") if isinstance(log.get("providers"), dict) else {}
                gate = dict(providers.get("gate_integrity") or {})
                gate["status"] = "DEGRADED"
                providers["gate_integrity"] = gate
                log["providers"] = providers

            contract = log.get("hunt_contract") if isinstance(log.get("hunt_contract"), dict) else {}
            raw_present = int((log.get("universe") or {}).get("raw", 0) or 0) > 0
            coverage_terminal = str(hunt.outcome or "") == "NOT_EVALUABLE_DISCOVERY_COVERAGE"
            if not bool(contract.get("broad_discovery")) and (
                coverage_terminal
                or (raw_present and _provider_implies_broad_live(getattr(self.agent.config, "market_data_provider", None)))
            ):
                contract["broad_discovery"] = True
                log["hunt_contract"] = contract
            return log

    shadow_module.DailyShadowRunner = ShadowNonEvaluableDailyRunner
    _INSTALLED = True
    return ShadowNonEvaluableDailyRunner
