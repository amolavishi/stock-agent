from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from stock_agent.hunt_resilience_v17 import (
    HUNT_RESILIENCE_VERSION,
    WIRE_CONTEXT_CHAR_BUDGET,
    _build_early_debt_payload,
    _safe_http_error_detail,
    _v17_openai_responses_call,
    _v17_provider_messages,
    project_context_for_wire,
)
from stock_agent.models import GateDecision
from stock_agent.providers import OpenAIResponsesProvider, ProviderRequestError


class _Response:
    def __init__(self, payload: dict, url: str = "https://api.openai.com/v1/responses") -> None:
        self.body = json.dumps(payload).encode("utf-8")
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size=None):
        return self.body


class _Initial:
    decision = GateDecision.INSUFFICIENT_EVIDENCE


class _Receipt:
    initial = _Initial()


class HuntResilienceV17Tests(unittest.TestCase):
    def test_large_evidence_is_projected_but_canonical_hash_and_sources_survive(self):
        sources = []
        for index in range(40):
            body = (
                "boilerplate " * 20_000
                + f" Company awarded a ${index + 1}00 million contract with customer and backlog expansion. "
                + "tail " * 5_000
            )
            sources.append({
                "source_class": "COMPANY_IR",
                "source_url": f"https://example.com/{index}",
                "source_observed_at": "2026-08-31T12:00:00Z",
                "title": f"source {index}",
                "content": body,
            })
        context = {
            "complete": True,
            "entries": [{
                "id": "research_context",
                "content": {
                    "source_stage": "RESEARCH_EVIDENCE",
                    "content_type": "ResearchContext",
                    "value": {"evidence_items": sources, "catalysts": [{"event_type": "CONTRACT_AWARD"}]},
                    "content_hash": "a" * 64,
                    "upstream_receipt": {"receipt_id": "stage-result:abc", "content_hash": "a" * 64},
                },
            }],
        }
        projected, metrics = project_context_for_wire(context)
        wire = json.dumps(projected, ensure_ascii=False, sort_keys=True)
        self.assertLessEqual(len(wire), WIRE_CONTEXT_CHAR_BUDGET)
        self.assertLess(metrics["wire_context_chars"], metrics["raw_context_chars"])
        self.assertEqual(metrics["authority"], "WIRE_PROJECTION_ONLY")
        value = projected["entries"][0]["content"]["value"]
        self.assertLessEqual(len(value["evidence_items"]), 25)  # 24 + truncation receipt
        first = value["evidence_items"][0]
        self.assertIn("source_url", first)
        self.assertIn("full_content_hash", first)
        self.assertIn("contract", first["content"].casefold())
        self.assertEqual(value["catalysts"][0]["event_type"], "CONTRACT_AWARD")

    def test_provider_messages_use_bounded_projection_not_full_context(self):
        giant = "X" * 1_000_000 + " contract $250 million " + "Y" * 1_000_000
        context = {"entries": [{"id": "x", "content": {"value": {"content": giant}}}], "complete": True}
        messages = _v17_provider_messages("policy", {"type": "object"}, context)
        self.assertEqual(len(messages), 2)
        self.assertIn("wire_projection", messages[1]["content"])
        self.assertNotIn(giant[:100_000], messages[1]["content"])
        self.assertLess(len(messages[1]["content"]), WIRE_CONTEXT_CHAR_BUDGET + 30_000)

    def test_responses_transport_does_not_duplicate_runtime_input_when_context_already_embedded(self):
        provider = OpenAIResponsesProvider("test-key")
        response = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"ok\": true}"}]}],
            "usage": {"input_tokens": 10, "output_tokens": 3, "input_tokens_details": {}, "output_tokens_details": {}},
        }
        captured: list[bytes] = []

        def fake_urlopen(request, timeout=None):
            captured.append(bytes(request.data or b""))
            return _Response(response)

        request = {
            "prompt_id": "test.stage",
            "output_schema_definition": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            "messages": [
                {"role": "system", "content": "policy"},
                {"role": "user", "content": "UNTRUSTED_CONTEXT_DATA already contains canonical context"},
            ],
            "context_manifest": {"complete": True},
            "runtime_input": {"DUPLICATION_SENTINEL": "must-not-cross-wire-twice"},
            "defer_provider_schema_validation": True,
            "reasoning_effort": "medium",
        }
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            payload, telemetry = _v17_openai_responses_call(provider, request)
        self.assertEqual(payload, {"ok": True})
        self.assertTrue(captured)
        wire = captured[0].decode("utf-8")
        self.assertNotIn("DUPLICATION_SENTINEL", wire)
        self.assertFalse(telemetry["runtime_input_duplicated"])
        self.assertGreater(telemetry["wire_request_bytes"], 0)
        self.assertEqual(telemetry["context_projection_version"], HUNT_RESILIENCE_VERSION)

    def test_http_400_surfaces_safe_openai_error_code_without_body_dump(self):
        provider = OpenAIResponsesProvider("test-key")
        body = json.dumps({
            "error": {
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
                "param": "input",
                "message": "sensitive long provider message must not be echoed",
            }
        }).encode("utf-8")
        exc = urllib.error.HTTPError(
            "https://api.openai.com/v1/responses", 400, "Bad Request", {}, io.BytesIO(body)
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(ProviderRequestError) as raised:
                _v17_openai_responses_call(provider, {
                    "prompt_id": "test.stage",
                    "output_schema_definition": {"type": "object", "properties": {}, "additionalProperties": False},
                    "messages": [{"role": "system", "content": "policy"}],
                    "reasoning_effort": "medium",
                })
        message = str(raised.exception)
        self.assertIn("HTTP 400", message)
        self.assertIn("type=invalid_request_error", message)
        self.assertIn("code=context_length_exceeded", message)
        self.assertIn("param=input", message)
        self.assertNotIn("sensitive long provider message", message)
        self.assertFalse(raised.exception.retryable)

    def test_safe_http_detail_ignores_malformed_body(self):
        exc = urllib.error.HTTPError("https://api.openai.com/v1/responses", 400, "Bad", {}, io.BytesIO(b"not-json"))
        self.assertEqual(_safe_http_error_detail(exc), "")

    def test_evidence_debt_payload_has_no_grade_or_execution_authority(self):
        payload = _build_early_debt_payload("PANW", _Receipt(), {
            "evidence_acquisition": {
                "refresh_attempts": 1,
                "source_exhausted": False,
                "successful_lanes": ["SEC_8K"],
                "missing_lanes": ["ISSUER_IR"],
                "grounded_catalyst_count": 0,
            }
        })
        self.assertEqual(payload["state"], "EVIDENCE_DEBT_BEFORE_CAPABILITY")
        self.assertEqual(payload["canonical_catalyst_decision"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(payload["grade_authority"])
        self.assertFalse(payload["pre_a_authority"])
        self.assertFalse(payload["execution_authority"])


if __name__ == "__main__":
    unittest.main()
