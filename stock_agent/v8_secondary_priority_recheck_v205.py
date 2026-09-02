"""Persistent HIGH Secondary recheck for V8 MAIN broad live discovery.

A persistent Secondary queue is useful only if queued names are actually
revisited.  This layer injects OPEN/WATCH_OUTSIDE_UNIVERSE HIGH-research-value
names into the next broad universe request as *additional* priority probes. It
does not reduce the normal alpha/breadth budget and does not create candidates,
Research Grade, PRE-A status, execution actions, position sizes, or broker
writes.
"""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from . import adapters as adapters_module
from . import runtime as runtime_module
from .alpha_bootstrap import _candle_series
from .adapters import ProviderError, _latest_observation_time
from .models import RawArtifact, RunMode, RunOutcome, canonical_hash, utc_now

V8_SECONDARY_PRIORITY_RECHECK_VERSION = "V8_SECONDARY_PRIORITY_RECHECK_V2.0.5"
_INSTALLED = False
_PROVIDER_PATCHED = False
_ALLOWED_QUEUE_STATUSES = {"OPEN", "WATCH_OUTSIDE_UNIVERSE"}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def active_high_secondary_ids(store: Any) -> list[str]:
    now = datetime.now(timezone.utc)
    rows = store.connection.execute(
        "SELECT security_id,status,expiry FROM discovery_secondary_queue "
        "WHERE research_value='HIGH' AND status IN ('OPEN','WATCH_OUTSIDE_UNIVERSE') "
        "ORDER BY security_id"
    ).fetchall()
    active: list[str] = []
    for row in rows:
        sid = str(row["security_id"] or "").upper().strip()
        expiry = _parse_ts(row["expiry"])
        if not sid:
            continue
        if expiry is not None and expiry <= now:
            store.connection.execute(
                "UPDATE discovery_secondary_queue SET status='EXPIRED_WATCH',updated_at=? WHERE security_id=?",
                (utc_now(), sid),
            )
            continue
        active.append(sid)
    return sorted(set(active))


def inject_secondary_priority_query(data: dict[str, Any], ids: list[str]) -> tuple[dict[str, Any], bool]:
    patched = copy.deepcopy(data)
    query = dict(patched.get("universe_query") or {})
    bounded = bool(query.get("symbols") or query.get("tickers"))
    if ids and not bounded:
        query["secondary_priority_tickers"] = sorted(set(str(item).upper() for item in ids if str(item).strip()))
        query.setdefault("broad", True)
        patched["universe_query"] = query
    return patched, bounded


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _normalize_priority_ids(query: dict[str, Any]) -> list[str]:
    values = []
    for raw in query.get("secondary_priority_tickers") or []:
        sid = str(raw).upper().strip()
        if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", sid):
            values.append(sid)
    return sorted(set(values))


