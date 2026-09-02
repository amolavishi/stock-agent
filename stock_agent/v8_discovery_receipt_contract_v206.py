"""V8 Discovery receipt contract compatibility hardening.

The V8 market/sector Discovery-admission layer deliberately preserves the
canonical strict GateReceipt even when research admission is widened for a
PARTIAL-but-usable context.  The prompt-library schemas predate that policy and
only allow PASS-like decisions in the MarketContext/Sector receipt projections.
That makes a legitimate canonical ``INSUFFICIENT_EVIDENCE`` receipt impossible
to round-trip through Sector Analysis / Stock Scout.

This patch fixes only that contract mismatch:
- MarketContextGateReceipt and SectorGateReceipt may carry
  INSUFFICIENT_EVIDENCE as preserved upstream state.
- no other gate schema is widened; MarketExecution/Stage/Capital remain strict.
- authoritative ``*_receipt`` fields are rebound from typed Python context
  before schema validation for every model provider, not only Luna.

It creates no candidate, grade, PRE-A, execution, sizing, or broker authority.
"""
from __future__ import annotations

import copy
from typing import Any, Callable

from . import prompt_runtime as prompt_runtime_module


V8_DISCOVERY_RECEIPT_CONTRACT_VERSION = "V8_DISCOVERY_RECEIPT_CONTRACT_V2.0.6"
_PRESERVED_DISCOVERY_GATE_DEFS = ("MarketContextGateReceipt", "SectorGateReceipt")
_INSTALLED = False
_BASE_PROMPT_RUNTIME_INIT = prompt_runtime_module.PromptRuntime.__init__
_BASE_STRICT_CALL = prompt_runtime_module.PromptRuntime.strict_call


def patch_registry_for_preserved_discovery_receipts(registry: dict[str, Any]) -> None:
    """Allow only the canonical insufficiency state required by Discovery.

    A hard REJECT/SYSTEM_ERROR/MANUAL_REVIEW state is intentionally not added.
    This is a provenance compatibility fix, not a gate relaxation.
    """
    defs = registry.get("$defs") if isinstance(registry, dict) else None
    if not isinstance(defs, dict):
        return
    for name in _PRESERVED_DISCOVERY_GATE_DEFS:
        definition = defs.get(name)
        if not isinstance(definition, dict):
            continue
        properties = definition.get("properties")
        decision = properties.get("decision") if isinstance(properties, dict) else None
        enum = decision.get("enum") if isinstance(decision, dict) else None
        if isinstance(enum, list) and "INSUFFICIENT_EVIDENCE" not in enum:
            enum.append("INSUFFICIENT_EVIDENCE")


def _context_entries(context_manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for entry in (context_manifest or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("id") or "")
        content = entry.get("content")
        if key and isinstance(content, dict):
            result[key] = content
    return result


def _field_schema(schema: dict[str, Any], field: str) -> dict[str, Any] | None:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    value = properties.get(field) if isinstance(properties, dict) else None
    if not isinstance(value, dict):
        return None
    ref = value.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}
        resolved = defs.get(ref.rsplit("/", 1)[-1])
        return resolved if isinstance(resolved, dict) else value
    return value


def bind_authoritative_receipts(
    payload: Any,
    context_manifest: dict[str, Any] | None,
    schema: dict[str, Any],
) -> Any:
    """Replace model-authored receipt echoes with Python-bound upstream state."""
    if not isinstance(payload, dict):
        return payload
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return payload
    entries = _context_entries(context_manifest)
    result = copy.deepcopy(payload)
    for field in properties:
        if not str(field).endswith("_receipt"):
            continue
        typed = entries.get(str(field))
        if typed is None:
            stem = str(field)[: -len("_receipt")]
            typed = entries.get(stem) or entries.get(f"{stem}_results")
        if not isinstance(typed, dict):
            continue
        value = typed.get("value") if "gate_receipt" in str(field) else (typed.get("upstream_receipt") or typed.get("value"))
        if value is None:
            continue
        field_schema = _field_schema(schema, str(field))
        allowed = field_schema.get("properties") if isinstance(field_schema, dict) else None
        if isinstance(value, dict) and isinstance(allowed, dict):
            value = {key: value[key] for key in allowed if key in value}
        result[str(field)] = copy.deepcopy(value)
    return result


def _prompt_runtime_init_v206(self: Any, *args: Any, **kwargs: Any) -> None:
    _BASE_PROMPT_RUNTIME_INIT(self, *args, **kwargs)
    patch_registry_for_preserved_discovery_receipts(self.registry)


def _strict_call_v206(
    self: Any,
    root_prompt_id: str,
    model_call: Callable[[dict[str, Any]], Any],
    max_attempts: int = 2,
    context: dict[str, Any] | None = None,
    run_mode: str | None = None,
) -> Any:
    def authoritative_model_call(request: dict[str, Any]) -> Any:
        payload = model_call(request)
        schema = request.get("output_schema_definition") if isinstance(request, dict) else {}
        return bind_authoritative_receipts(payload, context, schema if isinstance(schema, dict) else {})

    return _BASE_STRICT_CALL(
        self,
        root_prompt_id,
        authoritative_model_call,
        max_attempts=max_attempts,
        context=context,
        run_mode=run_mode,
    )


def install_v8_discovery_receipt_contract_v206() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    prompt_runtime_module.PromptRuntime.__init__ = _prompt_runtime_init_v206  # type: ignore[assignment]
    prompt_runtime_module.PromptRuntime.strict_call = _strict_call_v206  # type: ignore[assignment]
    _INSTALLED = True
