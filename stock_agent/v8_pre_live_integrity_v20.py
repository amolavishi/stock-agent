"""Pre-live integrity hardening for V8 MAIN / V8 NEXT.

This layer adds only fail-closed validation.  It does not create candidates,
Research Grade, PRE-A authority, execution actions, position sizes, or broker
writes.

Defects addressed before live validation:
- Secondary expiry could be extended forever by repeated rediscovery.
- Aggregator/research-bundle children could be over-counted as independent
  evidence origins merely because their text differed.
- Search-stop source exhaustion could be inferred from a small fully-probed
  subset instead of an explicit operational ceiling.
- scanner screened_count did not prove that every supplied security was
  actually assessed; omitted names were also invisible to rejection sentinel.
- multiple verified claims could reuse one Python origin while still leaving
  evidence_independence=PASS.
- a present-but-empty / NOT_EVALUATED technical snapshot could bypass explicit
  Discovery evidence-debt conservation.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from . import runtime as runtime_module
from . import v8_evidence_origin_v19 as origin
from . import v8_main_discovery_coach as coach
from . import v8_main_discovery_integrity as integrity
from . import v8_main_discovery_post_v11 as post
from . import v8_main_recall_conservation as recall
from . import v8_main_source_fidelity as source_fidelity
from . import v8_next_runtime as next_runtime
from .models import RunMode, RunOutcome, canonical_hash, utc_now

V8_PRE_LIVE_INTEGRITY_VERSION = "V8_PRE_LIVE_INTEGRITY_V2.0"
SCANNER_OUTPUT_CONTRACT_VERSION = "V8_MAIN_SCANNER_OUTPUT_V1.3"
MIN_SENTINEL_SAMPLE = 15
MIN_OPERATIONAL_PROBE = 1000
_INSTALLED = False

_COVERAGE_DISPOSITIONS = {
    "RETAINED",
    "NO_SIGNAL",
    "WATCH",
    "EXCLUDE",
    "DATA_BLOCK",
}
_FAILURE_CLASSES = {
    "NONE",
    "STRUCTURAL_HARD_FAIL",
    "THESIS_HARD_FAIL",
    "DISCOVERY_INSUFFICIENT",
    "TIME_HORIZON_MISMATCH",
    "PRICE_STAGE_MISMATCH",
    "DATA_INTEGRITY_BLOCK",
}


def _scanner_schema_v13() -> dict[str, Any]:
    schema = copy.deepcopy(integrity._integrity_scanner_schema())
    candidate = schema["properties"]["candidates"]["items"]
    candidate["properties"].update({
        "recheck_trigger_fired": {"type": "boolean"},
        "recheck_trigger_evidence_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
    })
    coverage_row = {
        "type": "object",
        "properties": {
            "security_id": {"type": "string", "minLength": 1},
            "disposition": {"type": "string", "enum": sorted(_COVERAGE_DISPOSITIONS)},
            "failure_class": {"type": "string", "enum": sorted(_FAILURE_CLASSES)},
            "signal_strength": {"type": "string", "enum": ["STRONG", "MODERATE", "WEAK", "NONE", "UNKNOWN"]},
            "research_value": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"]},
            "cheap_hard_gate_status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "evidence_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "rationale": {"type": "string"},
        },
        "required": [
            "security_id", "disposition", "failure_class", "signal_strength",
            "research_value", "cheap_hard_gate_status", "evidence_ids", "rationale",
        ],
        "additionalProperties": False,
    }
    schema["properties"]["coverage_ledger"] = {
        "type": "array",
        "items": coverage_row,
        "uniqueItems": True,
    }
    if "coverage_ledger" not in schema["required"]:
        schema["required"].append("coverage_ledger")
    schema["properties"]["output_contract_version"] = {"const": SCANNER_OUTPUT_CONTRACT_VERSION}
    return schema


def _default_scanner_v13(scanner_id: str, screened_count: int) -> dict[str, Any]:
    value = integrity._integrity_default_scanner(scanner_id, screened_count)
    value["coverage_ledger"] = []
    value["output_contract_version"] = SCANNER_OUTPUT_CONTRACT_VERSION
    return value


def _contract_complete_v20(scanner_id: str, result: dict[str, Any], expected_count: int) -> tuple[bool, list[str]]:
    # Preserve every existing source/strategy/candidate validation.
    original_version = result.get("output_contract_version")
    check = dict(result)
    check["output_contract_version"] = integrity.SCANNER_OUTPUT_CONTRACT_VERSION
    complete, failures = _BASE_CONTRACT_COMPLETE(scanner_id, check, expected_count)
    if original_version != SCANNER_OUTPUT_CONTRACT_VERSION:
        failures.append("OUTPUT_CONTRACT_VERSION_V13")
    ledger = [item for item in (result.get("coverage_ledger") or []) if isinstance(item, dict)]
    ids = [str(item.get("security_id") or "").upper() for item in ledger]
    if len(ledger) != expected_count:
        failures.append(f"COVERAGE_LEDGER_COUNT:{len(ledger)}!={expected_count}")
    if len(ids) != len(set(ids)) or any(not sid for sid in ids):
        failures.append("COVERAGE_LEDGER_IDS_DUPLICATE_OR_MISSING")
    for item in ledger:
        if str(item.get("disposition") or "") not in _COVERAGE_DISPOSITIONS:
            failures.append(f"COVERAGE_LEDGER_DISPOSITION:{item.get('security_id')}")
        if str(item.get("failure_class") or "") not in _FAILURE_CLASSES:
            failures.append(f"COVERAGE_LEDGER_FAILURE_CLASS:{item.get('security_id')}")
    return complete and not failures, sorted(set(failures))


def _round_metrics_v20(scanner_id: str, round_id: str, chunk: list[dict[str, Any]], result: dict[str, Any], cumulative: int, prior_evidence: set[str]):
    metrics, evidence = _BASE_ROUND_METRICS(scanner_id, round_id, chunk, result, cumulative, prior_evidence)
    candidates = [item for item in (result.get("candidates") or []) if isinstance(item, dict)]
    new_evidence = sorted(evidence - set(prior_evidence))
    metrics.update({
        "new_independent_evidence_ids": new_evidence,
        "new_signal_security_ids": sorted({
            str(item.get("security_id") or "").upper() for item in candidates
            if str(item.get("signal_strength") or "") in {"STRONG", "MODERATE", "WEAK"} and item.get("security_id")
        }),
        "new_secondary_security_ids": sorted({
            str(item.get("security_id") or "").upper() for item in candidates
            if item.get("recommended_discovery_action") == "DEEP_DIVE_SECONDARY" and item.get("security_id")
        }),
        "new_deep_dive_security_ids": sorted({
            str(item.get("security_id") or "").upper() for item in candidates
            if item.get("recommended_discovery_action") == "DEEP_DIVE_NOW" and item.get("security_id")
        }),
        "new_high_research_value_security_ids": sorted({
            str(item.get("security_id") or "").upper() for item in candidates
            if item.get("research_value") == "HIGH" and item.get("security_id")
        }),
    })
    return metrics, evidence


def _system_rounds_v20(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for item in rounds:
        if not isinstance(item, dict):
            continue
        round_id = str(item.get("round_id") or "")
        if "-R" not in round_id:
            continue
        try:
            sequence = int(round_id.rsplit("-R", 1)[1])
        except ValueError:
            continue
        row = grouped.setdefault(sequence, {
            "round_sequence": sequence,
            "scanner_ids": set(),
            "new_unique_tickers": 0,
            "signal_ids": set(),
            "secondary_ids": set(),
            "high_ids": set(),
            "evidence_ids": set(),
            "deep_ids": set(),
        })
        row["scanner_ids"].add(str(item.get("scanner_id") or ""))
        row["new_unique_tickers"] = max(int(row["new_unique_tickers"]), int(item.get("new_unique_tickers") or 0))
        for key, source_key in (
            ("signal_ids", "new_signal_security_ids"),
            ("secondary_ids", "new_secondary_security_ids"),
            ("high_ids", "new_high_research_value_security_ids"),
            ("evidence_ids", "new_independent_evidence_ids"),
            ("deep_ids", "new_deep_dive_security_ids"),
        ):
            row[key].update(str(value) for value in (item.get(source_key) or []) if str(value))
    result: list[dict[str, Any]] = []
    cumulative = 0
    for sequence in sorted(grouped):
        row = grouped[sequence]
        cumulative += int(row["new_unique_tickers"])
        complete = row["scanner_ids"] == set(integrity.SCANNER_REQUIRED_DIMENSIONS)
        unique = max(1, int(row["new_unique_tickers"]))
        result.append({
            "round_sequence": sequence,
            "scanner_ids": sorted(row["scanner_ids"]),
            "scanner_family_complete": complete,
            "new_unique_tickers": int(row["new_unique_tickers"]),
            "cumulative_unique_tickers": cumulative,
            "new_signal": len(row["signal_ids"]),
            "new_secondary": len(row["secondary_ids"]),
            "new_high_research_value": len(row["high_ids"]),
            "new_independent_evidence": len(row["evidence_ids"]),
            "new_deep_dive_now": len(row["deep_ids"]),
            "new_signal_security_ids": sorted(row["signal_ids"]),
            "new_secondary_security_ids": sorted(row["secondary_ids"]),
            "new_independent_evidence_ids": sorted(row["evidence_ids"]),
            "marginal_candidate_yield": (len(row["deep_ids"]) + len(row["secondary_ids"])) / unique,
            "marginal_evidence_yield": len(row["evidence_ids"]) / unique,
        })
    return result


def _provider_exhaustion_v20(store: Any, run_id: str, eligible: int) -> tuple[bool, dict[str, Any]]:
    funnel = {str(row.get("funnel_stage")): row for row in store.list_funnel(run_id)}
    def count(stage: str) -> int:
        row = funnel.get(stage) or {}
        return int(row.get("count") or 0)
    raw = count("RAW_UNIVERSE")
    adv_probed = count("ADV_PROBED")
    adv_unknown = count("ADV_NOT_EVALUATED")
    explicit_ceiling = adv_probed >= MIN_OPERATIONAL_PROBE
    exhausted = bool(explicit_ceiling)
    return exhausted, {
        "raw_unique_ticker_coverage": raw,
        "strategy_eligible_unique_coverage": int(eligible),
        "adv_probed": adv_probed,
        "adv_not_evaluated": adv_unknown,
        "complete_verified_breadth": bool(adv_unknown == 0 and adv_probed >= raw and raw >= MIN_OPERATIONAL_PROBE),
        "explicit_operational_ceiling": explicit_ceiling,
        "minimum_operational_probe": MIN_OPERATIONAL_PROBE,
        "small_fully_probed_subset_is_source_exhaustion": False,
    }


def _secondary_payload(item: dict[str, Any], scanner_ids: list[str], expiry: str, first_seen: str) -> dict[str, Any]:
    missing = list(dict.fromkeys(str(value) for value in (item.get("unknowns") or []) if str(value)))
    path = list(dict.fromkeys(str(value) for value in (item.get("verification_questions") or []) if str(value)))
    evidence_ids = sorted({
        str(eid) for evidence in (item.get("strategy_evidence") or []) if isinstance(evidence, dict)
        for eid in (evidence.get("evidence_ids") or []) if str(eid)
    })
    return {
        "security_id": str(item.get("security_id") or "").upper(),
        "research_value": item.get("research_value") or "UNKNOWN",
        "missing_evidence": missing,
        "verification_path": path,
        "expected_resolution": "WITHIN_1_8W_OR_NEXT_MATERIAL_DISCLOSURE",
        "recheck_trigger": "MISSING_EVIDENCE_RESOLVED_OR_NEW_MATERIAL_EVENT",
        "recheck_trigger_fired": bool(item.get("recheck_trigger_fired")),
        "recheck_trigger_evidence_ids": sorted(set(str(x) for x in (item.get("recheck_trigger_evidence_ids") or []) if str(x))),
        "strategy_evidence_ids": evidence_ids,
        "first_seen_at": first_seen,
        "expiry": expiry,
        "scanner_ids": sorted(set(str(x) for x in scanner_ids if str(x))),
        "grade_authority": False,
        "pre_a_authority": False,
        "execution_authority": False,
    }


def _upsert_secondary_v20(store: Any, run_id: str, item: dict[str, Any], scanner_ids: list[str]) -> None:
    sid = str(item.get("security_id") or "").upper()
    if not sid:
        return
    now_dt = datetime.now(timezone.utc).replace(microsecond=0)
    now = now_dt.isoformat().replace("+00:00", "Z")
    row = store.connection.execute(
        "SELECT status,expiry,payload_json FROM discovery_secondary_queue WHERE security_id=?",
        (sid,),
    ).fetchone()
    existing_status = str(row["status"]) if row else ""
    existing_expiry = str(row["expiry"]) if row else ""
    old_payload: dict[str, Any] = {}
    if row:
        try:
            old_payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            old_payload = {}

    # An OPEN queue item keeps its original cycle expiry. Repeated discovery is
    # not a material event and may not move the expiry window forward.
    if existing_status == "OPEN" and existing_expiry:
        if existing_expiry <= now:
            store.connection.execute(
                "UPDATE discovery_secondary_queue SET status='EXPIRED_WATCH',updated_at=? WHERE security_id=?",
                (now, sid),
            )
            existing_status = "EXPIRED_WATCH"
        else:
            expiry = existing_expiry
            first_seen = str(old_payload.get("first_seen_at") or now)
            payload = _secondary_payload(item, scanner_ids, expiry, first_seen)
            missing = payload["missing_evidence"]
            path = payload["verification_path"]
            store.connection.execute(
                "UPDATE discovery_secondary_queue SET originating_run_id=?,scanner_ids_json=?,research_value=?,missing_evidence_json=?,verification_path_json=?,expected_resolution=?,recheck_trigger=?,payload_json=?,updated_at=? WHERE security_id=?",
                (run_id, json.dumps(payload["scanner_ids"]), str(payload["research_value"]), json.dumps(missing), json.dumps(path), payload["expected_resolution"], payload["recheck_trigger"], json.dumps(payload, sort_keys=True), now, sid),
            )
            return

    # Closed/expired items may start a new queue cycle only if a scanner
    # explicitly declares that the recheck trigger fired and cites evidence.
    if row and existing_status != "OPEN":
        trigger_ids = [str(x) for x in (item.get("recheck_trigger_evidence_ids") or []) if str(x)]
        if not bool(item.get("recheck_trigger_fired")) or not trigger_ids:
            return

    expiry = (now_dt + timedelta(days=integrity.SECONDARY_EXPIRY_DAYS)).isoformat().replace("+00:00", "Z")
    payload = _secondary_payload(item, scanner_ids, expiry, now)
    missing = payload["missing_evidence"]
    path = payload["verification_path"]
    store.connection.execute(
        "INSERT INTO discovery_secondary_queue(security_id,originating_run_id,scanner_ids_json,research_value,missing_evidence_json,verification_path_json,expected_resolution,recheck_trigger,expiry,status,payload_json,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,'OPEN',?,?) ON CONFLICT(security_id) DO UPDATE SET "
        "originating_run_id=excluded.originating_run_id,scanner_ids_json=excluded.scanner_ids_json,research_value=excluded.research_value,"
        "missing_evidence_json=excluded.missing_evidence_json,verification_path_json=excluded.verification_path_json,"
        "expected_resolution=excluded.expected_resolution,recheck_trigger=excluded.recheck_trigger,expiry=excluded.expiry,status='OPEN',payload_json=excluded.payload_json,updated_at=excluded.updated_at",
        (sid, run_id, json.dumps(payload["scanner_ids"]), str(payload["research_value"]), json.dumps(missing), json.dumps(path), payload["expected_resolution"], payload["recheck_trigger"], expiry, json.dumps(payload, sort_keys=True), now),
    )


def _source_lineage_v20(source: dict[str, Any], parent_artifact_id: str) -> tuple[str, str]:
    source_class = str(source.get("source_class") or "UNKNOWN").upper()
    origin_artifact = str(source.get("origin_artifact_id") or parent_artifact_id)
    content = origin._normalized_text(source.get("content") or source.get("document") or source.get("text") or source.get("body"))
    title = origin._normalized_text(source.get("title"))
    content_lineage_hash = canonical_hash({"title": title, "content": content})
    # Conservative family: inside one aggregate/research artifact, differing
    # text alone does NOT manufacture independent origins.  source_class keeps
    # SEC/IR/news classes distinct while the parent/origin artifact dominates.
    independent_origin_id = "ORIGIN-" + canonical_hash({
        "origin_artifact_id": origin_artifact,
        "source_class": source_class,
    })[:24]
    return independent_origin_id, content_lineage_hash


def _finalize_atomic_audit_v20(draft: dict[str, Any], evidence_ids: list[str]) -> dict[str, Any]:
    value = _BASE_ATOMIC_FINALIZER(draft, evidence_ids)
    mapping = origin._ORIGIN_CONTEXT.get()
    claims = [item for item in (value.get("atomic_claims") or []) if isinstance(item, dict)]
    failures = list(value.get("validation_failures") or [])
    all_origins: set[str] = set()
    signature_to_groups: dict[tuple[str, ...], set[str]] = {}
    for claim in claims:
        cited = [str(eid) for eid in (claim.get("evidence_ids") or [])]
        signature = tuple(sorted({mapping[eid] for eid in cited if eid in mapping}))
        all_origins.update(signature)
        group = str(claim.get("independent_evidence_group") or "")
        if signature:
            signature_to_groups.setdefault(signature, set()).add(group)
    if len(claims) >= 2 and len(all_origins) < 2:
        failures.append("SINGLE_ORIGIN_CANNOT_PROVE_MULTI_CLAIM_INDEPENDENCE")
    for signature, groups in signature_to_groups.items():
        nonempty = {group for group in groups if group}
        if len(nonempty) > 1:
            failures.append("FALSE_INDEPENDENT_GROUP_SPLIT:" + canonical_hash(signature)[:12])
    if failures:
        value["status"] = "INCOMPLETE"
        value["evidence_independence"] = "FAIL"
    value["validation_failures"] = sorted(set(failures))
    value["python_independent_origin_count"] = len(all_origins)
    value["python_origin_diversity_validation"] = "PASS" if not failures else "FAIL"
    value["evidence_origin_version"] = V8_PRE_LIVE_INTEGRITY_VERSION
    return value


def _technical_receipt_usable(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    status = str(value.get("evaluation_status") or value.get("status") or "").upper()
    if status.startswith("NOT_EVALUATED") or status in {"UNKNOWN", "FAILED", "ERROR", "DATA_BLOCKED", "INCOMPLETE"}:
        return False
    for key, item in value.items():
        if key in {"timestamp", "observed_at", "source_observed_at", "retrieved_at"}:
            continue
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)):
            return True
    return status in {"PASS", "COMPLETE", "VALID"}


def _harden_pre_scanner_executor() -> None:
    target = next((cls for cls in runtime_module.ProductionStockAgent.__mro__ if cls.__name__ == "V8MainDiscoveryIntegrityPreProductionStockAgent"), None)
    if target is None or getattr(target, "_v20_coverage_hardened", False):
        return
    base = target._work_stage

    def wrapped(self: Any, run: Any, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None):
        result = base(self, run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
        if not stage.startswith("V8_MAIN_SCANNER_") or "_R" in stage:
            return result
        scanner_id = stage.rsplit("_", 1)[-1]
        if scanner_id not in integrity.SCANNER_REQUIRED_DIMENSIONS:
            return result
        raw = copy.deepcopy(payload.get("raw_input") or {}) if isinstance(payload, dict) else {}
        universe = [row for row in (raw.get("candidate_universe_packet") or []) if isinstance(row, dict)]
        chunks = [universe[index:index + integrity.SCANNER_ROUND_SIZE] for index in range(0, len(universe), integrity.SCANNER_ROUND_SIZE)] or [[]]
        failures: list[str] = []
        for index, chunk in enumerate(chunks, 1):
            round_stage = f"V8_MAIN_SCANNER_{scanner_id}_R{index:03d}"
            row = self.store.get_stage_result(run.run_id, round_stage, None)
            parsed = integrity._parse_result(row or {})
            ledger = [item for item in (parsed.get("coverage_ledger") or []) if isinstance(item, dict)]
            expected = [str(item.get("security_id") or item.get("ticker") or "").upper() for item in chunk]
            actual = [str(item.get("security_id") or "").upper() for item in ledger]
            if len(actual) != len(expected) or set(actual) != set(expected) or len(actual) != len(set(actual)):
                failures.append(f"{round_stage}:COVERAGE_SET_MISMATCH")
        state = getattr(self, "_v8_integrity_state", {}).get(run.run_id) or {}
        receipt = (state.get("scanners") or {}).get(scanner_id)
        if failures:
            if isinstance(receipt, dict):
                receipt["execution_status"] = "SIGNAL_SCAN_PARTIAL"
                receipt["output_validated"] = False
                receipt["failure_class"] = "COVERAGE_LEDGER_INCOMPLETE"
                receipt["coverage_ledger_failures"] = failures
            updated = dict(result) if isinstance(result, dict) else {}
            updated["execution_status"] = "PARTIAL"
            updated["coverage_ledger_validated"] = False
            updated["coverage_ledger_failures"] = failures
            self.store.record_funnel(run.run_id, f"V8_MAIN_SCANNER_{scanner_id}_COVERAGE_LEDGER_VALIDATION", len(universe), {
                "status": "FAIL", "failures": failures, "grade_authority": False,
            })
            return updated
        if isinstance(receipt, dict):
            receipt["coverage_ledger_validated"] = True
        updated = dict(result) if isinstance(result, dict) else {}
        updated["coverage_ledger_validated"] = True
        self.store.record_funnel(run.run_id, f"V8_MAIN_SCANNER_{scanner_id}_COVERAGE_LEDGER_VALIDATION", len(universe), {
            "status": "PASS", "expected_count": len(universe), "grade_authority": False,
        })
        return updated

    target._work_stage = wrapped  # type: ignore[assignment]
    target._v20_coverage_hardened = True


def _sentinel_sample_v20(results: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        scanner_id = str(result.get("scanner_id") or "")
        candidate_by_sid = {
            str(item.get("security_id") or "").upper(): item
            for item in (result.get("candidates") or []) if isinstance(item, dict) and item.get("security_id")
        }
        for item in result.get("coverage_ledger") or []:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("security_id") or "").upper()
            disposition = str(item.get("disposition") or "")
            if not sid or disposition == "RETAINED":
                continue
            candidate = candidate_by_sid.get(sid) or {}
            row = {
                "scanner_id": scanner_id,
                "security_id": sid,
                "coverage_disposition": disposition,
                "failure_class": item.get("failure_class"),
                "signal_strength": item.get("signal_strength"),
                "research_value": item.get("research_value"),
                "cheap_hard_gate_status": item.get("cheap_hard_gate_status"),
                "evidence_ids": list(item.get("evidence_ids") or []),
                "rationale": item.get("rationale"),
                "recommended_discovery_action": candidate.get("recommended_discovery_action", "EXCLUDE" if disposition == "EXCLUDE" else "WATCH_STAGE0"),
            }
            buckets.setdefault(disposition, []).append(row)
    sample: list[dict[str, Any]] = []
    order = ["NO_SIGNAL", "WATCH", "EXCLUDE", "DATA_BLOCK"]
    per_bucket = max(1, limit // max(1, len(order)))
    for key in order:
        values = sorted(buckets.get(key, []), key=lambda item: canonical_hash({"sid": item["security_id"], "scanner": item["scanner_id"], "bucket": key}))
        sample.extend(values[:per_bucket])
    used = {(item["security_id"], item["scanner_id"]) for item in sample}
    remaining = [item for values in buckets.values() for item in values if (item["security_id"], item["scanner_id"]) not in used]
    remaining.sort(key=lambda item: canonical_hash({"sid": item["security_id"], "scanner": item["scanner_id"], "fill": True}))
    sample.extend(remaining[:max(0, limit - len(sample))])
    return sample[:limit]


def install_v8_pre_live_integrity_v20() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_pre_live_integrity_version", None) == V8_PRE_LIVE_INTEGRITY_VERSION:
        return current

    # Scanner runtime schema / exact assessment proof.
    coach._scanner_schema = _scanner_schema_v13  # type: ignore[assignment]
    integrity._integrity_default_scanner = _default_scanner_v13  # type: ignore[assignment]
    integrity._contract_complete = _contract_complete_v20  # type: ignore[assignment]
    integrity._round_metrics = _round_metrics_v20  # type: ignore[assignment]
    integrity.SCANNER_OUTPUT_CONTRACT_VERSION = SCANNER_OUTPUT_CONTRACT_VERSION
    coach._sentinel_sample = _sentinel_sample_v20  # type: ignore[assignment]
    post._system_rounds = _system_rounds_v20  # type: ignore[assignment]
    post._two_complete_low_yield_system_rounds = lambda rounds: (
        len([row for row in _system_rounds_v20(rounds) if row.get("scanner_family_complete")]) >= 2
        and all(
            int(row.get("new_signal") or 0) == 0
            and int(row.get("new_secondary") or 0) == 0
            and int(row.get("new_independent_evidence") or 0) == 0
            for row in [row for row in _system_rounds_v20(rounds) if row.get("scanner_family_complete")][-2:]
        )
    )  # type: ignore[assignment]
    _harden_pre_scanner_executor()

    # Do not allow a small fully-probed subset to masquerade as source exhaustion.
    integrity._provider_exhaustion = _provider_exhaustion_v20  # type: ignore[assignment]
    post._provider_exhaustion = _provider_exhaustion_v20  # type: ignore[assignment]

    # Secondary queue lifecycle is monotonic within one queue cycle.
    integrity._upsert_secondary = _upsert_secondary_v20  # type: ignore[assignment]
    post._upsert_secondary = _upsert_secondary_v20  # type: ignore[assignment]

    # Source family and atomic independence must be Python-enforced.
    origin._source_lineage = _source_lineage_v20  # type: ignore[assignment]
    next_runtime._finalize_atomic_audit = _finalize_atomic_audit_v20  # type: ignore[assignment]

    if "COVERAGE_LEDGER_RUNTIME_CONTRACT_V13" not in source_fidelity._RUNTIME_ADDENDUM:
        source_fidelity._RUNTIME_ADDENDUM += r"""