def _priority_enrich(provider: Any, artifact: RawArtifact, query: dict[str, Any], priority_ids: list[str]) -> RawArtifact:
    if not priority_ids:
        provider.last_secondary_priority_recheck = {
            "version": V8_SECONDARY_PRIORITY_RECHECK_VERSION,
            "requested": [],
            "rows": [],
            "broad_budget_reduced": False,
        }
        return artifact

    payload = copy.deepcopy(artifact.payload if isinstance(artifact.payload, dict) else {})
    securities = [dict(row) for row in (payload.get("securities") or []) if isinstance(row, dict)]
    by_sid = {str(row.get("security_id") or row.get("ticker") or "").upper(): row for row in securities}
    min_price = float(query.get("min_price", 0) or 0)
    min_cap = float(query.get("min_market_cap", 0) or 0)
    min_adv = float(query.get("min_average_dollar_volume", 10_000_000) or 10_000_000)
    technical_count = int(query.get("technical_count", 100) or 100)
    rows_out: list[dict[str, Any]] = []
    source_times = [str(artifact.source_observed_at)] if artifact.source_observed_at else []
    extra_probe_count = 0
    probe_errors = list(payload.get("probe_errors") or [])

    for sid in priority_ids:
        row = by_sid.get(sid)
        receipt = {
            "security_id": sid,
            "found_in_broad_source": row is not None,
            "extra_priority_probe_executed": False,
            "adv": None,
            "status": "UNKNOWN",
        }
        if row is None:
            receipt["status"] = "ABSENT_FROM_BROAD_SOURCE"
            rows_out.append(receipt)
            continue
        price = _positive(row.get("price"))
        cap = _positive(row.get("market_cap"))
        if price is None or price < min_price:
            receipt["status"] = "PRICE_FILTER"
            rows_out.append(receipt)
            continue
        if cap is None or cap < min_cap:
            receipt["status"] = "MARKET_CAP_FILTER"
            rows_out.append(receipt)
            continue

        existing_adv = _positive(row.get("average_dollar_volume"))
        if existing_adv is not None and str(row.get("liquidity_status") or "") == "FULL_CANDLE":
            receipt["adv"] = existing_adv
            receipt["status"] = "ADV_PASS" if existing_adv >= min_adv else "ADV_BELOW_MIN"
            rows_out.append(receipt)
            continue

        toss = getattr(provider, "toss", None)
        fetch_candles = getattr(toss, "fetch_candles", None)
        if not callable(fetch_candles):
            receipt["status"] = "PROVIDER_DATA_BLOCK"
            rows_out.append(receipt)
            continue
        try:
            candle = fetch_candles(sid, "1d", technical_count)
            closes, volumes = _candle_series(candle)
            receipt["extra_priority_probe_executed"] = True
            extra_probe_count += 1
            if len(closes) < 2 or not volumes:
                receipt["status"] = "INSUFFICIENT_CANDLE_HISTORY"
                probe_errors.append(f"{sid}:SECONDARY_PRIORITY_INSUFFICIENT_CANDLE_HISTORY")
                rows_out.append(receipt)
                continue
            row["prices"] = closes
            row["volumes"] = volumes
            row["average_volume"] = sum(volumes[-20:]) / len(volumes[-20:])
            row_price = price or closes[-1]
            adv = row["average_volume"] * row_price
            row["average_dollar_volume"] = adv
            row["average_dollar_volume_source"] = "secondary_priority_probe:mean(volumes[-20:])*price"
            row["liquidity_status"] = "FULL_CANDLE"
            row["liquidity_source"] = f"{getattr(toss, 'base_url', 'toss')}/api/v1/candles"
            row["liquidity_observed_at"] = candle.source_observed_at
            row["liquidity_artifact_id"] = candle.artifact_id
            row["liquidity_artifact_hash"] = candle.payload_hash
            row["secondary_priority_recheck"] = True
            receipt["adv"] = adv
            receipt["status"] = "ADV_PASS" if adv >= min_adv else "ADV_BELOW_MIN"
            if candle.source_observed_at:
                source_times.append(str(candle.source_observed_at))
        except (ProviderError, ValueError) as exc:
            receipt["extra_priority_probe_executed"] = True
            extra_probe_count += 1
            receipt["status"] = "PROVIDER_DATA_BLOCK"
            receipt["error_type"] = type(exc).__name__
            probe_errors.append(f"{sid}:SECONDARY_PRIORITY_{type(exc).__name__}")
        rows_out.append(receipt)

    payload["securities"] = securities
    payload["secondary_priority_recheck_version"] = V8_SECONDARY_PRIORITY_RECHECK_VERSION
    payload["secondary_priority_requested_ids"] = priority_ids
    payload["secondary_priority_extra_probe_count"] = extra_probe_count
    payload["secondary_priority_recheck"] = rows_out
    payload["probe_errors"] = probe_errors[:500]

    full_ids = {
        str(row.get("security_id") or "").upper()
        for row in securities
        if str(row.get("liquidity_status") or "") == "FULL_CANDLE"
    }
    payload["probe_count"] = len(full_ids)
    strategy_eligible = [
        row for row in securities
        if (_positive(row.get("price")) or 0) >= min_price
        and (_positive(row.get("market_cap")) or 0) >= min_cap
        and (
            (_positive(row.get("average_dollar_volume")) or 0) >= min_adv
            or (_positive(row.get("approximate_dollar_volume")) or 0) >= min_adv
        )
    ]
    payload["probe_not_evaluated_count"] = sum(
        1 for row in strategy_eligible
        if str(row.get("security_id") or "").upper() not in full_ids
        and row.get("average_dollar_volume") is None
    )
    payload["probe_not_evaluated_ids"] = [
        str(row.get("security_id") or "").upper()
        for row in strategy_eligible
        if str(row.get("security_id") or "").upper() not in full_ids
        and row.get("average_dollar_volume") is None
    ][:500]

    diagnostic = {
        "version": V8_SECONDARY_PRIORITY_RECHECK_VERSION,
        "requested": priority_ids,
        "rows": rows_out,
        "extra_probe_count": extra_probe_count,
        "broad_budget_reduced": False,
        "base_probe_limit": payload.get("probe_limit"),
        "final_probe_count": payload.get("probe_count"),
    }
    provider.last_secondary_priority_recheck = diagnostic
    payload_hash = canonical_hash(payload)
    observed = _latest_observation_time(source_times) or artifact.source_observed_at or artifact.observed_at
    return replace(
        artifact,
        artifact_id=f"artifact-secondary-priority-universe-{payload_hash[:32]}",
        observed_at=str(observed),
        payload=payload,
        payload_hash=payload_hash,
        source_observed_at=str(observed),
        retrieved_at=utc_now(),
    )


