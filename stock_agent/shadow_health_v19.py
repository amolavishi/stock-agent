"""Shadow health probe hardening.

The provider health check must test transport/schema support only.  It must not
ask the model to recreate a full MarketAnalysisResult, because fields such as
canonical manifest hashes are research-output invariants rather than transport
health signals.  A fixed minimal schema makes the probe deterministic and
prevents a harmless echo mismatch from aborting PRIMARY before discovery.
"""
from __future__ import annotations

from typing import Any

from . import shadow as shadow_module

SHADOW_HEALTH_VERSION = "SHADOW_HEALTH_V1.9"
_HEALTH_NONCE = "0" * 64
_INSTALLED = False


def _health_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "const": "HEALTH_OK"},
            "nonce": {"type": "string", "const": _HEALTH_NONCE},
        },
        "required": ["status", "nonce"],
        "additionalProperties": False,
    }


def _health_check(self: Any) -> dict[str, Any]:
    schema = _health_schema()
    payload, telemetry = self.provider.call({
        "prompt_id": "health.luna_transport_v19",
        "messages": [
            {
                "role": "system",
                "content": (
                    "This is a transport health probe, not an investment task. "
                    "Return exactly the JSON object required by the supplied schema."
                ),
            },
            {
                "role": "user",
                "content": '{"status":"HEALTH_OK","nonce":"' + _HEALTH_NONCE + '"}',
            },
        ],
        "output_schema_definition": schema,
        "reasoning_effort": getattr(self.provider, "reasoning_effort", "medium"),
        "max_tokens": 256,
    })
    if not isinstance(payload, dict) or payload.get("status") != "HEALTH_OK" or payload.get("nonce") != _HEALTH_NONCE:
        raise ValueError("Luna transport health probe returned an invalid fixed receipt")
    return {
        "status": "PASS",
        "health_version": SHADOW_HEALTH_VERSION,
        "model": telemetry.get("model"),
        "latency_ms": telemetry.get("latency_ms"),
        "usage_source": telemetry.get("usage_source"),
        "input_tokens": telemetry.get("input_tokens", 0),
        "cached_input_tokens": telemetry.get("cached_tokens", 0),
        "output_tokens": telemetry.get("output_tokens", 0),
        "reasoning_output_tokens": telemetry.get("reasoning_output_tokens", 0),
    }


def install_shadow_health_v19() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    shadow_module.LunaHealthChecker.check = _health_check
    shadow_module.SHADOW_HEALTH_VERSION = SHADOW_HEALTH_VERSION
    _INSTALLED = True
