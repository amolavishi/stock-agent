"""Structured, read-only PRIMARY source bundle for the non-authoritative PRE-A sidecar.

PRE-A must not infer eligibility from human Markdown formatting.  This module
projects only persisted PRIMARY SQLite state (ShadowDecision + StageResult)
into a bounded JSON bundle.  It never writes SQLite and carries no grade,
execution, position-sizing, or broker authority of its own.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import canonical_hash


PRE_A_SOURCE_VERSION = "PRE_A_STRUCTURED_SOURCE_V2"
STEP18_SOURCE_SHA256 = "26fddaa0b0ddec166427d89a50ad0f272d06ee6d43a6b91995f45fefaa039528"

_INCLUDED_STAGES = (
    "STAGE_GATE",
    "CAPITAL_PRESCREEN_GATE",
    "CATALYST_GATE",
    "EXPECTATION_GAP_GATE",
    "CAP_FUNDAMENTAL_CHANGE",
    "CAP_CATALYST_EXPECTATION_RESEARCH",
    "CAP_DIRECTIONAL_PROBABILITY",
    "DEEP_RESEARCH",
    "FULL_SEC_FORENSIC",
    "STANDARD_AUDIT",
    "ADVERSARIAL_AUDIT",
    "V8_CERTIFICATION",
    "CANDIDATE_CONSERVATION",
    "EVIDENCE_DEBT",
    "SOURCE_EXHAUSTED",
    "RESEARCH_PROVIDER_FAILURE",
    "SEC_PROVIDER_FAILURE",
    "SEC_STALE_DATA",
    "CANDIDATE_ENGINEERING_FAILURE",
)
_ALLOWED_GRADES = {"A", "A-", "B+", "B", "EXCLUDE", "NOT_EVALUATED", "UNKNOWN"}


class PreASourceError(RuntimeError):
    """Fail-closed structured-source error."""


def _decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _readonly_connection(database: Path) -> sqlite3.Connection:
    path = database.expanduser().resolve()
    if not path.is_file():
        raise PreASourceError(f"PRIMARY SQLite database does not exist: {path}")
    # SQLite URI read-only mode prevents the PRE-A process from creating,
    # migrating, journaling, or otherwise mutating PRIMARY state.
    uri = "file:" + path.as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _certification_grade(value: dict[str, Any] | None) -> str | None:
    if not isinstance(value, dict):
        return None
    if str(value.get("source_sha256") or "") != STEP18_SOURCE_SHA256:
        return None
    if value.get("grade_authority") not in {True, "V8_STEP18_CANONICAL"}:
        return None
    if value.get("discovery_score_used") not in {False, "NO", "FALSE"}:
        return None
    grade = str(value.get("research_grade") or value.get("grade") or "").upper()
    return grade if grade in {"A", "A-", "B+", "B"} else None


def build_pre_a_source_bundle(database: Path, shadow_run_id: str) -> dict[str, Any]:
    """Build a deterministic PRE-A input from persisted PRIMARY state only."""
    run_key = str(shadow_run_id or "").strip()
    if not run_key:
        raise PreASourceError("shadow_run_id is required")

    connection = _readonly_connection(database)
    try:
        shadow = connection.execute(
            "SELECT shadow_run_id,shadow_version,status,started_at,finished_at,hunt_run_id,execution_run_id,broker_write_count "
            "FROM shadow_runs WHERE shadow_run_id=?",
            (run_key,),
        ).fetchone()
        if shadow is None:
            raise PreASourceError(f"unknown shadow_run_id: {run_key}")
        hunt_run_id = str(shadow["hunt_run_id"] or "").strip()
        if not hunt_run_id:
            raise PreASourceError("PRIMARY Shadow run has no authoritative HUNT run id")
        if int(shadow["broker_write_count"] or 0) != 0:
            raise PreASourceError("PRIMARY Shadow run violates broker_write=0 invariant")

        decisions: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            "SELECT ticker,decision_json,decision_hash FROM shadow_decisions WHERE shadow_run_id=? ORDER BY ticker",
            (run_key,),
        ).fetchall():
            value = _decode(row["decision_json"])
            if not isinstance(value, dict):
                raise PreASourceError(f"malformed ShadowDecision for {row['ticker']}")
            decisions[str(row["ticker"]).upper()] = {
                "value": value,
                "decision_hash": str(row["decision_hash"]),
            }

        stage_map: dict[str, dict[str, dict[str, Any]]] = {}
        placeholders = ",".join("?" for _ in _INCLUDED_STAGES)
        rows = connection.execute(
            f"SELECT stage,subject_id,result_json,status,dependency_hash,evidence_epoch,created_at "
            f"FROM stage_results WHERE run_id=? AND subject_id IS NOT NULL AND stage IN ({placeholders}) "
            "ORDER BY created_at,result_id",
            (hunt_run_id, *_INCLUDED_STAGES),
        ).fetchall()
        for row in rows:
            ticker = str(row["subject_id"] or "").upper().strip()
            if not ticker:
                continue
            result = _decode(row["result_json"])
            stage_map.setdefault(ticker, {})[str(row["stage"])] = {
                "status": str(row["status"]),
                "result": result if isinstance(result, dict) else {"raw_value": result},
                "dependency_hash": str(row["dependency_hash"]),
                "evidence_epoch": int(row["evidence_epoch"] or 0),
                "created_at": str(row["created_at"]),
            }

        tickers = sorted(set(decisions) | set(stage_map))
        candidates: list[dict[str, Any]] = []
        for ticker in tickers:
            decision_entry = decisions.get(ticker) or {}
            decision = decision_entry.get("value") if isinstance(decision_entry.get("value"), dict) else {}
            stages = stage_map.get(ticker) or {}
            cert_entry = stages.get("V8_CERTIFICATION") or {}
            cert = cert_entry.get("result") if isinstance(cert_entry.get("result"), dict) else None

            decision_grade = str(decision.get("grade") or "").upper()
            if decision_grade not in _ALLOWED_GRADES:
                decision_grade = ""
            cert_grade = _certification_grade(cert)
            source_grade = decision_grade or cert_grade or "UNKNOWN"
            # If both exist they must agree.  PRE-A must never reconcile a
            # disagreement by guessing which grade is newer/correct.
            grade_conflict = bool(decision_grade and cert_grade and decision_grade != cert_grade)
            if grade_conflict:
                source_grade = "UNKNOWN"

            candidates.append({
                "ticker": ticker,
                "source_grade": source_grade,
                "grade_conflict": grade_conflict,
                "decision": decision,
                "decision_hash": decision_entry.get("decision_hash"),
                "stages": stages,
            })

        bundle = {
            "source_version": PRE_A_SOURCE_VERSION,
            "authority": "NON_AUTHORITATIVE_READ_ONLY_PROJECTION",
            "primary_mutation": False,
            "broker_write_count": 0,
            "shadow_run": {
                "shadow_run_id": str(shadow["shadow_run_id"]),
                "shadow_version": str(shadow["shadow_version"]),
                "status": str(shadow["status"]),
                "started_at": str(shadow["started_at"]),
                "finished_at": shadow["finished_at"],
                "hunt_run_id": hunt_run_id,
                "execution_run_id": shadow["execution_run_id"],
            },
            "candidate_count": len(candidates),
            "candidates": candidates,
        }
        bundle["source_hash"] = canonical_hash(bundle)
        return bundle
    except sqlite3.DatabaseError as exc:
        raise PreASourceError(f"failed to read PRIMARY structured source: {exc}") from exc
    finally:
        connection.close()


def candidate_index(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = bundle.get("candidates") if isinstance(bundle, dict) else None
    if not isinstance(values, list):
        raise PreASourceError("structured PRE-A source has no candidates array")
    result: dict[str, dict[str, Any]] = {}
    for candidate in values:
        if not isinstance(candidate, dict):
            raise PreASourceError("structured PRE-A candidate is malformed")
        ticker = str(candidate.get("ticker") or "").upper().strip()
        if not ticker or ticker in result:
            raise PreASourceError("structured PRE-A source contains missing/duplicate ticker")
        result[ticker] = candidate
    return result
