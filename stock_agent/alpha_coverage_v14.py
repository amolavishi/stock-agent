"""Alpha Discovery V1.4 coverage hardening.

V1.3 correctly increased the intended historical-candle budget, but it built
its additional probe pool only from securities whose *current-day* quote
volume already implied at least the hard ADV threshold.  Before/near the U.S.
open that proxy is naturally sparse, so a nominal 300-name budget could
collapse to one probe.  That is a discovery-correctness failure, not evidence
that no opportunity exists.

V1.4 keeps current-day turnover strictly as a ranking hint.  Price/cap eligible
names remain eligible for historical ADV investigation even when the current
session has no/low volume.  The expensive historical probe budget is filled
from a blend of quote anomalies and deterministic broad exploration, then the
canonical ADV gate is evaluated only from historical candles.

Authority boundary is unchanged:
Discovery Priority != Research Grade != PRE-A Readiness != Execution Action.
This module cannot promote a grade, grant STARTER, size a position, or write a
broker order.
"""
from __future__ import annotations

import math
from typing import Any

from . import adapters as adapters_module
from .alpha_bootstrap import (
    _BaseCompositeLiveMarketContextProvider,
    _candle_series,
    _positive_float,
    _probe_limit,
)
from .adapters import ProviderError, _latest_observation_time
from .models import RawArtifact, canonical_hash, utc_now


ALPHA_COVERAGE_VERSION = "alpha-discovery-v1.4"
MIN_SUCCESS_RATIO = 0.80


def _sid(row: dict[str, Any]) -> str:
    return str(row.get("security_id") or row.get("ticker") or "").upper()


