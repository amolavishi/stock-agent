"""Deterministic catalyst eligibility gate for 1-8 week HUNT research.

The LLM may analyze a catalyst only after this module proves that at least one
raw research observation has source provenance, a concrete event window, and
quantified economic transmission.  This module does not estimate valuation,
probability, an execution action, or position size.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any
from urllib.parse import urlparse

from .gates import age_seconds
from .models import EffectiveRuleSet, GateDecision, canonical_hash, utc_now


CATALYST_GATE_VERSION = "catalyst-gate-v1"
VERIFIED_STATES = {"CONFIRMED", "VERIFIED", "OFFICIAL"}
NON_BINDING_EVENT_TYPES = {"MOU", "MEMORANDUM_OF_UNDERSTANDING", "LOI", "LETTER_OF_INTENT"}
NUMERIC_TRANSMISSION_KEYS = (
    "magnitude", "value", "amount", "percent", "percentage", "bps",
    "expected_change", "revenue", "ebitda", "fcf", "eps", "per_share",
)


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _numeric_transmission(transmission: Any) -> tuple[bool, str | None, float | None]:
    if not isinstance(transmission, dict):
        return False, None, None
    metric = transmission.get("metric") or transmission.get("driver") or transmission.get("mechanism")
    direction = str(transmission.get("direction") or "").upper()
    if not isinstance(metric, str) or not metric.strip() or direction in {"", "UNKNOWN", "NONE"}:
        return False, None, None
    for key in NUMERIC_TRANSMISSION_KEYS:
        raw = transmission.get(key)
        if isinstance(raw, bool):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number != 0:
            return True, key, number
    return False, None, None


def _candidate_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = [payload]
    source = payload.get("source")
    if isinstance(source, dict):
        candidates.append(source)
    provider_payload = payload.get("provider_payload")
    if isinstance(provider_payload, dict):
        candidates.append(provider_payload)
        evidence = provider_payload.get("evidence")
        if isinstance(evidence, dict):
            candidates.append(evidence)
    return candidates


def extract_catalyst_packet(
    payload: dict[str, Any],
    *,
    artifact_id: str,
    evidence_id: str,
    fallback_source_observed_at: str | None = None,
) -> dict[str, Any]:
    """Normalize catalyst observations without inventing missing fields."""
    catalysts: list[dict[str, Any]] = []
    root_url = payload.get("source_url")
    root_time = payload.get("source_observed_at") or payload.get("observed_at") or fallback_source_observed_at
    for source in _candidate_sources(payload):
        source_url = source.get("source_url") or source.get("url") or source.get("canonical_url") or root_url
        source_time = source.get("source_observed_at") or source.get("published_at") or source.get("observed_at") or source.get("as_of") or root_time
        raw_items = source.get("catalysts")
        if not isinstance(raw_items, list):
            continue
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            catalysts.append({
                "catalyst_id": str(raw.get("catalyst_id") or raw.get("id") or f"CATALYST-{index + 1}"),
                "event_type": str(raw.get("event_type") or raw.get("type") or "UNKNOWN").upper(),
                "event_at": raw.get("event_at") or raw.get("event_date") or raw.get("expected_date") or raw.get("date"),
                "verification_status": str(raw.get("verification_status") or raw.get("verification") or raw.get("status") or "UNKNOWN").upper(),
                "binding_status": str(raw.get("binding_status") or raw.get("binding") or "NOT_APPLICABLE").upper(),
                "economic_transmission": raw.get("economic_transmission") or raw.get("economic_impact") or {},
                "confirmation_metric": raw.get("confirmation_metric") or raw.get("confirmation") or raw.get("observable_metric"),
                "source_url": raw.get("source_url") or raw.get("url") or source_url,
                "source_observed_at": raw.get("source_observed_at") or raw.get("published_at") or source_time,
                "artifact_id": artifact_id,
                "evidence_id": evidence_id,
            })
        if catalysts:
            break
    return {
        "version": CATALYST_GATE_VERSION,
        "artifact_id": artifact_id,
        "evidence_id": evidence_id,
        "catalysts": catalysts,
    }


@dataclass(frozen=True)
class CatalystGateReceipt:
    gate_type: str
    decision: GateDecision
    input_hash: str
    rule_set_hash: str
    evaluated_at: str
    receipt_hash: str
    core_input_complete: bool
    valid_catalyst_ids: tuple[str, ...]
    evaluations: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate_type": self.gate_type,
            "decision": self.decision.value,
            "input_hash": self.input_hash,
            "rule_set_hash": self.rule_set_hash,
            "evaluated_at": self.evaluated_at,
            "receipt_hash": self.receipt_hash,
            "core_input_complete": self.core_input_complete,
            "valid_catalyst_ids": list(self.valid_catalyst_ids),
            "evaluations": [dict(item) for item in self.evaluations],
            "gate_version": CATALYST_GATE_VERSION,
        }


class CatalystGate:
    """Fail closed unless raw evidence proves at least one actionable catalyst."""

    recent_event_grace_days = 7

    def evaluate(self, packet: dict[str, Any], rules: EffectiveRuleSet, now: datetime | None = None) -> CatalystGateReceipt:
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        evaluations: list[dict[str, Any]] = []
        valid_ids: list[str] = []
        for raw in list(packet.get("catalysts") or []):
            if not isinstance(raw, dict):
                continue
            catalyst_id = str(raw.get("catalyst_id") or "UNKNOWN")
            reasons: list[str] = []
            source_url = raw.get("source_url")
            source_time = raw.get("source_observed_at")
            event_time = _parse_timestamp(raw.get("event_at"))
            event_type = str(raw.get("event_type") or "UNKNOWN").upper()
            verification = str(raw.get("verification_status") or "UNKNOWN").upper()
            binding = str(raw.get("binding_status") or "NOT_APPLICABLE").upper()

            if not _valid_http_url(source_url):
                reasons.append("CATALYST_SOURCE_URL_MISSING")
            if _parse_timestamp(source_time) is None:
                reasons.append("CATALYST_SOURCE_TIMESTAMP_MISSING")
            else:
                try:
                    if age_seconds(str(source_time), now=reference, max_future_skew_seconds=rules.max_future_skew_seconds) > rules.max_age_research_hours * 3600:
                        reasons.append("CATALYST_SOURCE_STALE")
                except Exception:
                    reasons.append("CATALYST_SOURCE_TIMESTAMP_INVALID")
            if verification not in VERIFIED_STATES:
                reasons.append("CATALYST_UNVERIFIED")
            if event_type in {"", "UNKNOWN"}:
                reasons.append("CATALYST_TYPE_MISSING")
            if event_type in NON_BINDING_EVENT_TYPES and binding != "BINDING":
                reasons.append("CATALYST_NON_BINDING_EVENT")
            if event_time is None:
                reasons.append("CATALYST_EVENT_TIME_MISSING")
                days_to_event = None
            else:
                seconds = (event_time - reference).total_seconds()
                days_to_event = seconds / 86400.0
                if event_time < reference - timedelta(days=self.recent_event_grace_days) or event_time > reference + timedelta(days=rules.strategy_max_days):
                    reasons.append("CATALYST_OUTSIDE_STRATEGY_WINDOW")
            transmission_ok, transmission_key, transmission_value = _numeric_transmission(raw.get("economic_transmission"))
            if not transmission_ok:
                reasons.append("CATALYST_ECONOMIC_TRANSMISSION_UNQUANTIFIED")
            confirmation_metric = raw.get("confirmation_metric")
            if not isinstance(confirmation_metric, str) or not confirmation_metric.strip():
                reasons.append("CATALYST_CONFIRMATION_METRIC_MISSING")
            evidence_id = raw.get("evidence_id")
            artifact_id = raw.get("artifact_id")
            if not evidence_id or not artifact_id:
                reasons.append("CATALYST_PROVENANCE_RECEIPT_MISSING")

            valid = not reasons
            if valid:
                valid_ids.append(catalyst_id)
            evaluations.append({
                "catalyst_id": catalyst_id,
                "valid": valid,
                "reason_codes": reasons,
                "event_type": event_type,
                "event_at": raw.get("event_at"),
                "days_to_event": round(days_to_event, 6) if days_to_event is not None else None,
                "verification_status": verification,
                "binding_status": binding,
                "economic_transmission_key": transmission_key,
                "economic_transmission_value": transmission_value,
                "confirmation_metric": confirmation_metric,
                "source_url": source_url,
                "source_observed_at": source_time,
                "artifact_id": artifact_id,
                "evidence_id": evidence_id,
            })

        complete = bool(valid_ids)
        decision = GateDecision.PASS if complete else GateDecision.INSUFFICIENT_EVIDENCE
        input_hash = canonical_hash(packet)
        receipt_payload = {
            "gate_type": "CatalystGate",
            "decision": decision.value,
            "input_hash": input_hash,
            "rule_set_hash": rules.rule_set_hash,
            "core_input_complete": complete,
            "valid_catalyst_ids": sorted(valid_ids),
            "evaluations": evaluations,
            "gate_version": CATALYST_GATE_VERSION,
        }
        receipt_hash = canonical_hash(receipt_payload)
        return CatalystGateReceipt(
            "CatalystGate", decision, input_hash, rules.rule_set_hash, utc_now(), receipt_hash,
            complete, tuple(sorted(valid_ids)), tuple(evaluations),
        )

