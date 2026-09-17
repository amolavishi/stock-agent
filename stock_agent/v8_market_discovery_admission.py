"""V8 Market Context discovery admission without weakening execution.

Investment Rules v2.0 explicitly separate Market Context from Market Execution:
non-core PARTIAL context may continue Discovery, while missing/conflicted core
market data must block aggressive execution. Legacy MAIN used the strict full
MarketContextGate as a pre-discovery kill switch.

This layer keeps the canonical strict receipt unchanged for audit. It only
changes the in-memory research-admission decision when a conservative broad
market core is fresh/valid. MarketExecutionGate is untouched.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from . import gates as gates_module
from . import runtime as runtime_module
from .models import GateDecision

V8_MARKET_DISCOVERY_ADMISSION_VERSION = "V8_MARKET_DISCOVERY_ADMISSION_V1.1"
_CORE_DISCOVERY_ASSETS = ("SPY", "QQQ", "IWM", "VIX")
_INSTALLED = False


def _core_discovery_context_complete(context: dict[str, Any], rules: Any, evaluation_time: datetime | None = None) -> bool:
    assets = context.get("assets") if isinstance(context.get("assets"), dict) else {}
    gate = gates_module.MarketContextGate()
    latest_session = gates_module._latest_completed_us_session_date(evaluation_time)
    for symbol in _CORE_DISCOVERY_ASSETS:
        receipt = assets.get(symbol)
        if not isinstance(receipt, dict):
            return False
        observed_at = receipt.get("observed_at")
        if not observed_at or not receipt.get("source") or int(receipt.get("observation_count") or 0) < 2:
            return False
        if not gate._validate_live_asset(symbol, receipt):
            return False
        group = str(receipt.get("sync_group") or "legacy")
        max_age, _ = gate._group_policy(rules, group)
        try:
            if gates_module.age_seconds(str(observed_at), now=evaluation_time, max_future_skew_seconds=rules.max_future_skew_seconds) > max_age:
                return False
            observed = gates_module._parse_timestamp(str(observed_at))
        except Exception:
            return False
        if group == "exchange" and observed.date() < latest_session:
            return False
    labels = all(context.get(key) not in (None, "", "UNKNOWN") for key in ("regime", "breadth", "volatility"))
    normalized = str(context.get("normalization_status") or "").upper() in {"COMPLETE", "PARTIAL"}
    return labels and normalized


class _DiscoveryAdmissionReceipt:
    """Admission view over an immutable canonical GateReceipt.

    Only ``decision`` is widened for research admission. Every other receipt
    field (receipt_hash, core_input_complete, input_hash, etc.) delegates to
    the canonical strict receipt so persistence/audit code cannot observe a
    fabricated PASS receipt.
    """

    def __init__(self, canonical: Any, admitted: bool) -> None:
        self.canonical = canonical
        self.admitted = bool(admitted)

    @property
    def decision(self):
        return GateDecision.PASS if self.admitted else self.canonical.decision

    def __getattr__(self, name: str) -> Any:
        return getattr(self.canonical, name)

    def as_dict(self):
        # Do not forge the canonical gate decision. Models and persisted
        # receipts see the original strict receipt; admission is observability
        # metadata recorded separately by the agent.
        return self.canonical.as_dict()


class _MarketContextDiscoveryAdmissionGate:
    def __init__(self, strict_gate: Any, state_setter: Callable[[bool, str], None]) -> None:
        self.strict_gate = strict_gate
        self.state_setter = state_setter

    def evaluate(self, context: dict[str, Any], rules: Any, *, evaluation_time: datetime | None = None):
        canonical = self.strict_gate.evaluate(context, rules, evaluation_time=evaluation_time)
        admitted = canonical.decision == GateDecision.PASS
        reason = "CANONICAL_PASS"
        if not admitted and _core_discovery_context_complete(context, rules, evaluation_time):
            admitted = True
            reason = "PARTIAL_CONTEXT_CORE_DISCOVERY_VALID"
        elif not admitted:
            reason = "CORE_DISCOVERY_CONTEXT_INSUFFICIENT"
        self.state_setter(admitted, reason)
        return _DiscoveryAdmissionReceipt(canonical, admitted)


class _SectorDiscoveryAdmissionGate:
    def __init__(self, strict_gate: Any, market_admitted: Callable[[], bool], state_setter: Callable[[bool, str], None]) -> None:
        self.strict_gate = strict_gate
        self.market_admitted = market_admitted
        self.state_setter = state_setter

    def evaluate(self, sector: dict[str, Any], rules: Any):
        canonical = self.strict_gate.evaluate(sector, rules)
        admitted = canonical.decision == GateDecision.PASS
        reason = "CANONICAL_PASS"
        if not admitted and self.market_admitted():
            admitted = True
            reason = "BOTTOM_UP_DISCOVERY_CONTINUES_WITH_PARTIAL_SECTOR_CONTEXT"
        elif not admitted:
            reason = "SECTOR_CONTEXT_INSUFFICIENT_AND_MARKET_CORE_NOT_ADMITTED"
        self.state_setter(admitted, reason)
        return _DiscoveryAdmissionReceipt(canonical, admitted)


def install_v8_market_discovery_admission() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_market_discovery_admission_version", None) == V8_MARKET_DISCOVERY_ADMISSION_VERSION:
        return current

    class V8MarketDiscoveryAdmissionProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_market_discovery_admission_version = V8_MARKET_DISCOVERY_ADMISSION_VERSION

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._v8_market_discovery_admitted = False
            self._v8_market_discovery_reason = "NOT_EVALUATED"
            self._v8_sector_discovery_admitted = False
            self._v8_sector_discovery_reason = "NOT_EVALUATED"
            strict_market = self.market_context_gate
            strict_sector = self.sector_gate
            self.market_context_gate = _MarketContextDiscoveryAdmissionGate(strict_market, self._set_market_discovery_state)
            self.sector_gate = _SectorDiscoveryAdmissionGate(strict_sector, lambda: self._v8_market_discovery_admitted, self._set_sector_discovery_state)

        def _set_market_discovery_state(self, admitted: bool, reason: str) -> None:
            self._v8_market_discovery_admitted = bool(admitted)
            self._v8_market_discovery_reason = str(reason)

        def _set_sector_discovery_state(self, admitted: bool, reason: str) -> None:
            self._v8_sector_discovery_admitted = bool(admitted)
            self._v8_sector_discovery_reason = str(reason)

        def _run_strict(self, mode, data):
            self._v8_market_discovery_admitted = False
            self._v8_sector_discovery_admitted = False
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if run_id and run_id != "unstarted":
                self.store.record_funnel(run_id, "V8_MARKET_CONTEXT_DISCOVERY_ADMISSION", int(self._v8_market_discovery_admitted), {
                    "admitted": self._v8_market_discovery_admitted,
                    "reason": self._v8_market_discovery_reason,
                    "market_execution_gate_relaxed": False,
                    "version": V8_MARKET_DISCOVERY_ADMISSION_VERSION,
                })
                self.store.record_funnel(run_id, "V8_SECTOR_CONTEXT_DISCOVERY_ADMISSION", int(self._v8_sector_discovery_admitted), {
                    "admitted": self._v8_sector_discovery_admitted,
                    "reason": self._v8_sector_discovery_reason,
                    "bottom_up_discovery_preserved": self._v8_sector_discovery_admitted,
                    "version": V8_MARKET_DISCOVERY_ADMISSION_VERSION,
                })
            return outcome

    runtime_module.ProductionStockAgent = V8MarketDiscoveryAdmissionProductionStockAgent
    _INSTALLED = True
    return V8MarketDiscoveryAdmissionProductionStockAgent
