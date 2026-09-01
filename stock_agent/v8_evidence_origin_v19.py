"""Python-owned evidence-origin lineage for V8 Step16.

A list of URLs is not evidence independence.  This layer materializes each
normalized source inside a research bundle as its own RawArtifact/Evidence
receipt, assigns a conservative independent-origin ID, injects that ledger into
the blind Step17 packet, and validates Step16 claim origin declarations in
Python.

Company PR -> media reprint chains therefore cannot become multiple independent
origins merely because several URLs exist.  Existing investment thresholds are
unchanged; an unresolved origin relationship is evidence debt, not bearishness.
"""
from __future__ import annotations

import copy
import re
from contextvars import ContextVar
from typing import Any

from . import runtime as runtime_module
from . import v8_next_certification as cert
from . import v8_next_runtime as next_runtime
from .models import Evidence, RawArtifact, canonical_hash, utc_now

V8_EVIDENCE_ORIGIN_VERSION = "V8_EVIDENCE_ORIGIN_V1.9"
_ORIGIN_CONTEXT: ContextVar[dict[str, str]] = ContextVar("v8_evidence_origin_map", default={})
_INSTALLED = False


def _normalized_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return text[:80_000]


def _source_lineage(source: dict[str, Any], parent_artifact_id: str) -> tuple[str, str]:
    source_class = str(source.get("source_class") or "UNKNOWN").upper()
    origin_artifact = str(source.get("origin_artifact_id") or parent_artifact_id)
    content = _normalized_text(source.get("content") or source.get("document") or source.get("text") or source.get("body"))
    title = _normalized_text(source.get("title"))
    content_lineage_hash = canonical_hash({"title": title, "content": content})
    # origin_artifact_id is deliberately dominant.  A Yahoo/RSS artifact that
    # contains multiple reprints is one conservative origin family. Exact
    # duplicate content across artifacts is also collapsed by the lineage hash.
    independent_origin_id = "ORIGIN-" + canonical_hash({
        "origin_artifact_id": origin_artifact,
        "source_class": source_class,
        "content_lineage_hash": content_lineage_hash,
    })[:24]
    return independent_origin_id, content_lineage_hash


def _materialize_research_sources(store: Any, research_artifact: Any) -> dict[str, str]:
    payload = research_artifact.payload if isinstance(getattr(research_artifact, "payload", None), dict) else {}
    items = payload.get("evidence_items") if isinstance(payload.get("evidence_items"), list) else []
    mapping: dict[str, str] = {}
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        source = copy.deepcopy(raw)
        content = source.get("content") or source.get("document") or source.get("text") or source.get("body")
        if content in (None, "", [], {}):
            continue
        origin_id, lineage_hash = _source_lineage(source, str(research_artifact.artifact_id))
        source_payload = {
            **source,
            "parent_research_artifact_id": str(research_artifact.artifact_id),
            "independent_origin_id": origin_id,
            "content_lineage_hash": lineage_hash,
            "evidence_origin_version": V8_EVIDENCE_ORIGIN_VERSION,
        }
        source_hash = canonical_hash(source_payload)
        artifact_id = f"artifact-research-source-{source_hash[:32]}"
        observed = str(source.get("source_observed_at") or research_artifact.source_observed_at or research_artifact.observed_at)
        child = RawArtifact(
            artifact_id,
            str(source.get("source_class") or research_artifact.provider),
            "RESEARCH_SOURCE_EVIDENCE",
            research_artifact.subject_id,
            observed,
            source_payload,
            source_hash,
            observed,
            utc_now(),
        )
        store.save_raw_artifact(child)
        evidence_id = f"E-RESEARCH_SOURCE:{origin_id[7:]}:{source_hash[:16]}"
        store.upsert_evidence(Evidence(
            evidence_id,
            str(research_artifact.subject_id),
            str(source.get("source_class") or research_artifact.provider),
            observed,
            0,
            source_hash,
            "RAW",
            raw_artifact_id=artifact_id,
        ))
        mapping[evidence_id] = origin_id
    return mapping


def _origin_map_for_evidence(store: Any, evidence_ids: list[str], materialized: dict[str, str]) -> dict[str, str]:
    result = dict(materialized)
    for evidence_id in evidence_ids:
        eid = str(evidence_id)
        if eid in result:
            continue
        row = store.connection.execute(
            "SELECT e.raw_artifact_id,r.artifact_type,r.provider,r.payload_hash "
            "FROM evidence e LEFT JOIN raw_artifacts r ON r.artifact_id=e.raw_artifact_id "
            "WHERE e.evidence_id=? AND e.status='ACTIVE'",
            (eid,),
        ).fetchone()
        if not row:
            continue
        artifact_id = str(row["raw_artifact_id"] or "")
        result[eid] = "ORIGIN-" + canonical_hash({
            "artifact_id": artifact_id,
            "artifact_type": str(row["artifact_type"] or ""),
            "provider": str(row["provider"] or ""),
            "payload_hash": str(row["payload_hash"] or ""),
        })[:24]
    return result


def _packet_origin_ledger(store: Any, evidence_ids: list[str]) -> list[dict[str, str]]:
    mapping = _ORIGIN_CONTEXT.get()
    rows = []
    for eid in sorted(set(str(item) for item in evidence_ids)):
        origin = mapping.get(eid)
        if not origin:
            continue
        rows.append({"evidence_id": eid, "independent_origin_id": origin})
    return rows


