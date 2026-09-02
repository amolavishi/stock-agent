"""Compatibility patch for V8 pre-live integrity v2.0.

Preserves the scanner-specific V1.2 conditional schema while adding V1.3 full
coverage-ledger proof, removes a possible default-payload recursion, and chains
the conservative evidence-origin / Secondary-recheck / sentinel semantic
patches. This module adds no discovery grade, PRE-A, execution, or broker
authority.
"""
from __future__ import annotations

import copy
from typing import Any

from . import v8_main_discovery_coach as coach
from . import v8_main_discovery_integrity as integrity
from . import v8_main_scanner_contract_v12 as scanner_contract
from . import v8_pre_live_integrity_v20 as v20

V8_PRE_LIVE_INTEGRITY_PATCH_VERSION = "V8_PRE_LIVE_INTEGRITY_V2.0.1"
_INSTALLED = False


def scanner_schema_v201() -> dict[str, Any]:
    # V1.2 owns scanner-specific dimension conditions. Extend, never replace.
    schema = copy.deepcopy(scanner_contract.scanner_schema_v12())
    candidate = schema["properties"]["candidates"]["items"]
    candidate["properties"].update({
        "recheck_trigger_fired": {"type": "boolean"},
        "recheck_trigger_evidence_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
    })
    coverage_row = {
        "type": "object",
        "properties": {
            "security_id": {"type": "string", "minLength": 1},
            "disposition": {"type": "string", "enum": sorted(v20._COVERAGE_DISPOSITIONS)},
            "failure_class": {"type": "string", "enum": sorted(v20._FAILURE_CLASSES)},
            "signal_strength": {"type": "string", "enum": ["STRONG", "MODERATE", "WEAK", "NONE", "UNKNOWN"]},
            "research_value": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"]},
            "cheap_hard_gate_status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "evidence_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "rationale": {"type": "string"},
        },
        "required": [
            "security_id", "disposition", "failure_class", "signal_strength",
            "research_value", "cheap_hard_gate_status", "evidence_ids", "rationale",
        ],
        "additionalProperties": False,
    }
    schema["properties"]["coverage_ledger"] = {
        "type": "array",
        "items": coverage_row,
        "uniqueItems": True,
    }
    if "coverage_ledger" not in schema["required"]:
        schema["required"].append("coverage_ledger")
    schema["properties"]["output_contract_version"] = {"const": v20.SCANNER_OUTPUT_CONTRACT_VERSION}
    return schema


def default_scanner_v201(scanner_id: str, screened_count: int) -> dict[str, Any]:
    # Deliberately incomplete coverage for non-empty universes. A fallback
    # payload cannot masquerade as real scanner execution.
    return {
        "scanner_id": scanner_id,
        "scanner_source_sha256": coach.V8_SCANNERS[scanner_id]["sha256"],
        "execution_status": "COMPLETE",
        "screened_count": int(screened_count),
        "candidates": [],
        "coverage_ledger": [],
        "systemic_unknowns": [],
        "search_expansion_questions": [],
        "grade_authority": False,
        "output_contract_version": v20.SCANNER_OUTPUT_CONTRACT_VERSION,
        "strategy_contract": {
            "scanner_id": scanner_id,
            "dimensions_evaluated": list(integrity.SCANNER_REQUIRED_DIMENSIONS[scanner_id]),
            "methodology_summary": "fallback is non-authoritative; complete coverage ledger requires provider execution",
        },
        "source_exhaustion": False,
        "source_exhaustion_reason": "NOT_PROVEN",
    }


def install_v8_pre_live_integrity_v201() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    v20._scanner_schema_v13 = scanner_schema_v201  # type: ignore[assignment]
    v20._default_scanner_v13 = default_scanner_v201  # type: ignore[assignment]
    coach._scanner_schema = scanner_schema_v201  # type: ignore[assignment]
    integrity._integrity_default_scanner = default_scanner_v201  # type: ignore[assignment]
    from .v8_pre_live_integrity_v202 import install_v8_pre_live_integrity_v202
    from .v8_pre_live_integrity_v203 import install_v8_pre_live_integrity_v203
    from .v8_secondary_priority_recheck_v205 import install_v8_secondary_priority_recheck_v205
    from .v8_pre_live_integrity_v204 import install_v8_pre_live_integrity_v204
    install_v8_pre_live_integrity_v202()
    install_v8_pre_live_integrity_v203()
    # Persistent HIGH Secondary names are re-probed before the outer sentinel
    # validator is installed. The sentinel therefore remains the final runtime
    # wrapper and the recheck layer remains visible in its MRO.
    install_v8_secondary_priority_recheck_v205()
    install_v8_pre_live_integrity_v204()
    _INSTALLED = True