# COVERAGE_LEDGER_RUNTIME_CONTRACT_V13
For every security in candidate_universe_packet return exactly one coverage_ledger row.
The coverage_ledger security_id set must exactly equal the input security_id set; no omission,
duplicate, or invented ticker is allowed. A ticker not retained as a candidate still requires a
NO_SIGNAL/WATCH/EXCLUDE/DATA_BLOCK row with failure_class, signal_strength, research_value,
cheap_hard_gate_status, evidence_ids, and rationale. `screened_count` alone is not proof of
execution. Set `source_exhaustion=true` only when this scanner has no unresolved expansion query
within the supplied operational search budget. Repeated rediscovery does not reset Secondary
expiry. `recheck_trigger_fired=true` is allowed only after a new material event or previously
missing decision-critical evidence is actually resolved, and must include recheck_trigger_evidence_ids.
"""

    class V8PreLiveIntegrityProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_pre_live_integrity_version = V8_PRE_LIVE_INTEGRITY_VERSION

        def _work_stage(self, run: Any, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None):
            result = super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            if stage != "STOCK_DISCOVERY" or prompt_id != "workflow.stock_scout" or not isinstance(result, dict):
                return result
            raw = payload.get("raw_input") if isinstance(payload, dict) else {}
            technical = (raw or {}).get("technical_features") if isinstance(raw, dict) else {}
            technical = technical if isinstance(technical, dict) else {}
            candidates = [copy.deepcopy(item) for item in (result.get("candidates") or []) if isinstance(item, dict)]
            debt_ids: set[str] = set()
            for item in candidates:
                sid = str(item.get("security_id") or "").upper()
                action = str(item.get("recommended_discovery_action") or "")
                if not sid or action not in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY"}:
                    continue
                if _technical_receipt_usable(technical.get(sid)):
                    continue
                debt_ids.add(sid)
                recall._record_debt(self, run, sid, action)
                item["recommended_discovery_action"] = "DEEP_DIVE_SECONDARY"
                rationale = str(item.get("rationale") or "").strip()
                suffix = "technical evidence missing/unusable; retained as explicit evidence debt"
                item["rationale"] = (rationale + " | " if rationale else "") + suffix
            if debt_ids:
                updated = dict(result)
                updated["candidates"] = candidates
                self.store.record_funnel(run.run_id, "DISCOVERY_TECHNICAL_UNUSABLE_EVIDENCE_DEBT", len(debt_ids), {
                    "security_ids": sorted(debt_ids)[:300],
                    "key_presence_alone_counts_as_valid_technical_evidence": False,
                    "version": V8_PRE_LIVE_INTEGRITY_VERSION,
                    "grade_authority": False,
                })
                return updated
            return result

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            state = getattr(self, "_v8_integrity_state", {}).get(run_id) or {}
            scanners = state.get("scanners") or {}
            if not scanners:
                return outcome
            scanner_source_exhausted = (
                set(scanners) == set(integrity.SCANNER_REQUIRED_DIMENSIONS)
                and all(receipt.get("source_exhaustion") is True for receipt in scanners.values())
            )
            coverage_complete = (
                set(scanners) == set(integrity.SCANNER_REQUIRED_DIMENSIONS)
                and all(receipt.get("coverage_ledger_validated") is True for receipt in scanners.values())
            )
            sentinel_sample = 0
            for row in reversed(self.store.list_funnel(run_id)):
                if str(row.get("funnel_stage") or "") != "V8_MAIN_REJECTION_SENTINEL":
                    continue
                try:
                    details = json.loads(row.get("details_json") or "{}")
                except (TypeError, ValueError):
                    details = {}
                sentinel_sample = int(details.get("sample_size") or 0) if isinstance(details, dict) else 0
                break
            sentinel_sample_sufficient = sentinel_sample >= MIN_SENTINEL_SAMPLE
            current_outcome = str(getattr(outcome, "outcome", "") or "")
            self.store.record_funnel(run_id, "V8_PRE_LIVE_INTEGRITY_AUDIT", len(scanners), {
                "scanner_source_exhaustion_complete": scanner_source_exhausted,
                "scanner_coverage_ledger_complete": coverage_complete,
                "sentinel_sample_size": sentinel_sample,
                "sentinel_sample_minimum": MIN_SENTINEL_SAMPLE,
                "sentinel_sample_sufficient": sentinel_sample_sufficient,
                "small_fully_probed_subset_is_source_exhaustion": False,
                "version": V8_PRE_LIVE_INTEGRITY_VERSION,
                "grade_authority": False,
            })
            if current_outcome in {
                "NO_QUALIFIED_CANDIDATE", "NO_TRADE", "QUALIFIED_CANDIDATE_POOL",
                "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT", "NOT_EVALUABLE_DISCOVERY_COVERAGE",
            } and not (scanner_source_exhausted and coverage_complete and sentinel_sample_sufficient):
                terminal = "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT"
                reason = (
                    f"pre-live integrity debt: scanner_source_exhausted={scanner_source_exhausted}; "
                    f"coverage_ledger={coverage_complete}; sentinel_sample={sentinel_sample}"
                )
                with self.store.transaction() as db:
                    db.execute("UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?", (terminal, run_id))
                return replace(outcome, outcome=terminal, blocked_reason=reason)
            return outcome

    runtime_module.ProductionStockAgent = V8PreLiveIntegrityProductionStockAgent
    _INSTALLED = True
    return V8PreLiveIntegrityProductionStockAgent


_BASE_CONTRACT_COMPLETE = integrity._contract_complete
_BASE_ROUND_METRICS = integrity._round_metrics
_BASE_ATOMIC_FINALIZER = next_runtime._finalize_atomic_audit
