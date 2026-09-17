"""Prevent aggregate parent/child double-counting in V8 evidence lineage.

When a research artifact materializes child source receipts, the parent
research envelope is an aggregation container, not an additional independent
source.  Its Evidence ID is therefore removed from the Step16 origin map while
raw child origins remain available.  No grade/execution authority is added.
"""
from __future__ import annotations

import json
from typing import Any

from . import v8_evidence_origin_v19 as origin

V8_PRE_LIVE_PARENT_ORIGIN_PATCH_VERSION = "V8_PRE_LIVE_PARENT_ORIGIN_V2.0.3"
_INSTALLED = False
_BASE_ORIGIN_MAP = origin._origin_map_for_evidence


def origin_map_for_evidence_v203(store: Any, evidence_ids: list[str], materialized: dict[str, str]) -> dict[str, str]:
    result = _BASE_ORIGIN_MAP(store, evidence_ids, materialized)
    if not materialized:
        return result

    parent_artifact_ids: set[str] = set()
    for child_eid in materialized:
        row = store.connection.execute(
            "SELECT r.payload_json FROM evidence e JOIN raw_artifacts r ON r.artifact_id=e.raw_artifact_id "
            "WHERE e.evidence_id=? AND e.status='ACTIVE'",
            (str(child_eid),),
        ).fetchone()
        if not row:
            continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            continue
        parent = str(payload.get("parent_research_artifact_id") or "") if isinstance(payload, dict) else ""
        if parent:
            parent_artifact_ids.add(parent)

    if not parent_artifact_ids:
        return result

    for eid in list(result):
        row = store.connection.execute(
            "SELECT raw_artifact_id FROM evidence WHERE evidence_id=? AND status='ACTIVE'",
            (str(eid),),
        ).fetchone()
        if row and str(row["raw_artifact_id"] or "") in parent_artifact_ids:
            result.pop(eid, None)
    return result


def install_v8_pre_live_integrity_v203() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    origin._origin_map_for_evidence = origin_map_for_evidence_v203  # type: ignore[assignment]
    _INSTALLED = True