def _patch_live_provider() -> None:
    global _PROVIDER_PATCHED
    provider_cls = adapters_module.CompositeLiveMarketContextProvider
    if _PROVIDER_PATCHED or getattr(provider_cls, "v8_secondary_priority_recheck_version", None) == V8_SECONDARY_PRIORITY_RECHECK_VERSION:
        return
    base_fetch = provider_cls.fetch_universe

    def fetch_universe(self: Any, query: dict[str, Any]):
        values = dict(query or {})
        priority_ids = _normalize_priority_ids(values)
        artifact = base_fetch(self, values)
        bounded = bool(values.get("symbols") or values.get("tickers"))
        if bounded or not values.get("broad", True):
            self.last_secondary_priority_recheck = {
                "version": V8_SECONDARY_PRIORITY_RECHECK_VERSION,
                "requested": priority_ids,
                "rows": [],
                "skipped": "BOUNDED_OR_NON_BROAD",
                "broad_budget_reduced": False,
            }
            return artifact
        return _priority_enrich(self, artifact, values, priority_ids)

    provider_cls.fetch_universe = fetch_universe  # type: ignore[assignment]
    provider_cls.v8_secondary_priority_recheck_version = V8_SECONDARY_PRIORITY_RECHECK_VERSION
    _PROVIDER_PATCHED = True


def _scanner_recheck_counts(store: Any, run_id: str, requested: list[str]) -> dict[str, int]:
    wanted = set(requested)
    seen: dict[str, set[str]] = {sid: set() for sid in requested}
    for row in store.list_stage_results(run_id):
        stage = str(row.get("stage") or "")
        if not stage.startswith("V8_MAIN_SCANNER_") or "_R" not in stage or row.get("status") != "SUCCEEDED":
            continue
        try:
            payload = json.loads(row.get("result_json") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        scanner_id = str(payload.get("scanner_id") or "")
        for item in payload.get("coverage_ledger") or []:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("security_id") or "").upper()
            if sid in wanted:
                seen[sid].add(scanner_id)
    return {sid: len(scanners) for sid, scanners in seen.items()}


def install_v8_secondary_priority_recheck_v205() -> type:
    global _INSTALLED
    _patch_live_provider()
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_secondary_priority_recheck_version", None) == V8_SECONDARY_PRIORITY_RECHECK_VERSION:
        return current

    class V8SecondaryPriorityRecheckProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_secondary_priority_recheck_version = V8_SECONDARY_PRIORITY_RECHECK_VERSION

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            requested = active_high_secondary_ids(self.store)
            patched, bounded = inject_secondary_priority_query(data, requested)
            outcome = super()._run_strict(mode, patched)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted" or bounded or not requested:
                return outcome
            provider = getattr(self.config, "market_data_provider", None)
            diagnostic = getattr(provider, "last_secondary_priority_recheck", None)
            counts = _scanner_recheck_counts(self.store, run_id, requested)
            queue_status = {
                str(row["security_id"]): str(row["status"])
                for row in self.store.connection.execute(
                    "SELECT security_id,status FROM discovery_secondary_queue WHERE security_id IN (%s)" % ",".join("?" for _ in requested),
                    tuple(requested),
                ).fetchall()
            }
            self.store.record_funnel(run_id, "V8_SECONDARY_PRIORITY_RECHECK", len(requested), {
                "version": V8_SECONDARY_PRIORITY_RECHECK_VERSION,
                "requested_security_ids": requested,
                "provider_recheck": diagnostic,
                "scanner_family_evaluated_count": counts,
                "queue_status_after_run": queue_status,
                "normal_broad_probe_budget_reduced": False,
                "secondary_is_pre_a": False,
                "research_value_is_research_grade": False,
                "grade_authority": False,
            })
            return outcome

    runtime_module.ProductionStockAgent = V8SecondaryPriorityRecheckProductionStockAgent
    _INSTALLED = True
    return V8SecondaryPriorityRecheckProductionStockAgent
