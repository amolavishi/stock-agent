"""Deterministic rejected/not-evaluated outcome audit.

This is diagnostic analytics, never an investment-rule override.  It joins
immutable ShadowDecision rows with append-only +1/+3/+5/+10/+20/+40 session
outcomes and surfaces gates/providers that repeatedly discarded later winners.
No gate is relaxed automatically.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


HORIZONS = ("1D", "3D", "5D", "10D", "20D", "40D")


class FalseNegativeAuditError(RuntimeError):
    pass


def _decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _reason(decision: dict[str, Any]) -> str:
    if bool(decision.get("rejected")):
        return str(decision.get("rejected_stage") or decision.get("rejection_reason") or "REJECTED_UNKNOWN")
    if bool(decision.get("not_evaluated")):
        return str(decision.get("not_evaluated_stage") or decision.get("not_evaluated_reason") or "NOT_EVALUATED_UNKNOWN")
    return str(decision.get("decision") or "UNKNOWN")


def _latest_outcomes(connection: sqlite3.Connection, decision_id: str) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        "SELECT horizon,as_of,outcome_json FROM shadow_outcomes WHERE decision_id=? ORDER BY as_of,created_at",
        (decision_id,),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    latest_as_of: dict[str, str] = {}
    for row in rows:
        horizon = str(row["horizon"])
        if horizon not in HORIZONS:
            continue
        as_of = str(row["as_of"])
        value = _decode(row["outcome_json"])
        if not isinstance(value, dict):
            continue
        if horizon not in latest_as_of or as_of >= latest_as_of[horizon]:
            latest[horizon] = value
            latest_as_of[horizon] = as_of
    return latest


def audit_false_negatives(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    decisions: list[dict[str, Any]] = []
    for row in connection.execute(
        "SELECT decision_id,ticker,decision_json FROM shadow_decisions ORDER BY created_at,decision_id"
    ).fetchall():
        value = _decode(row["decision_json"])
        if not isinstance(value, dict):
            continue
        if not (bool(value.get("rejected")) or bool(value.get("not_evaluated"))):
            continue
        value = dict(value)
        value.setdefault("decision_id", str(row["decision_id"]))
        value.setdefault("ticker", str(row["ticker"]))
        decisions.append(value)

    by_reason: dict[str, dict[str, Any]] = {}
    by_sector: dict[str, dict[str, Any]] = {}
    incidents: list[dict[str, Any]] = []
    missing_outcome_ids: list[str] = []
    evaluated = 0
    winners_30 = 0
    winners_50 = 0

    for decision in decisions:
        decision_id = str(decision["decision_id"])
        outcomes = _latest_outcomes(connection, decision_id)
        reason = _reason(decision)
        sector = str(decision.get("sector") or "UNKNOWN")
        reason_bucket = by_reason.setdefault(reason, {
            "decisions": 0,
            "with_outcomes": 0,
            "winner_30_count": 0,
            "winner_50_count": 0,
            "max_mfe": None,
            "min_mae": None,
        })
        sector_bucket = by_sector.setdefault(sector, {
            "decisions": 0,
            "with_outcomes": 0,
            "winner_30_count": 0,
            "winner_50_count": 0,
        })
        reason_bucket["decisions"] += 1
        sector_bucket["decisions"] += 1

        if not outcomes:
            missing_outcome_ids.append(decision_id)
            continue
        evaluated += 1
        reason_bucket["with_outcomes"] += 1
        sector_bucket["with_outcomes"] += 1

        max_mfe = max((float(value.get("mfe")) for value in outcomes.values() if isinstance(value.get("mfe"), (int, float))), default=None)
        min_mae = min((float(value.get("mae")) for value in outcomes.values() if isinstance(value.get("mae"), (int, float))), default=None)
        max_forward = max((float(value.get("forward_return")) for value in outcomes.values() if isinstance(value.get("forward_return"), (int, float))), default=None)
        max_mfe_horizon = None
        if max_mfe is not None:
            max_mfe_horizon = next((horizon for horizon in HORIZONS if horizon in outcomes and outcomes[horizon].get("mfe") == max_mfe), None)
            reason_bucket["max_mfe"] = max_mfe if reason_bucket["max_mfe"] is None else max(float(reason_bucket["max_mfe"]), max_mfe)
        if min_mae is not None:
            reason_bucket["min_mae"] = min_mae if reason_bucket["min_mae"] is None else min(float(reason_bucket["min_mae"]), min_mae)

        winner_30 = bool(max_mfe is not None and max_mfe >= 0.30)
        winner_50 = bool(max_mfe is not None and max_mfe >= 0.50)
        if winner_30:
            winners_30 += 1
            reason_bucket["winner_30_count"] += 1
            sector_bucket["winner_30_count"] += 1
        if winner_50:
            winners_50 += 1
            reason_bucket["winner_50_count"] += 1
            sector_bucket["winner_50_count"] += 1

        if winner_30:
            incidents.append({
                "decision_id": decision_id,
                "ticker": decision.get("ticker"),
                "decision": decision.get("decision"),
                "reason": reason,
                "sector": sector,
                "max_mfe": max_mfe,
                "max_mfe_horizon": max_mfe_horizon,
                "max_forward_return": max_forward,
                "min_mae": min_mae,
                "operational_failure": bool(decision.get("not_evaluated")),
            })

    for bucket in by_reason.values():
        n = int(bucket["with_outcomes"])
        bucket["winner_30_rate"] = (int(bucket["winner_30_count"]) / n) if n else None
        bucket["winner_50_rate"] = (int(bucket["winner_50_count"]) / n) if n else None
    for bucket in by_sector.values():
        n = int(bucket["with_outcomes"])
        bucket["winner_30_rate"] = (int(bucket["winner_30_count"]) / n) if n else None
        bucket["winner_50_rate"] = (int(bucket["winner_50_count"]) / n) if n else None

    repeated_winner_killers = [
        {"reason": reason, **bucket}
        for reason, bucket in sorted(by_reason.items())
        if int(bucket["winner_30_count"]) >= 2
    ]
    operational_winners = [
        item for item in incidents
        if item["operational_failure"] or any(marker in str(item["reason"]).upper() for marker in ("PROVIDER", "ENGINEERING", "NOT_EVALUATED"))
    ]

    return {
        "audit_type": "FALSE_NEGATIVE_OUTCOME_AUDIT_V1",
        "authority": "DIAGNOSTIC_ONLY_NO_GATE_RELAXATION",
        "horizons": list(HORIZONS),
        "audited_decision_count": len(decisions),
        "decisions_with_outcomes": evaluated,
        "decisions_missing_outcomes": len(missing_outcome_ids),
        "missing_outcome_decision_ids": missing_outcome_ids,
        "winner_30_count": winners_30,
        "winner_50_count": winners_50,
        "by_reason": by_reason,
        "by_sector": by_sector,
        "winner_incidents": incidents,
        "operational_failure_winners": operational_winners,
        "repeated_winner_killers": repeated_winner_killers,
    }


def audit_database(database: Path) -> dict[str, Any]:
    path = database.expanduser().resolve()
    if not path.is_file():
        raise FalseNegativeAuditError(f"database does not exist: {path}")
    connection = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
    try:
        return audit_false_negatives(connection)
    except sqlite3.DatabaseError as exc:
        raise FalseNegativeAuditError(f"false-negative audit failed: {exc}") from exc
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit rejected/not-evaluated Shadow decisions against later outcomes")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_database(args.database)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
