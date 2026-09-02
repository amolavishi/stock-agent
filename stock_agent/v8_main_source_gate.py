"""Run-level source integrity gate for MAIN V8 discovery.

A scanner is not executable unless all canonical 02..14 source bytes are
available and match SOURCE_MANIFEST.json.  This guard is provider-independent,
so Fake/Recorded providers cannot make a missing source look executed.
"""
from __future__ import annotations

from typing import Any

from . import runtime as runtime_module
from .runtime import ContractViolation
from .v8_main_source_fidelity import V8_MAIN_SOURCE_FIDELITY_VERSION, source_bundle_status

V8_MAIN_SOURCE_GATE_VERSION = "V8_MAIN_SOURCE_GATE_V1.0"
_INSTALLED = False


def install_v8_main_source_gate() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_main_source_gate_version", None) == V8_MAIN_SOURCE_GATE_VERSION:
        return current

    class V8MainSourceGateProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_main_source_gate_version = V8_MAIN_SOURCE_GATE_VERSION
        v8_main_source_fidelity_version = V8_MAIN_SOURCE_FIDELITY_VERSION

        def _work_stage(self, run, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
            if stage == "STOCK_DISCOVERY" and prompt_id == "workflow.stock_scout":
                status = source_bundle_status()
                self.store.record_funnel(run.run_id, "V8_SOURCE_INTEGRITY", int(status["pass_count"]), {
                    "version": status["version"],
                    "complete": status["complete"],
                    "scanner_count": status["scanner_count"],
                    "pass_count": status["pass_count"],
                    "rows": status["rows"],
                    "scanner_executed": False if not status["complete"] else None,
                    "grade_authority": False,
                })
                if not status["complete"]:
                    failed = [f"{row['scanner_id']}:{row['status']}" for row in status["rows"] if row["status"] != "PASS"]
                    raise ContractViolation("V8_SOURCE_INTEGRITY:" + ",".join(failed))
            return super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)

        @staticmethod
        def _terminal_block_for_exception(exc: Exception) -> tuple[str, str]:
            message = str(exc)
            if message.startswith("V8_SOURCE_INTEGRITY:") or "V8 source integrity failure" in message:
                return "NOT_EVALUABLE_INPUT_INTEGRITY", message
            return current._terminal_block_for_exception(exc)

    runtime_module.ProductionStockAgent = V8MainSourceGateProductionStockAgent
    _INSTALLED = True
    return V8MainSourceGateProductionStockAgent
