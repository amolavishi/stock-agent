"""Final evidence-origin conservatism for pre-live V8 hardening.

If a research-bundle child has no explicit origin_artifact_id, its source_class
or text is insufficient to prove independent provenance.  All such children
therefore share the parent artifact origin family.  Explicit origin_artifact_id
values may distinguish origins.  No grade or execution authority is added.
"""
from __future__ import annotations

from typing import Any

from . import v8_evidence_origin_v19 as origin
from . import v8_pre_live_integrity_v20 as v20
from .models import canonical_hash

V8_PRE_LIVE_EVIDENCE_ORIGIN_PATCH_VERSION = "V8_PRE_LIVE_EVIDENCE_ORIGIN_V2.0.2"
_INSTALLED = False


def source_lineage_v202(source: dict[str, Any], parent_artifact_id: str) -> tuple[str, str]:
    explicit = str(source.get("origin_artifact_id") or "").strip()
    origin_artifact = explicit or str(parent_artifact_id)
    content = origin._normalized_text(source.get("content") or source.get("document") or source.get("text") or source.get("body"))
    title = origin._normalized_text(source.get("title"))
    content_lineage_hash = canonical_hash({"title": title, "content": content})
    independent_origin_id = "ORIGIN-" + canonical_hash({"origin_artifact_id": origin_artifact})[:24]
    return independent_origin_id, content_lineage_hash


def install_v8_pre_live_integrity_v202() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    origin._source_lineage = source_lineage_v202  # type: ignore[assignment]
    v20._source_lineage_v20 = source_lineage_v202  # type: ignore[assignment]
    _INSTALLED = True
