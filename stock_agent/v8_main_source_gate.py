"""Run-level exact-source integrity gate for MAIN V8.4 discovery.

A scanner is executable only when the packaged V8.4 common contract, canonical
universe rules and every 02..14 scanner profile match the raw-byte source lock.
This guard is provider-independent, so Fake/Recorded providers cannot convert a
missing/mismatched source into SCANNER_EXECUTED.
"""
from __future__ import annotations

from typing import Any

from . import runtime as runtime_module
from .runtime import ContractViolation
from .v8_main_source_fidelity import (
    V8_MAIN_SOURCE_FIDELITY_VERSION,
    V8_4_PACKAGE_VERSION,
    source_bundle_status,
)

V8_MAIN_SOURCE_GATE_VERSION = "V8_MAIN_SOURCE_GATE_V8_4_V1.1"
_INSTALLED = False


def install_v8_main_source_gate() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_main_source_gate_version", None) == V8_MAIN_SOURCE_GATE_VERSION:
        return current

    class V8MainSourceGateProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_main_source_gate_version = V8_MAIN_SOURCE_GATE_VERSION
        v8_main_source_fidelity_version = V8_MAIN_SOURCE_FIDELITY_VERSION
        v8_source_package_version = V8_4_PACKAGE_VERSION

        def _work_stage(self, run, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
            if stage == "STOCK_DISCOVERY" and prompt_id == "workflow.stock_scout":
                status = source_bundle_status()
                self.store.record_funnel(run.run_id, "V8_SOURCE_INTEGRITY", int(status["pass_count"]), {
                    "version": status["version"],
                    "manifest_version": status["manifest_version"],
                    "package_version": status["package_version"],
                    "canonical_package": status["canonical_package"],
                    "canonical_runtime_tree_hash": status["canonical_runtime_tree_hash"],
                    "complete": status["complete"],
                    "scanner_count": status["scanner_count"],
                    "pass_count": status["pass_count"],
                    "core_count": status["core_count"],
                    "core_pass_count": status["core_pass_count"],
                    "rows": status["rows"],
                    "core_rows": status["core_rows"],
                    "scanner_executed": False if not status["complete"] else None,
                    "grade_authority": False,
                })
                if not status["complete"]:
                    failed = [
                        f"{row.get('source_id')}:{row.get('status')}"
                        for row in status.get("all_rows") or []
                        if row.get("status") != "PASS"
                    ]
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