def _selection_v14(
    rows: list[dict[str, Any]],
    budget: int,
    rotation_key: str,
    min_adv: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fill a bounded probe budget without using same-day volume as a gate."""
    material: list[dict[str, Any]] = []
    for raw in rows:
        row = raw
        sid = _sid(row)
        if not sid:
            continue
        approx = _positive_float(row.get("approximate_dollar_volume"))
        cap = _positive_float(row.get("market_cap"))
        row["quote_turnover_proxy"] = (approx / cap) if approx is not None and cap else None
        material.append(row)

    budget = min(max(0, int(budget)), len(material))
    if budget == 0:
        return [], {
            "turnover": 0,
            "liquidity": 0,
            "exploration": 0,
            "strong_hint_fraction": 0.0,
            "quote_signal_regime": "NO_POOL",
        }

    strong_hints = sum(
        1 for row in material
        if (_positive_float(row.get("approximate_dollar_volume")) or 0.0) >= min_adv
    )
    strong_hint_fraction = strong_hints / max(1, len(material))

    # When current-session liquidity is sparse (typical premarket/just-open),
    # current-day volume becomes a weak ranking signal and broad exploration
    # receives most of the budget.  It is never an eligibility requirement.
    if strong_hint_fraction < 0.10:
        turnover_share, liquidity_share = 0.15, 0.15
        signal_regime = "THIN_OR_PREMARKET"
    else:
        turnover_share, liquidity_share = 0.45, 0.30
        signal_regime = "NORMAL_SESSION_HINTS"

    turnover_slots = int(round(budget * turnover_share))
    liquidity_slots = int(round(budget * liquidity_share))
    turnover_slots = min(turnover_slots, budget)
    liquidity_slots = min(liquidity_slots, max(0, budget - turnover_slots))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add(source: list[dict[str, Any]], limit: int) -> int:
        before = len(selected)
        for row in source:
            if len(selected) - before >= limit:
                break
            sid = _sid(row)
            if not sid or sid in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(sid)
        return len(selected) - before

    hint_rows = [row for row in material if _positive_float(row.get("approximate_dollar_volume")) is not None]
    turnover_ranked = sorted(
        hint_rows,
        key=lambda row: (
            float(row.get("quote_turnover_proxy") or 0.0),
            float(row.get("approximate_dollar_volume") or 0.0),
            _sid(row),
        ),
        reverse=True,
    )
    turnover_count = add(turnover_ranked, turnover_slots)

    liquidity_ranked = sorted(
        hint_rows,
        key=lambda row: (
            float(row.get("approximate_dollar_volume") or 0.0),
            float(row.get("quote_turnover_proxy") or 0.0),
            _sid(row),
        ),
        reverse=True,
    )
    liquidity_count = add(liquidity_ranked, liquidity_slots)

    # Exploration spans *all* remaining price/cap eligible names.  Missing or
    # tiny same-day volume therefore cannot starve the historical-ADV probe.
    remaining = [row for row in material if _sid(row) not in selected_ids]
    exploration_ranked = sorted(
        remaining,
        key=lambda row: canonical_hash({
            "version": ALPHA_COVERAGE_VERSION,
            "rotation_key": rotation_key,
            "security_id": _sid(row),
        }),
    )
    exploration_count = add(exploration_ranked, budget - len(selected))

    # Hard selection invariant: if enough price/cap eligible rows exist, the
    # requested budget must be completely filled.  Never silently report a
    # one-name clean discovery run against a 300-name target again.
    if len(selected) != budget:
        raise ProviderError(
            f"alpha probe selection coverage invariant violated: selected={len(selected)} target={budget}"
        )

    return selected, {
        "turnover": turnover_count,
        "liquidity": liquidity_count,
        "exploration": exploration_count,
        "strong_hint_fraction": strong_hint_fraction,
        "quote_signal_regime": signal_regime,
    }


class AlphaCoverageLiveMarketContextProvider(_BaseCompositeLiveMarketContextProvider):
    """Broad live provider with a mandatory, session-robust ADV probe budget."""

    provider_name = "composite-live-market-alpha-v14"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.alpha_benchmark_return_window: float | None = None

    def fetch_market_context(self, query: dict[str, Any]) -> RawArtifact:
        artifact = super().fetch_market_context(query)
        self.alpha_benchmark_return_window = None
        for item in artifact.payload.get("source") or []:
            if not isinstance(item, dict) or str(item.get("symbol") or "").upper() != "SPY":
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), (dict, list)) else item
            rows = payload.get("data") if isinstance(payload, dict) else payload
            closes: list[float] = []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                value = _positive_float(row.get("close") if row.get("close") is not None else row.get("closePrice"))
                if value is not None:
                    closes.append(value)
            if len(closes) >= 2 and closes[0] > 0:
                self.alpha_benchmark_return_window = closes[-1] / closes[0] - 1.0
                break
        return artifact

    def fetch_universe(self, query: dict[str, Any]) -> RawArtifact:
        query = dict(query or {})
        requested = query.get("symbols") or query.get("tickers")
        if requested or not query.get("broad", True):
            return super().fetch_universe(query)

        target_budget = _probe_limit(query)

        # Reuse the canonical screener + full quote scan, but spend only one
        # legacy candle before V1.4 sees the complete quote-enriched universe.
        base_query = dict(query)
        base_query["liquidity_full_probe_limit"] = 1
        base = super().fetch_universe(base_query)
        payload = dict(base.payload)
        securities = [dict(row) for row in (payload.get("securities") or []) if isinstance(row, dict)]

        min_price = float(query.get("min_price", 0) or 0)
        min_cap = float(query.get("min_market_cap", 0) or 0)
        min_adv = float(query.get("min_average_dollar_volume", 10_000_000) or 10_000_000)
        rotation_key = str(query.get("liquidity_rotation_key") or query.get("as_of") or utc_now())[:10]

        price_cap_eligible = [
            row for row in securities
            if (_positive_float(row.get("price")) or 0.0) >= min_price
            and (_positive_float(row.get("market_cap")) or 0.0) >= min_cap
        ]
        already_full = {
            _sid(row) for row in price_cap_eligible
            if str(row.get("liquidity_status") or "") == "FULL_CANDLE"
        }
        remaining_pool = [row for row in price_cap_eligible if _sid(row) not in already_full]
        coverage_target = min(target_budget, len(price_cap_eligible))
        additional_budget = max(0, coverage_target - len(already_full))

        selected, selection_meta = _selection_v14(
            remaining_pool,
            additional_budget,
            rotation_key,
            min_adv,
        )
        attempted_ids = set(already_full)
        probe_errors = list(payload.get("probe_errors") or [])
        source_times: list[str] = [str(base.source_observed_at)] if base.source_observed_at else []
        by_sid = {_sid(row): row for row in securities if _sid(row)}

        for rank, selected_row in enumerate(selected, start=1):
            sid = _sid(selected_row)
            attempted_ids.add(sid)
            row = by_sid[sid]
            row["alpha_probe_rank"] = rank
            approx = _positive_float(row.get("approximate_dollar_volume")) or 0.0
            turnover = _positive_float(row.get("quote_turnover_proxy")) or 0.0
            row["alpha_probe_score"] = round(turnover * 0.70 + math.log1p(approx) / 100.0, 12)
            try:
                candle = self.toss.fetch_candles(sid, "1d", int(query.get("technical_count", 100)))
                closes, volumes = _candle_series(candle)
                if len(closes) < 2 or not volumes:
                    probe_errors.append(f"{sid}:INSUFFICIENT_CANDLE_HISTORY")
                    continue
                row["prices"] = closes
                row["volumes"] = volumes
                row["average_volume"] = sum(volumes[-20:]) / len(volumes[-20:])
                row_price = _positive_float(row.get("price")) or closes[-1]
                row["average_dollar_volume"] = row["average_volume"] * row_price
                row["average_dollar_volume_source"] = "alpha_v14_probe:mean(volumes[-20:])*price"
                row["liquidity_status"] = "FULL_CANDLE"
                row["liquidity_source"] = f"{self.toss.base_url}/api/v1/candles"
                row["liquidity_observed_at"] = candle.source_observed_at
                row["liquidity_artifact_id"] = candle.artifact_id
                row["liquidity_artifact_hash"] = candle.payload_hash
                if candle.source_observed_at:
                    source_times.append(str(candle.source_observed_at))
            except (ProviderError, ValueError) as exc:
                probe_errors.append(f"{sid}:{type(exc).__name__}")

        full_ids = {
            _sid(row) for row in price_cap_eligible
            if _positive_float(row.get("average_dollar_volume")) is not None
        }
        attempt_count = len(attempted_ids)
        success_count = len(full_ids & attempted_ids)

        if attempt_count != coverage_target:
            raise ProviderError(
                f"alpha probe attempt coverage invariant violated: attempted={attempt_count} target={coverage_target}"
            )
        required_success = math.ceil(coverage_target * MIN_SUCCESS_RATIO) if coverage_target else 0
        if success_count < required_success:
            raise ProviderError(
                "alpha historical ADV coverage degraded: "
                f"success={success_count} target={coverage_target} required={required_success}"
            )

        payload["securities"] = securities
        payload["enrichment_provider"] = self.provider_name
        payload["alpha_discovery_version"] = ALPHA_COVERAGE_VERSION
        payload["probe_strategy"] = "ALPHA_V14_SESSION_ROBUST_BUDGET_FILL"
        payload["probe_limit"] = target_budget
        payload["probe_target"] = coverage_target
        # Keep `probe_count` as attempted historical probes, matching the
        # original provider telemetry semantics.  Exact successes are separate.
        payload["probe_count"] = attempt_count
        payload["probe_success_count"] = success_count
        payload["probe_success_ratio"] = (success_count / coverage_target) if coverage_target else 1.0
        payload["coverage_status"] = "PASS"
        payload["alpha_selected_count"] = len(selected)
        payload["alpha_turnover_probe_count"] = selection_meta["turnover"]
        payload["alpha_liquidity_probe_count"] = selection_meta["liquidity"]
        payload["alpha_exploration_probe_count"] = selection_meta["exploration"]
        payload["liquidity_priority_probe_count"] = selection_meta["turnover"] + selection_meta["liquidity"]
        payload["liquidity_rotation_probe_count"] = selection_meta["exploration"]
        payload["liquidity_rotation_key"] = rotation_key
        payload["quote_signal_regime"] = selection_meta["quote_signal_regime"]
        payload["strong_quote_hint_fraction"] = selection_meta["strong_hint_fraction"]
        payload["probe_errors"] = probe_errors[:500]

        # Unknown liquidity remains explicit for every price/cap eligible name
        # not backed by historical ADV.  A same-day quote proxy cannot turn an
        # unknown into either PASS or a legitimate low-liquidity rejection.
        not_evaluated = [
            row for row in price_cap_eligible
            if _positive_float(row.get("average_dollar_volume")) is None
        ]
        payload["probe_not_evaluated_count"] = len(not_evaluated)
        payload["probe_not_evaluated_ids"] = [_sid(row) for row in not_evaluated][:500]

        source_observed = _latest_observation_time(source_times) or base.source_observed_at or base.observed_at
        payload_hash = canonical_hash(payload)
        return RawArtifact(
            artifact_id=f"artifact-alpha-v14-universe-{payload_hash[:32]}",
            provider=self.provider_name,
            artifact_type="UNIVERSE",
            subject_id=None,
            observed_at=str(source_observed),
            payload=payload,
            payload_hash=payload_hash,
            source_observed_at=str(source_observed),
            retrieved_at=utc_now(),
        )


_INSTALLED = False


def install_alpha_coverage_v14() -> None:
    """Install V1.4 market discovery before CLI imports concrete adapters."""
    global _INSTALLED
    if _INSTALLED:
        return
    adapters_module.CompositeLiveMarketContextProvider = AlphaCoverageLiveMarketContextProvider
    _INSTALLED = True
