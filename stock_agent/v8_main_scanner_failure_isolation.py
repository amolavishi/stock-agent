"""Isolate V8 MAIN scanner-round engineering failures without hiding them.

This layer sits below the round aggregator and above the base runtime. A single
provider/schema/transport failure is converted into an explicit DATA_BLOCKED
round payload so the remaining rounds and scanners can still execute. The run
cannot cleanly stop or certify from that payload; downstream receipt validation
keeps the failed scanner incomplete.

No candidate, Research Grade, PRE-A status, execution action, position size, or
broker authority is created here.
"""
from __future__ import annotations

import re
from typing import Any

from . import runtime as runtime_module
from . import v8_main_discovery_coach as coach
from . import v8_main_discovery_integrity as integrity
from .providers import ProviderRequestError
from .runtime import ContractViolation

V8_MAIN_SCANNER_FAILURE_ISOLATION_VERSION = "V8_MAIN_SCANNER_FAILURE_ISOLATION_V1.0"
_INSTALLED = False
_ROUND_RE = re.compile(r"^V8_MAIN_SCANNER_(\d{2})_R(\d{3})$")


def _security_id(row: dict[str, Any]) -> str:
    return str(row.get("security_id") or row.get("ticker") or "").upper().strip()


def _isolated_round_payload(scanner_id: str, raw_input: dict[str, Any], exc: Exception) -> dict[str, Any]:
    universe = [row for row in (raw_input.get("candidate_universe_packet") or []) if isinstance(row, dict)]
    coverage = []
    for row in universe:
        sid = _security_id(row)
        if not sid:
            continue
        coverage.append({
            "security_id": sid,
            "disposition": "DATA_BLOCK",
            "failure_class": "DATA_INTEGRITY_BLOCK",
            "signal_strength": "UNKNOWN",
            "research_value": "UNKNOWN",
            "cheap_hard_gate_status": "UNKNOWN",
            "evidence_ids": [],
            "rationale": f"scanner engineering failure; security not evaluated ({type(exc).__name__})",
        })
    return {
        "scanner_id": scanner_id,
        "scanner_source_sha256": coach.V8_SCANNERS[scanner_id]["sha256"],
        "execution_status": "DATA_BLOCKED",
        # Zero is deliberate: input coverage rows describe non-evaluation and
        # must never satisfy screened_count == expected_count.
        "screened_count": 0,
        "candidates": [],
        "coverage_ledger": coverage,
        "systemic_unknowns": [f"ENGINEERING_FAILURE:{type(exc).__name__}"],
        "search_expansion_questions": ["retry failed scanner round with the same PIT input after engineering recovery"],
        "grade_authority": False,
        "output_contract_version": integrity.SCANNER_OUTPUT_CONTRACT_VERSION,
        "strategy_contract": {
            "scanner_id": scanner_id,
            "dimensions_evaluated": list(integrity.SCANNER_REQUIRED_DIMENSIONS[scanner_id]),
            "methodology_summary": "engineering failure isolated; no scanner assessment claimed",
        },
        "source_exhaustion": False,
        "source_exhaustion_reason": "ENGINEERING_FAILURE_NOT_SOURCE_EXHAUSTION",
    }


def _isolatable(exc: Exception) -> bool:
    message = str(exc)
    if "V8_SOURCE_INTEGRITY" in message or "V8 source integrity failure" in message:
        return False
    return isinstance(exc, (ProviderRequestError, ContractViolation, RuntimeError, TimeoutError))


def install_v8_main_scanner_failure_isolation() -> type:
    """Install before ``install_pre_coach_discovery_integrity``."""
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_main_scanner_failure_isolation_version", None) == V8_MAIN_SCANNER_FAILURE_ISOLATION_VERSION:
        return current

    class V8MainScannerFailureIsolationProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_main_scanner_failure_isolation_version = V8_MAIN_SCANNER_FAILURE_ISOLATION_VERSION

        def _work_stage(self, run: Any, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None):
            match = _ROUND_RE.match(str(stage))
            if not match:
                return super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            scanner_id = match.group(1)
            try:
                return super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            except Exception as exc:
                if not _isolatable(exc):
                    raise
                raw_input = payload.get("raw_input") if isinstance(payload, dict) else {}
                raw_input = raw_input if isinstance(raw_input, dict) else {}
                value = _isolated_round_payload(scanner_id, raw_input, exc)
                self.store.record_funnel(run.run_id, f"{stage}_ENGINEERING_FAILURE", len(value.get("coverage_ledger") or []), {
                    "scanner_id": scanner_id,
                    "round_id": raw_input.get("round_id"),
                    "error_type": type(exc).__name__,
                    "provider_status_code": getattr(exc, "status_code", None),
                    "isolated": True,
                    "investment_rejection": False,
                    "source_exhaustion": False,
                    "scanner_executed": False,
                    "grade_authority": False,
                    "version": V8_MAIN_SCANNER_FAILURE_ISOLATION_VERSION,
                })
                return value

    runtime_module.ProductionStockAgent = V8MainScannerFailureIsolationProductionStockAgent
    _INSTALLED = True
    return V8MainScannerFailureIsolationProductionStockAgent