def _build_step17_packet_with_origins(store: Any, run_id: str, subject_id: str, candidate: dict[str, Any], evidence_ids: list[str], research_artifact_payload: dict[str, Any], policy_version: str, policy_hash: str) -> dict[str, Any]:
    packet = _BASE_BUILD_PACKET(store, run_id, subject_id, candidate, evidence_ids, research_artifact_payload, policy_version, policy_hash)
    packet = {key: copy.deepcopy(value) for key, value in packet.items() if key != "packet_hash"}
    packet["evidence_origin_ledger"] = _packet_origin_ledger(store, evidence_ids)
    packet["evidence_origin_version"] = V8_EVIDENCE_ORIGIN_VERSION
    packet["packet_hash"] = canonical_hash(packet)
    return packet


def _step16_schema_with_origins() -> dict[str, Any]:
    schema_id, body, schema = _BASE_STEP16_PROMPT
    value = copy.deepcopy(schema)
    claim = value["properties"]["atomic_claims"]["items"]
    claim["properties"]["independent_origin_ids"] = {
        "type": "array",
        "items": {"type": "string", "pattern": "^ORIGIN-[a-f0-9]{24}$"},
        "uniqueItems": True,
        "minItems": 1,
    }
    claim["required"].append("independent_origin_ids")
    strengthened_body = body + "\n\n# PYTHON EVIDENCE ORIGIN CONTRACT\nUse certification_packet.evidence_origin_ledger. For every atomic claim, list exactly the independent_origin_ids belonging to the evidence_ids cited by that claim. Multiple URLs with the same independent_origin_id are one origin, not independent evidence. Do not invent an origin ID."
    return schema_id, strengthened_body, value


def _default_step16_with_origins(packet: dict[str, Any], evidence_ids: list[str]) -> dict[str, Any]:
    value = _BASE_DEFAULT_STEP16(packet, evidence_ids)
    mapping = {str(item.get("evidence_id")): str(item.get("independent_origin_id")) for item in (packet.get("evidence_origin_ledger") or []) if isinstance(item, dict)}
    for claim in value.get("atomic_claims") or []:
        if not isinstance(claim, dict):
            continue
        origins = sorted({mapping[eid] for eid in (claim.get("evidence_ids") or []) if eid in mapping})
        claim["independent_origin_ids"] = origins or ["ORIGIN-" + "0" * 24]
    return value


def _finalize_atomic_audit_with_origins(draft: dict[str, Any], evidence_ids: list[str]) -> dict[str, Any]:
    value = _BASE_FINALIZE_ATOMIC(draft, evidence_ids)
    mapping = _ORIGIN_CONTEXT.get()
    failures = list(value.get("validation_failures") or [])
    origin_counts: dict[str, int] = {}
    for claim in value.get("atomic_claims") or []:
        if not isinstance(claim, dict):
            continue
        cited = [str(eid) for eid in (claim.get("evidence_ids") or [])]
        expected = sorted({mapping[eid] for eid in cited if eid in mapping})
        declared = sorted(set(str(item) for item in (claim.get("independent_origin_ids") or [])))
        if not expected:
            failures.append(f"NO_PYTHON_ORIGIN_LINEAGE:{claim.get('claim_id')}")
        elif declared != expected:
            failures.append(f"EVIDENCE_ORIGIN_MISMATCH:{claim.get('claim_id')}")
        for origin in expected:
            origin_counts[origin] = origin_counts.get(origin, 0) + 1
    if failures:
        value["status"] = "INCOMPLETE"
        value["evidence_independence"] = "FAIL"
    value["validation_failures"] = sorted(set(failures))
    value["python_independent_origin_count"] = len(origin_counts)
    value["python_evidence_origin_validation"] = "PASS" if not failures else "FAIL"
    value["evidence_origin_version"] = V8_EVIDENCE_ORIGIN_VERSION
    return value


def install_v8_evidence_origin_v19() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_evidence_origin_version", None) == V8_EVIDENCE_ORIGIN_VERSION:
        return current

    cert.PROMPTS[cert.PROMPT_STEP16] = _step16_schema_with_origins()
    cert.default_step16 = _default_step16_with_origins  # type: ignore[assignment]
    cert.build_step17_packet = _build_step17_packet_with_origins  # type: ignore[assignment]
    next_runtime._finalize_atomic_audit = _finalize_atomic_audit_with_origins  # type: ignore[assignment]

    class V8EvidenceOriginProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_evidence_origin_version = V8_EVIDENCE_ORIGIN_VERSION

        def _persist_hunt_reverse_valuation(self, run: Any, candidate: dict[str, Any], evidence_ids: list[str], research_artifact: Any, research_evidence_id: str, universe_artifact: Any, raw_row: dict[str, Any]):
            child_origins = _materialize_research_sources(self.store, research_artifact)
            for evidence_id in child_origins:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
            mapping = _origin_map_for_evidence(self.store, evidence_ids, child_origins)
            token = _ORIGIN_CONTEXT.set(mapping)
            try:
                return super()._persist_hunt_reverse_valuation(
                    run, candidate, evidence_ids, research_artifact, research_evidence_id, universe_artifact, raw_row
                )
            finally:
                _ORIGIN_CONTEXT.reset(token)

    runtime_module.ProductionStockAgent = V8EvidenceOriginProductionStockAgent
    _INSTALLED = True
    return V8EvidenceOriginProductionStockAgent


_BASE_BUILD_PACKET = cert.build_step17_packet
_BASE_DEFAULT_STEP16 = cert.default_step16
_BASE_STEP16_PROMPT = cert.PROMPTS[cert.PROMPT_STEP16]
_BASE_FINALIZE_ATOMIC = next_runtime._finalize_atomic_audit
