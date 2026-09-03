"""V8 MAIN Discovery execution integrity.

This module hardens the existing MAIN Discovery path; it does not create a
parallel discovery engine.  The canonical V8 source prompts remain the
qualitative scanner authority and ``workflow.stock_scout`` remains the sole
final DiscoveryCandidateSetV2 owner.

It implements the forensic audit invariants that were missing from MAIN:
- LANE_TOUCHED != SCANNER_EXECUTED
- explicit 02..14 round execution with model-call receipts
- scanner-specific structured output-equivalent checks
- RAW / ELIGIBLE / SIGNAL coverage separation
- persistent Secondary queue with expiry and no grade/PRE-A authority
- Near-Miss separation from structural hard fails
- search stop requires scanner completion + two low-yield rounds + no open
  HIGH research-value debt + rejection sentinel completion + operational
  source/budget exhaustion evidence
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from . import runtime as runtime_module
from . import v8_main_discovery_coach as coach
from .models import RunMode, RunOutcome, canonical_hash, utc_now

V8_MAIN_DISCOVERY_INTEGRITY_VERSION = "V8_MAIN_DISCOVERY_INTEGRITY_V1.0"
SCANNER_OUTPUT_CONTRACT_VERSION = "V8_MAIN_SCANNER_OUTPUT_V1.1"
SCANNER_ROUND_SIZE = 75
SECONDARY_EXPIRY_DAYS = 56

# Scanner-specific output-equivalent dimensions.  These are not Python signal
# heuristics; they are proof that the source scanner's distinct analytical
# questions were actually represented in the structured model output.
SCANNER_REQUIRED_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "02": ("economic_change", "catalyst_1_8w", "price_lag", "stage"),
    "03": ("ipo_dislocation", "operating_delta", "lockup_resale"),
    "04": ("operating_inflection", "one_off_vs_structural", "cash_conversion"),
    "05": ("funded_policy", "issuer_materiality", "realization_1_8w"),
    "06": ("funded_backlog", "contract_quality", "revenue_conversion"),
    "07": ("profitability_inflection", "cash_conversion", "underfollowed"),
    "08": ("offering_terms", "dilution_float", "absorption"),
    "09": ("open_market_purchase", "buyback_execution", "sbc_offset"),
    "10": ("refinancing_terms", "maturity_covenant", "interest_cashflow"),
    "11": ("earnings_surprise", "estimate_revision", "abnormal_price_reaction"),
    "12": ("customer_concentration", "second_customer_economics", "diversification_realization"),
    "13": ("branch_kpi", "sector_rotation", "stock_relative_strength"),
    "14": ("bottleneck_directness", "demand_evidence", "per_share_economics"),
}

_ACTION_RANK = {
    "DEEP_DIVE_NOW": 5,
    "DEEP_DIVE_SECONDARY": 4,
    "WATCH_RESET": 3,
    "WATCH_STAGE0": 2,
    "EXCLUDE": 1,
}

_PREPARED = False
_PRE_INSTALLED = False
_POST_INSTALLED = False


def _integrity_scanner_schema() -> dict[str, Any]:
    base = _ORIGINAL_SCANNER_SCHEMA()
    candidate = base["properties"]["candidates"]["items"]
    candidate["properties"].update({
        "partial_signal": {"type": "boolean"},
        "failure_class": {
            "type": "string",
            "enum": [
                "NONE", "STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL",
                "DISCOVERY_INSUFFICIENT", "TIME_HORIZON_MISMATCH",
                "PRICE_STAGE_MISMATCH", "DATA_INTEGRITY_BLOCK",
            ],
        },
        "strategy_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string", "minLength": 1},
                    "status": {"type": "string", "enum": ["VERIFIED", "STALE", "UNKNOWN", "CONFLICT"]},
                    "summary": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["dimension", "status", "summary", "evidence_ids"],
                "additionalProperties": False,
            },
        },
    })
    candidate["required"].extend(["partial_signal", "failure_class", "strategy_evidence"])
    base["properties"].update({
        "output_contract_version": {"const": SCANNER_OUTPUT_CONTRACT_VERSION},
        "strategy_contract": {
            "type": "object",
            "properties": {
                "scanner_id": {"type": "string"},
                "dimensions_evaluated": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "methodology_summary": {"type": "string", "minLength": 1},
            },
            "required": ["scanner_id", "dimensions_evaluated", "methodology_summary"],
            "additionalProperties": False,
        },
        "source_exhaustion": {"type": "boolean"},
        "source_exhaustion_reason": {"type": "string"},
    })
    base["required"].extend(["output_contract_version", "strategy_contract", "source_exhaustion", "source_exhaustion_reason"])
    return base


def _integrity_default_scanner(scanner_id: str, screened_count: int) -> dict[str, Any]:
    value = _ORIGINAL_DEFAULT_SCANNER(scanner_id, screened_count)
    value.update({
        "output_contract_version": SCANNER_OUTPUT_CONTRACT_VERSION,
        "strategy_contract": {
            "scanner_id": scanner_id,
            "dimensions_evaluated": list(SCANNER_REQUIRED_DIMENSIONS[scanner_id]),
            "methodology_summary": "default payload is non-authoritative; validated provider output is required",
        },
        "source_exhaustion": False,
        "source_exhaustion_reason": "NOT_PROVEN",
    })
    return value


def prepare_v8_main_discovery_integrity() -> None:
    """Patch scanner schema/defaults without owning canonical source identity.

    Source SHA/path/package identity belongs exclusively to
    ``v8_main_source_fidelity``. This legacy compatibility helper must never
    mutate source identity, regardless of import or call order.
    """
    global _PREPARED
    if _PREPARED:
        return
    coach._scanner_schema = _integrity_scanner_schema  # type: ignore[assignment]
    coach._default_scanner = _integrity_default_scanner  # type: ignore[assignment]
    _PREPARED = True


def _contract_complete(scanner_id: str, result: dict[str, Any], expected_count: int) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if result.get("scanner_id") != scanner_id:
        failures.append("SCANNER_ID")
    if result.get("scanner_source_sha256") != coach.V8_SCANNERS[scanner_id]["sha256"]:
        failures.append("SOURCE_SHA")
    if result.get("output_contract_version") != SCANNER_OUTPUT_CONTRACT_VERSION:
        failures.append("OUTPUT_CONTRACT_VERSION")
    if int(result.get("screened_count") or 0) != expected_count:
        failures.append("SCREENED_COUNT")
    if result.get("grade_authority") is not False:
        failures.append("GRADE_AUTHORITY")
    contract = result.get("strategy_contract") if isinstance(result.get("strategy_contract"), dict) else {}
    if str(contract.get("scanner_id") or "") != scanner_id:
        failures.append("STRATEGY_CONTRACT_SCANNER")
    seen = {str(item) for item in (contract.get("dimensions_evaluated") or [])}
    missing = sorted(set(SCANNER_REQUIRED_DIMENSIONS[scanner_id]) - seen)
    if missing:
        failures.append("STRATEGY_DIMENSIONS:" + ",".join(missing))
    for candidate in result.get("candidates") or []:
        if not isinstance(candidate, dict):
            failures.append("CANDIDATE_TYPE")
            continue
        evidence_dims = {
            str(item.get("dimension"))
            for item in (candidate.get("strategy_evidence") or [])
            if isinstance(item, dict) and item.get("dimension")
        }
        # A retained signal must carry scanner-specific evidence/UNKNOWN rows.
        action = str(candidate.get("recommended_discovery_action") or "")
        if action in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY"}:
            missing_candidate = sorted(set(SCANNER_REQUIRED_DIMENSIONS[scanner_id]) - evidence_dims)
            if missing_candidate:
                failures.append(f"CANDIDATE_DIMENSIONS:{candidate.get('security_id')}:{','.join(missing_candidate)}")
        if candidate.get("failure_class") == "STRUCTURAL_HARD_FAIL" and action != "EXCLUDE":
            failures.append(f"STRUCTURAL_FAIL_ROUTING:{candidate.get('security_id')}")
    return not failures, failures


def _merge_candidate(existing: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return copy.deepcopy(new)
    a = str(existing.get("recommended_discovery_action") or "EXCLUDE")
    b = str(new.get("recommended_discovery_action") or "EXCLUDE")
    chosen = copy.deepcopy(new if _ACTION_RANK.get(b, 0) > _ACTION_RANK.get(a, 0) else existing)
    for key in ("strengths", "weaknesses", "unknowns", "verification_questions", "strategy_evidence"):
        merged: list[Any] = []
        seen: set[str] = set()
        for item in list(existing.get(key) or []) + list(new.get(key) or []):
            token = canonical_hash(item)
            if token in seen:
                continue
            seen.add(token)
            merged.append(copy.deepcopy(item))
        chosen[key] = merged
    chosen["partial_signal"] = bool(existing.get("partial_signal")) or bool(new.get("partial_signal"))
    return chosen


def _model_call_receipt(store: Any, run_id: str, stage: str) -> dict[str, Any]:
    row = store.connection.execute(
        "SELECT wi.work_item_id,wi.status,wi.created_at AS started_at,wi.updated_at AS completed_at,"
        "mc.provider,mc.model,mc.reasoning_effort,mc.router_profile,mc.created_at AS model_call_at "
        "FROM work_items wi LEFT JOIN model_calls mc ON mc.work_item_id=wi.work_item_id "
        "WHERE wi.run_id=? AND wi.stage=? ORDER BY mc.created_at DESC,wi.created_at DESC LIMIT 1",
        (run_id, stage),
    ).fetchone()
    if not row:
        return {"model_call_executed": False, "work_item_status": "MISSING"}
    return {
        "work_item_id": str(row["work_item_id"]),
        "work_item_status": str(row["status"]),
        "model_call_executed": row["provider"] is not None,
        "model_provider": row["provider"],
        "model_name": row["model"],
        "reasoning_effort": row["reasoning_effort"],
        "router_profile": row["router_profile"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "model_call_at": row["model_call_at"],
    }


def _candidate_evidence_ids(candidate: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for item in candidate.get("strategy_evidence") or []:
        if not isinstance(item, dict):
            continue
        values.update(str(value) for value in (item.get("evidence_ids") or []) if str(value))
    return values


def _round_metrics(scanner_id: str, round_id: str, chunk: list[dict[str, Any]], result: dict[str, Any], cumulative: int, prior_evidence: set[str]) -> tuple[dict[str, Any], set[str]]:
    candidates = [item for item in (result.get("candidates") or []) if isinstance(item, dict)]
    evidence: set[str] = set()
    for item in candidates:
        evidence.update(_candidate_evidence_ids(item))
    new_evidence = evidence - prior_evidence
    signals = [item for item in candidates if str(item.get("signal_strength")) in {"STRONG", "MODERATE", "WEAK"}]
    secondary = [item for item in candidates if item.get("recommended_discovery_action") == "DEEP_DIVE_SECONDARY"]
    deep = [item for item in candidates if item.get("recommended_discovery_action") == "DEEP_DIVE_NOW"]
    high = [item for item in candidates if item.get("research_value") == "HIGH"]
    partial = [item for item in candidates if bool(item.get("partial_signal")) or item.get("signal_strength") == "UNKNOWN"]
    return ({
        "round_id": round_id,
        "scanner_id": scanner_id,
        "new_unique_tickers": len(chunk),
        "cumulative_unique_tickers": cumulative,
        "new_deep_dive_now": len(deep),
        "new_secondary": len(secondary),
        "new_high_research_value": len(high),
        "new_signal": len(signals),
        "new_partial_signal": len(partial),
        "new_independent_evidence": len(new_evidence),
        "duplicate_count": max(0, len(candidates) - len({str(item.get('security_id') or '') for item in candidates})),
        "duplicate_saturation": max(0.0, (len(candidates) - len({str(item.get('security_id') or '') for item in candidates})) / max(1, len(candidates))),
        "marginal_candidate_yield": (len(deep) + len(secondary)) / max(1, len(chunk)),
        "marginal_evidence_yield": len(new_evidence) / max(1, len(chunk)),
        "source_exhaustion_model_claim": bool(result.get("source_exhaustion")),
        "source_exhaustion_reason": str(result.get("source_exhaustion_reason") or ""),
        "search_expansion_questions": list(result.get("search_expansion_questions") or []),
        "grade_authority": False,
    }, evidence)


def install_pre_coach_discovery_integrity() -> type:
    """Install the executor parent that the MAIN coach calls via super()."""
    global _PRE_INSTALLED
    prepare_v8_main_discovery_integrity()
    current = runtime_module.ProductionStockAgent
    if _PRE_INSTALLED or getattr(current, "v8_main_discovery_integrity_pre_version", None) == V8_MAIN_DISCOVERY_INTEGRITY_VERSION:
        return current

    class V8MainDiscoveryIntegrityPreProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_main_discovery_integrity_pre_version = V8_MAIN_DISCOVERY_INTEGRITY_VERSION

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._v8_integrity_state: dict[str, dict[str, Any]] = {}
            self.store.connection.execute(
                "CREATE TABLE IF NOT EXISTS discovery_secondary_queue ("
                "security_id TEXT PRIMARY KEY, originating_run_id TEXT NOT NULL, scanner_ids_json TEXT NOT NULL, "
                "research_value TEXT NOT NULL, missing_evidence_json TEXT NOT NULL, verification_path_json TEXT NOT NULL, "
                "expected_resolution TEXT NOT NULL, recheck_trigger TEXT NOT NULL, expiry TEXT NOT NULL, status TEXT NOT NULL, "
                "payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )

        def _work_stage(self, run, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
            if not stage.startswith("V8_MAIN_SCANNER_") or "_R" in stage:
                return super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            scanner_id = stage.rsplit("_", 1)[-1]
            if scanner_id not in SCANNER_REQUIRED_DIMENSIONS:
                return super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            raw = copy.deepcopy(payload.get("raw_input") or {})
            universe = [row for row in (raw.get("candidate_universe_packet") or []) if isinstance(row, dict)]
            chunks = [universe[index:index + SCANNER_ROUND_SIZE] for index in range(0, len(universe), SCANNER_ROUND_SIZE)] or [[]]
            merged: dict[str, dict[str, Any]] = {}
            all_unknowns: list[str] = []
            all_expansion: list[str] = []
            rounds: list[dict[str, Any]] = []
            round_receipts: list[dict[str, Any]] = []
            cumulative = 0
            prior_evidence: set[str] = set()
            all_complete = True
            all_source_exhausted = True

            for index, chunk in enumerate(chunks, 1):
                round_id = f"{scanner_id}-R{index:03d}"
                round_stage = f"V8_MAIN_SCANNER_{scanner_id}_R{index:03d}"
                round_raw = copy.deepcopy(raw)
                round_raw["candidate_universe_packet"] = chunk
                round_raw["round_id"] = round_id
                round_raw["round_sequence"] = index
                round_raw["round_count"] = len(chunks)
                round_payload = {
                    "raw_input": round_raw,
                    "default_payload": _integrity_default_scanner(scanner_id, len(chunk)),
                }
                result = super()._work_stage(run, round_stage, prompt_id, round_payload, subject_id, dependency_ids, context_inputs)
                result = dict(result)
                complete, failures = _contract_complete(scanner_id, result, len(chunk))
                call = _model_call_receipt(self.store, run.run_id, round_stage)
                source_state = coach.V8_SCANNERS[scanner_id]
                validated = complete and bool(call.get("model_call_executed")) and call.get("work_item_status") == "SUCCEEDED" and source_state.get("source_integrity_status") == "PASS"
                execution_status = "SIGNAL_SCAN_COMPLETE" if validated else ("DATA_BLOCKED" if source_state.get("source_integrity_status") != "PASS" else "SIGNAL_SCAN_PARTIAL")
                cumulative += len(chunk)
                metrics, evidence = _round_metrics(scanner_id, round_id, chunk, result, cumulative, prior_evidence)
                prior_evidence.update(evidence)
                metrics["output_contract_complete"] = complete
                metrics["scanner_execution_validated"] = validated
                self.store.record_funnel(run.run_id, f"V8_SCANNER_{scanner_id}_{round_id}_METRICS", len(chunk), metrics)
                round_receipt = {
                    "scanner_id": scanner_id,
                    "scanner_name": coach.V8_SCANNERS[scanner_id]["name"],
                    "source_file": source_state.get("source_file"),
                    "source_sha256": source_state.get("sha256"),
                    "source_integrity_status": source_state.get("source_integrity_status"),
                    "prompt_runtime_hash": source_state.get("runtime_prompt_sha256"),
                    "run_id": run.run_id,
                    "round_id": round_id,
                    "universe_input_count": len(chunk),
                    "unique_ticker_count": len({str(row.get('security_id') or row.get('ticker') or '') for row in chunk}),
                    "evaluated_count": int(result.get("screened_count") or 0),
                    "signal_count": metrics["new_signal"],
                    "partial_signal_count": metrics["new_partial_signal"],
                    "secondary_count": metrics["new_secondary"],
                    "deep_count": metrics["new_deep_dive_now"],
                    "excluded_count": sum(1 for item in (result.get("candidates") or []) if isinstance(item, dict) and item.get("recommended_discovery_action") == "EXCLUDE"),
                    "unknown_count": sum(1 for item in (result.get("candidates") or []) if isinstance(item, dict) and item.get("unknowns")),
                    "output_schema_version": SCANNER_OUTPUT_CONTRACT_VERSION,
                    "output_validated": complete,
                    "execution_status": execution_status,
                    "failure_class": None if validated else ("SOURCE_INTEGRITY" if source_state.get("source_integrity_status") != "PASS" else "OUTPUT_OR_MODEL_CONTRACT"),
                    "contract_failures": failures,
                    "source_exhaustion": bool(result.get("source_exhaustion")),
                    "grade_authority": False,
                    **call,
                }
                round_receipts.append(round_receipt)
                self.store.record_funnel(run.run_id, f"V8_MAIN_SCANNER_{scanner_id}_{round_id}_RECEIPT", len(chunk), round_receipt)
                all_complete = all_complete and validated
                all_source_exhausted = all_source_exhausted and bool(result.get("source_exhaustion"))
                rounds.append(metrics)
                all_unknowns.extend(str(item) for item in (result.get("systemic_unknowns") or []))
                all_expansion.extend(str(item) for item in (result.get("search_expansion_questions") or []))
                for candidate in result.get("candidates") or []:
                    if isinstance(candidate, dict) and candidate.get("security_id"):
                        sid = str(candidate["security_id"]).upper()
                        merged[sid] = _merge_candidate(merged.get(sid), candidate)

            aggregate = {
                "scanner_id": scanner_id,
                "scanner_source_sha256": coach.V8_SCANNERS[scanner_id]["sha256"],
                "execution_status": "COMPLETE" if all_complete else "PARTIAL",
                "screened_count": len(universe),
                "candidates": list(merged.values()),
                "systemic_unknowns": list(dict.fromkeys(all_unknowns)),
                "search_expansion_questions": list(dict.fromkeys(all_expansion)),
                "grade_authority": False,
                "output_contract_version": SCANNER_OUTPUT_CONTRACT_VERSION,
                "strategy_contract": {
                    "scanner_id": scanner_id,
                    "dimensions_evaluated": list(SCANNER_REQUIRED_DIMENSIONS[scanner_id]),
                    "methodology_summary": "aggregated from validated scanner-specific round outputs",
                },
                "source_exhaustion": all_source_exhausted,
                "source_exhaustion_reason": "ALL_ROUNDS_CLAIM_EXHAUSTED" if all_source_exhausted else "NOT_PROVEN",
            }
            receipt = {
                "scanner_id": scanner_id,
                "scanner_name": coach.V8_SCANNERS[scanner_id]["name"],
                "source_file": coach.V8_SCANNERS[scanner_id].get("source_file"),
                "source_sha256": coach.V8_SCANNERS[scanner_id]["sha256"],
                "prompt_runtime_hash": coach.V8_SCANNERS[scanner_id].get("runtime_prompt_sha256"),
                "run_id": run.run_id,
                "round_count": len(rounds),
                "universe_input_count": len(universe),
                "unique_ticker_count": len({str(row.get('security_id') or row.get('ticker') or '') for row in universe}),
                "evaluated_count": len(universe) if all_complete else sum(item["new_unique_tickers"] for item in rounds if item["scanner_execution_validated"]),
                "signal_count": sum(item["new_signal"] for item in rounds),
                "partial_signal_count": sum(item["new_partial_signal"] for item in rounds),
                "secondary_count": sum(item["new_secondary"] for item in rounds),
                "deep_count": sum(item["new_deep_dive_now"] for item in rounds),
                "unknown_count": sum(1 for item in merged.values() if item.get("unknowns")),
                "output_schema_version": SCANNER_OUTPUT_CONTRACT_VERSION,
                "output_validated": all_complete,
                "execution_status": "SIGNAL_SCAN_COMPLETE" if all_complete else "SIGNAL_SCAN_PARTIAL",
                "failure_class": None if all_complete else "ROUND_INCOMPLETE",
                "source_exhaustion": all_source_exhausted,
                "round_receipts": round_receipts,
                "lane_touched_is_scanner_executed": False,
                "grade_authority": False,
            }
            self.store.record_funnel(run.run_id, f"V8_MAIN_SCANNER_{scanner_id}_AUTHORITATIVE_RECEIPT", len(universe), receipt)
            self._v8_integrity_state.setdefault(run.run_id, {"scanners": {}, "rounds": []})["scanners"][scanner_id] = receipt
            self._v8_integrity_state[run.run_id]["rounds"].extend(rounds)
            return aggregate

    runtime_module.ProductionStockAgent = V8MainDiscoveryIntegrityPreProductionStockAgent
    _PRE_INSTALLED = True
    return V8MainDiscoveryIntegrityPreProductionStockAgent


def _parse_result(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row.get("result_json") or "{}")
    except (AttributeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _upsert_secondary(store: Any, run_id: str, item: dict[str, Any], scanner_ids: list[str]) -> None:
    sid = str(item.get("security_id") or "").upper()
    if not sid:
        return
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expiry = (now + timedelta(days=SECONDARY_EXPIRY_DAYS)).isoformat().replace("+00:00", "Z")
    missing = list(dict.fromkeys(str(value) for value in (item.get("unknowns") or []) if str(value)))
    path = list(dict.fromkeys(str(value) for value in (item.get("verification_questions") or []) if str(value)))
    payload = {
        "security_id": sid,
        "research_value": item.get("research_value") or "UNKNOWN",
        "missing_evidence": missing,
        "verification_path": path,
        "expected_resolution": "WITHIN_1_8W_OR_NEXT_MATERIAL_DISCLOSURE",
        "recheck_trigger": "MISSING_EVIDENCE_RESOLVED_OR_NEW_MATERIAL_EVENT",
        "expiry": expiry,
        "scanner_ids": sorted(set(scanner_ids)),
        "grade_authority": False,
        "pre_a_authority": False,
        "execution_authority": False,
    }
    store.connection.execute(
        "INSERT INTO discovery_secondary_queue(security_id,originating_run_id,scanner_ids_json,research_value,missing_evidence_json,verification_path_json,expected_resolution,recheck_trigger,expiry,status,payload_json,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,'OPEN',?,?) ON CONFLICT(security_id) DO UPDATE SET "
        "originating_run_id=excluded.originating_run_id,scanner_ids_json=excluded.scanner_ids_json,research_value=excluded.research_value,"
        "missing_evidence_json=excluded.missing_evidence_json,verification_path_json=excluded.verification_path_json,"
        "expected_resolution=excluded.expected_resolution,recheck_trigger=excluded.recheck_trigger,expiry=excluded.expiry,status='OPEN',payload_json=excluded.payload_json,updated_at=excluded.updated_at",
        (sid, run_id, json.dumps(payload["scanner_ids"]), str(payload["research_value"]), json.dumps(missing), json.dumps(path), payload["expected_resolution"], payload["recheck_trigger"], expiry, json.dumps(payload, sort_keys=True), now.isoformat().replace("+00:00", "Z")),
    )


def _expire_secondary(store: Any) -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    cursor = store.connection.execute(
        "UPDATE discovery_secondary_queue SET status='EXPIRED_WATCH',updated_at=? WHERE status='OPEN' AND expiry<?",
        (now, now),
    )
    return int(cursor.rowcount or 0)


def _provider_exhaustion(store: Any, run_id: str, eligible: int) -> tuple[bool, dict[str, Any]]:
    funnel = {str(row.get("funnel_stage")): row for row in store.list_funnel(run_id)}
    def count(stage: str) -> int:
        row = funnel.get(stage) or {}
        return int(row.get("count") or 0)
    adv_probed = count("ADV_PROBED")
    adv_unknown = count("ADV_NOT_EVALUATED")
    raw = count("RAW_UNIVERSE")
    # Complete verified breadth is genuine exhaustion.  Otherwise an explicit
    # >=1000-name probe ceiling plus low marginal yield is required later.
    complete_verified = raw > 0 and adv_unknown == 0 and eligible > 0
    explicit_ceiling = adv_probed >= 1000
    return complete_verified or explicit_ceiling, {
        "raw_unique_ticker_coverage": raw,
        "strategy_eligible_unique_coverage": eligible,
        "adv_probed": adv_probed,
        "adv_not_evaluated": adv_unknown,
        "complete_verified_breadth": complete_verified,
        "explicit_operational_ceiling": explicit_ceiling,
    }


def _two_low_yield_rounds(rounds: list[dict[str, Any]]) -> bool:
    if len(rounds) < 2:
        return False
    tail = rounds[-2:]
    return all(
        int(item.get("new_signal") or 0) == 0
        and int(item.get("new_secondary") or 0) == 0
        and int(item.get("new_independent_evidence") or 0) == 0
        for item in tail
    )


def install_post_coach_discovery_integrity() -> type:
    """Install the final MAIN Discovery ledger/stop validator around coach."""
    global _POST_INSTALLED
    current = runtime_module.ProductionStockAgent
    if _POST_INSTALLED or getattr(current, "v8_main_discovery_integrity_post_version", None) == V8_MAIN_DISCOVERY_INTEGRITY_VERSION:
        return current

    class V8MainDiscoveryIntegrityPostProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_main_discovery_integrity_post_version = V8_MAIN_DISCOVERY_INTEGRITY_VERSION

        def _work_stage(self, run, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
            result = super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            if stage != "STOCK_DISCOVERY" or prompt_id != "workflow.stock_scout" or not isinstance(result, dict):
                return result
            state = getattr(self, "_v8_integrity_state", {}).get(run.run_id) or {}
            scanners = state.get("scanners") or {}
            final_by_sid = {
                str(item.get("security_id") or "").upper(): item
                for item in (result.get("candidates") or [])
                if isinstance(item, dict) and item.get("security_id")
            }
            scanner_by_sid: dict[str, list[str]] = {}
            scanner_candidates: dict[str, dict[str, Any]] = {}
            structural: set[str] = set()
            for scanner_id in sorted(scanners):
                row = self.store.get_stage_result(run.run_id, "STOCK_DISCOVERY", None)
                del row  # canonical final output is already in result; scanner payload is held in coach state below.
            # The coach injects scanner results into its local synthesis input but
            # does not persist them individually as authoritative grades.  Build
            # Secondary candidates from round stage results instead.
            for row in self.store.list_stage_results(run.run_id):
                stage_name = str(row.get("stage") or "")
                if not stage_name.startswith("V8_MAIN_SCANNER_") or "_R" not in stage_name or row.get("status") != "SUCCEEDED":
                    continue
                value = _parse_result(row)
                scanner_id = str(value.get("scanner_id") or "")
                for item in value.get("candidates") or []:
                    if not isinstance(item, dict) or not item.get("security_id"):
                        continue
                    sid = str(item["security_id"]).upper()
                    scanner_by_sid.setdefault(sid, []).append(scanner_id)
                    scanner_candidates[sid] = _merge_candidate(scanner_candidates.get(sid), item)
                    if item.get("failure_class") == "STRUCTURAL_HARD_FAIL":
                        structural.add(sid)
            secondary_ids: list[str] = []
            near_miss: list[str] = []
            for sid, item in scanner_candidates.items():
                action = str(item.get("recommended_discovery_action") or "")
                if sid in structural:
                    continue
                final_action = str((final_by_sid.get(sid) or {}).get("recommended_discovery_action") or action)
                if final_action == "DEEP_DIVE_SECONDARY" or (item.get("research_value") == "HIGH" and final_action not in {"DEEP_DIVE_NOW", "EXCLUDE"}):
                    _upsert_secondary(self.store, run.run_id, item, scanner_by_sid.get(sid, []))
                    secondary_ids.append(sid)
                if final_action in {"WATCH_STAGE0", "WATCH_RESET", "DEEP_DIVE_SECONDARY"} and sid not in structural:
                    near_miss.append(sid)
            expired = _expire_secondary(self.store)
            self.store.record_funnel(run.run_id, "DISCOVERY_SECONDARY_QUEUE", len(set(secondary_ids)), {
                "security_ids": sorted(set(secondary_ids))[:300],
                "persistent_table": "discovery_secondary_queue",
                "expired_to_watch_count": expired,
                "secondary_is_pre_a": False,
                "research_value_is_research_grade": False,
                "grade_authority": False,
            })
            self.store.record_funnel(run.run_id, "DISCOVERY_NEAR_MISS_LEDGER", len(set(near_miss)), {
                "security_ids": sorted(set(near_miss))[:300],
                "structural_hard_fail_excluded": sorted(structural)[:300],
                "grade_authority": False,
            })
            return result

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            state = getattr(self, "_v8_integrity_state", {}).get(run_id) or {}
            scanners = state.get("scanners") or {}
            rounds = list(state.get("rounds") or [])
            if not scanners:
                return outcome
            completed = {sid for sid, receipt in scanners.items() if receipt.get("execution_status") == "SIGNAL_SCAN_COMPLETE" and receipt.get("output_validated") is True}
            signal_coverage = min((int(receipt.get("evaluated_count") or 0) for receipt in scanners.values()), default=0)
            raw_state = getattr(self, "_v8_main_discovery_state", {}).get(run_id) or {}
            eligible = int(raw_state.get("strategy_eligible_unique") or signal_coverage)
            source_exhausted, universe = _provider_exhaustion(self.store, run_id, eligible)
            open_high = self.store.connection.execute(
                "SELECT COUNT(*) n FROM discovery_secondary_queue WHERE status='OPEN' AND research_value='HIGH'"
            ).fetchone()["n"]
            near_row = next((row for row in reversed(self.store.list_funnel(run_id)) if row.get("funnel_stage") == "DISCOVERY_NEAR_MISS_LEDGER"), None)
            near_count = int((near_row or {}).get("count") or 0)
            sentinel_complete = bool(raw_state.get("sentinel_complete"))
            systematic_fn = bool(raw_state.get("systematic_false_negative_risk"))
            low_tail = _two_low_yield_rounds(rounds)
            scanner_complete = completed == set(SCANNER_REQUIRED_DIMENSIONS)
            minimum_coverage = signal_coverage >= coach.V8_MAIN_MIN_UNIQUE
            stop_allowed = (
                scanner_complete
                and minimum_coverage
                and sentinel_complete
                and not systematic_fn
                and int(open_high) == 0
                and near_count == 0
                and low_tail
                and source_exhausted
            )
            audit = {
                **universe,
                "scanner_signal_coverage": signal_coverage,
                "mandatory_scanners": sorted(SCANNER_REQUIRED_DIMENSIONS),
                "scanner_execution_complete": scanner_complete,
                "scanner_completed": sorted(completed),
                "minimum_signal_coverage_met": minimum_coverage,
                "open_high_research_value_secondary": int(open_high),
                "unresolved_near_miss": near_count,
                "rejection_sentinel_complete": sentinel_complete,
                "systematic_false_negative_risk": systematic_fn,
                "two_consecutive_low_yield_rounds": low_tail,
                "source_or_budget_exhaustion_documented": source_exhausted,
                "deep_dive_yield_zero_alone_proves_exhaustion": False,
                "search_stop_allowed": stop_allowed,
                "reason": "MAIN_V8_FORENSIC_STOP_COMPLETE" if stop_allowed else "MAIN_V8_SEARCH_DEBT_REMAINS",
                "grade_authority": False,
                "version": V8_MAIN_DISCOVERY_INTEGRITY_VERSION,
            }
            self.store.record_funnel(run_id, "V8_MAIN_FORENSIC_SEARCH_STOP_AUDIT", signal_coverage, audit)
            current_outcome = str(getattr(outcome, "outcome", "") or "")
            if not stop_allowed and current_outcome in {"NO_QUALIFIED_CANDIDATE", "NO_TRADE", "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT", "NOT_EVALUABLE_DISCOVERY_COVERAGE", "QUALIFIED_CANDIDATE_POOL"}:
                terminal = "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT"
                reason = (
                    f"scanner_complete={scanner_complete}; signal_coverage={signal_coverage}; sentinel={sentinel_complete}; "
                    f"open_high_secondary={int(open_high)}; near_miss={near_count}; low_tail={low_tail}; source_exhausted={source_exhausted}"
                )
                with self.store.transaction() as db:
                    db.execute("UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?", (terminal, run_id))
                return replace(outcome, outcome=terminal, blocked_reason=reason)
            return outcome

    runtime_module.ProductionStockAgent = V8MainDiscoveryIntegrityPostProductionStockAgent
    _POST_INSTALLED = True
    return V8MainDiscoveryIntegrityPostProductionStockAgent


_ORIGINAL_SCANNER_SCHEMA = coach._scanner_schema
_ORIGINAL_DEFAULT_SCANNER = coach._default_scanner
