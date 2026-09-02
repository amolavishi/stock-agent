"""Scanner-specific JSON-schema contract for V8 MAIN 02..14.

The source prompt remains the strategy authority.  This module prevents a
model from satisfying every scanner with one generic response shape: for each
scanner_id the exact forensic dimensions must be present in both the top-level
strategy contract and every returned candidate's strategy-evidence ledger.
"""
from __future__ import annotations

from typing import Any

from . import v8_main_discovery_coach as coach
from .v8_main_discovery_integrity import (
    SCANNER_REQUIRED_DIMENSIONS,
    _integrity_scanner_schema,
)

V8_MAIN_SCANNER_CONTRACT_VERSION = "V8_MAIN_SCANNER_CONTRACT_V1.2"
_PREPARED = False


def scanner_schema_v12() -> dict[str, Any]:
    schema = _integrity_scanner_schema()
    conditional: list[dict[str, Any]] = []
    for scanner_id, dimensions in sorted(SCANNER_REQUIRED_DIMENSIONS.items()):
        dimension_requirements = [{"contains": {"const": dimension}} for dimension in dimensions]
        candidate_dimension_requirements = [
            {"contains": {"type": "object", "properties": {"dimension": {"const": dimension}}, "required": ["dimension"]}}
            for dimension in dimensions
        ]
        conditional.append({
            "if": {
                "properties": {"scanner_id": {"const": scanner_id}},
                "required": ["scanner_id"],
            },
            "then": {
                "properties": {
                    "strategy_contract": {
                        "properties": {
                            "dimensions_evaluated": {"allOf": dimension_requirements},
                        }
                    },
                    "candidates": {
                        "items": {
                            "properties": {
                                "strategy_evidence": {"allOf": candidate_dimension_requirements},
                            }
                        }
                    },
                }
            },
        })
    schema["allOf"] = list(schema.get("allOf") or []) + conditional
    return schema


def prepare_v8_main_scanner_contract_v12() -> None:
    global _PREPARED
    if _PREPARED:
        return
    coach._scanner_schema = scanner_schema_v12  # type: ignore[assignment]
    _PREPARED = True
