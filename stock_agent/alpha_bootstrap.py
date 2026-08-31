"""Alpha-first discovery bootstrap for the production CLI.

This module changes *discovery recall*, not certification authority.

The existing Stock Agent was deliberately fail-closed, but the live broad
provider spent almost all expensive candle probes on a tiny daily sample and
an alphabetic rotation.  That is useful for eventual coverage but is badly
aligned with the product objective: find idiosyncratic 1-8 week winners,
including securities showing unusual strength while the tape is weak.

V1.3 therefore applies four narrow production policies before ``cli`` imports
its concrete classes:

1. spend a larger, bounded candle budget on turnover/liquidity anomalies plus
   deterministic exploration instead of contiguous alphabetic rotation;
2. expose richer *non-authoritative* alpha/relative-strength technical
   features to Discovery;
3. use filing-cycle-aware SEC freshness and a wider HUNT research lookback,
   while execution review keeps the original 7-day research freshness;
4. deterministically structure a catalyst only when a real IR/media source
   text itself contains an identifiable event and quantified economic fact.

Hard A/A- gates, FinalAllocation, position sizing and broker-write boundaries
are untouched.  PRE-A concepts are used only as a design principle here:
Discovery Priority != Research Grade != Promotion Readiness != Execution
Action.  Nothing in this module can auto-promote a candidate or grant STARTER.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from . import adapters as adapters_module
from . import runtime as runtime_module
from .adapters import (
    CompositeLiveMarketContextProvider as _BaseCompositeLiveMarketContextProvider,
    CompositeResearchEvidenceProvider as _BaseCompositeResearchEvidenceProvider,
    ProviderError,
    _chronological_observation_rows,
    _close_series,
    _latest_observation_time,
)
from .models import EffectiveRuleSet, RawArtifact, RunMode, TechnicalFeatures, canonical_hash, utc_now
from .normalizers import TechnicalFeatureCalculator
from .runtime import ProductionStockAgent as _BaseProductionStockAgent


ALPHA_DISCOVERY_VERSION = "alpha-discovery-v1.3"
DEFAULT_ALPHA_PROBE_LIMIT = 300
MIN_ALPHA_PROBE_LIMIT = 30
MAX_ALPHA_PROBE_LIMIT = 1000


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _probe_limit(query: dict[str, Any]) -> int:
    raw = query.get("alpha_probe_limit")
    if raw in (None, ""):
        raw = os.getenv("STOCK_AGENT_ALPHA_PROBE_LIMIT", str(DEFAULT_ALPHA_PROBE_LIMIT))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ProviderError("alpha probe limit must be an integer") from exc
    if not MIN_ALPHA_PROBE_LIMIT <= value <= MAX_ALPHA_PROBE_LIMIT:
        raise ProviderError(
            f"alpha probe limit must be {MIN_ALPHA_PROBE_LIMIT}..{MAX_ALPHA_PROBE_LIMIT}"
        )
    return value


def _candle_series(artifact: RawArtifact) -> tuple[list[float], list[float]]:
    payload = artifact.payload.get("result") if isinstance(artifact.payload, dict) else []
    candles = payload.get("candles") if isinstance(payload, dict) else payload
    closes: list[float] = []
    volumes: list[float] = []
    for candle in _chronological_observation_rows(candles if isinstance(candles, list) else []):
        if not isinstance(candle, dict):
            continue
        close = _positive_float(candle.get("closePrice") if candle.get("closePrice") is not None else candle.get("close"))
        volume = _positive_float(candle.get("volume"))
        if close is not None:
            closes.append(close)
        if volume is not None:
            volumes.append(volume)
    return closes, volumes


def _alpha_selection(rows: list[dict[str, Any]], budget: int, rotation_key: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select expensive probes for alpha recall, then deterministic breadth.

    Selection is not an investment grade.  It only decides where to spend
    market-data retrieval budget.  All selected names must still pass the
    canonical deterministic ADV filter after full candles are observed.
    """
    if budget <= 0 or not rows:
        return [], {"turnover": 0, "liquidity": 0, "exploration": 0}

    material: list[dict[str, Any]] = []
    for row in rows:
        sid = str(row.get("security_id") or row.get("ticker") or "").upper()
        approx = _positive_float(row.get("approximate_dollar_volume"))
        cap = _positive_float(row.get("market_cap"))
        if not sid or approx is None:
            continue
        candidate = row
        candidate["quote_turnover_proxy"] = (approx / cap) if cap else None
        material.append(candidate)

    budget = min(budget, len(material))
    turnover_slots = min(budget, max(1, int(round(budget * 0.50)))) if budget else 0
    liquidity_slots = min(max(0, budget - turnover_slots), int(round(budget * 0.35)))
    exploration_slots = max(0, budget - turnover_slots - liquidity_slots)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add(source: list[dict[str, Any]], limit: int) -> int:
        before = len(selected)
        for row in source:
            if len(selected) - before >= limit:
                break
            sid = str(row.get("security_id") or row.get("ticker") or "").upper()
            if not sid or sid in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(sid)
        return len(selected) - before

    turnover_ranked = sorted(
        material,
        key=lambda row: (
            float(row.get("quote_turnover_proxy") or 0.0),
            float(row.get("approximate_dollar_volume") or 0.0),
            str(row.get("security_id") or ""),
        ),
        reverse=True,
    )
    turnover_count = add(turnover_ranked, turnover_slots)

    liquidity_ranked = sorted(
        material,
        key=lambda row: (
            float(row.get("approximate_dollar_volume") or 0.0),
            float(row.get("quote_turnover_proxy") or 0.0),
            str(row.get("security_id") or ""),
        ),
        reverse=True,
    )
    liquidity_count = add(liquidity_ranked, liquidity_slots)

    remaining = [row for row in material if str(row.get("security_id") or "").upper() not in selected_ids]
    exploration_ranked = sorted(
        remaining,
        key=lambda row: canonical_hash({
            "version": ALPHA_DISCOVERY_VERSION,
            "rotation_key": rotation_key,
            "security_id": str(row.get("security_id") or "").upper(),
        }),
    )
    exploration_count = add(exploration_ranked, exploration_slots)

    # If deduplication left spare slots, fill by the blended alpha ranking.
    if len(selected) < budget:
        blended = sorted(
            material,
            key=lambda row: (
                float(row.get("quote_turnover_proxy") or 0.0) * 0.65
                + math.log1p(float(row.get("approximate_dollar_volume") or 0.0)) / 100.0,
                str(row.get("security_id") or ""),
            ),
            reverse=True,
        )
        add(blended, budget - len(selected))

    return selected, {
        "turnover": turnover_count,
        "liquidity": liquidity_count,
        "exploration": exploration_count,
    }


class AlphaCompositeLiveMarketContextProvider(_BaseCompositeLiveMarketContextProvider):
    """Broad provider that spends candle budget on anomaly discovery first."""

    provider_name = "composite-live-market-alpha-v13"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.alpha_benchmark_return_window: float | None = None

    def fetch_market_context(self, query: dict[str, Any]) -> RawArtifact:
        artifact = super().fetch_market_context(query)
        self.alpha_benchmark_return_window = None
        for item in artifact.payload.get("source") or []:
            if not isinstance(item, dict) or str(item.get("symbol") or "").upper() != "SPY":
                continue
            values = _close_series(item.get("payload") if isinstance(item.get("payload"), (dict, list)) else item)
            if len(values) >= 2 and values[0] > 0:
                self.alpha_benchmark_return_window = values[-1] / values[0] - 1.0
                break
        return artifact

    def fetch_universe(self, query: dict[str, Any]) -> RawArtifact:
        query = dict(query or {})
        requested = query.get("symbols") or query.get("tickers")
        if requested or not query.get("broad", True):
            return super().fetch_universe(query)

        target_budget = _probe_limit(query)
        # The base provider remains responsible for the broad screener and the
        # full quote scan.  Give its legacy rotation only one candle so it
        # cannot consume our alpha budget before we see quote anomalies.
        base_query = dict(query)
        base_query["liquidity_full_probe_limit"] = 1
        base = super().fetch_universe(base_query)
        payload = dict(base.payload)
        securities = [dict(row) for row in (payload.get("securities") or []) if isinstance(row, dict)]

        min_price = float(query.get("min_price", 0) or 0)
        min_cap = float(query.get("min_market_cap", 0) or 0)
        min_adv = float(query.get("min_average_dollar_volume", 10_000_000) or 10_000_000)
        rotation_key = str(query.get("liquidity_rotation_key") or query.get("as_of") or utc_now())[:10]

        already_full = {
            str(row.get("security_id") or "").upper()
            for row in securities
            if str(row.get("liquidity_status") or "") == "FULL_CANDLE"
        }
        pool: list[dict[str, Any]] = []
        for row in securities:
            price = _positive_float(row.get("price"))
            cap = _positive_float(row.get("market_cap"))
            approx = _positive_float(row.get("approximate_dollar_volume"))
            sid = str(row.get("security_id") or "").upper()
            if not sid or sid in already_full:
                continue
            if price is None or price < min_price or cap is None or cap < min_cap:
                continue
            # A one-day quote is never accepted as ADV.  It is only a cheap
            # prioritization hint; names below the hard ADV threshold are not
            # worth spending scarce historical probes on in the default lane.
            if approx is None or approx < min_adv:
                continue
            pool.append(row)

        selected, quota_counts = _alpha_selection(pool, max(0, target_budget - len(already_full)), rotation_key)
        selected_ids = {str(row.get("security_id") or "").upper() for row in selected}
        probe_errors = list(payload.get("probe_errors") or [])
        source_times: list[str] = [str(base.source_observed_at)] if base.source_observed_at else []

        by_sid = {str(row.get("security_id") or "").upper(): row for row in securities}
        for rank, selected_row in enumerate(selected, start=1):
            sid = str(selected_row.get("security_id") or "").upper()
            row = by_sid[sid]
            row["alpha_probe_rank"] = rank
            turnover = float(row.get("quote_turnover_proxy") or 0.0)
            approx = float(row.get("approximate_dollar_volume") or 0.0)
            row["alpha_probe_score"] = round(turnover * 0.70 + math.log1p(approx) / 100.0, 12)
            try:
                candle = self.toss.fetch_candles(
                    sid,
                    "1d",
                    int(query.get("technical_count", 100)),
                )
                closes, volumes = _candle_series(candle)
                if len(closes) < 2 or not volumes:
                    probe_errors.append(f"{sid}:INSUFFICIENT_CANDLE_HISTORY")
                    continue
                row["prices"] = closes
                row["volumes"] = volumes
                row["average_volume"] = sum(volumes[-20:]) / len(volumes[-20:])
                row_price = _positive_float(row.get("price")) or closes[-1]
                row["average_dollar_volume"] = row["average_volume"] * row_price
                row["average_dollar_volume_source"] = "alpha_probe:mean(volumes[-20:])*price"
                row["liquidity_status"] = "FULL_CANDLE"
                row["liquidity_source"] = f"{self.toss.base_url}/api/v1/candles"
                row["liquidity_observed_at"] = candle.source_observed_at
                row["liquidity_artifact_id"] = candle.artifact_id
                row["liquidity_artifact_hash"] = candle.payload_hash
                if candle.source_observed_at:
                    source_times.append(str(candle.source_observed_at))
            except (ProviderError, ValueError) as exc:
                probe_errors.append(f"{sid}:{type(exc).__name__}")

        payload["securities"] = securities
        payload["enrichment_provider"] = self.provider_name
        payload["alpha_discovery_version"] = ALPHA_DISCOVERY_VERSION
        payload["probe_strategy"] = "ALPHA_TURNOVER_LIQUIDITY_HASH_EXPLORATION_V1"
        payload["probe_limit"] = target_budget
        full_ids = {
            str(row.get("security_id") or "").upper()
            for row in securities
            if str(row.get("liquidity_status") or "") == "FULL_CANDLE"
        }
        payload["probe_count"] = len(full_ids)
        payload["alpha_selected_count"] = len(selected_ids)
        payload["alpha_turnover_probe_count"] = quota_counts["turnover"]
        payload["alpha_liquidity_probe_count"] = quota_counts["liquidity"]
        payload["alpha_exploration_probe_count"] = quota_counts["exploration"]
        payload["liquidity_priority_probe_count"] = quota_counts["turnover"] + quota_counts["liquidity"]
        payload["liquidity_rotation_probe_count"] = quota_counts["exploration"]
        payload["liquidity_rotation_key"] = rotation_key
        payload["probe_errors"] = probe_errors[:500]

        strategy_eligible = [
            row for row in securities
            if (_positive_float(row.get("price")) or 0) >= min_price
            and (_positive_float(row.get("market_cap")) or 0) >= min_cap
            and (
                (_positive_float(row.get("average_dollar_volume")) or 0) >= min_adv
                or (_positive_float(row.get("approximate_dollar_volume")) or 0) >= min_adv
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

        source_observed = _latest_observation_time(source_times) or base.source_observed_at or base.observed_at
        payload_hash = canonical_hash(payload)
        return RawArtifact(
            artifact_id=f"artifact-alpha-universe-{payload_hash[:32]}",
            provider=self.provider_name,
            artifact_type="UNIVERSE",
            subject_id=None,
            observed_at=str(source_observed),
            payload=payload,
            payload_hash=payload_hash,
            source_observed_at=str(source_observed),
            retrieved_at=utc_now(),
        )


class AlphaTechnicalFeatureCalculator(TechnicalFeatureCalculator):
    version = "technical-features-alpha-v1.3"

    def __init__(self, market_provider: Any | None = None) -> None:
        self.market_provider = market_provider

    def calculate(
        self,
        security_id: str,
        prices: list[float],
        volumes: list[float] | None = None,
        as_of: str | None = None,
        source_artifact_ids: tuple[str, ...] = (),
    ) -> TechnicalFeatures:
        base = super().calculate(security_id, prices, volumes, as_of, source_artifact_ids)
        values = [float(item) for item in prices]
        features = dict(base.features)

        def trailing_return(sessions: int) -> float | None:
            if len(values) < 2:
                return None
            start = values[max(0, len(values) - 1 - sessions)]
            return values[-1] / start - 1.0 if start > 0 else None

        r5 = trailing_return(5)
        r20 = trailing_return(20)
        r30 = trailing_return(30)
        high20 = max(values[-20:]) if values else values[-1]
        benchmark = getattr(self.market_provider, "alpha_benchmark_return_window", None)
        benchmark = float(benchmark) if isinstance(benchmark, (int, float)) and math.isfinite(float(benchmark)) else None
        reference_return = r30 if r30 is not None else float(features.get("return_window") or 0.0)
        relative = reference_return - benchmark if benchmark is not None else None
        volume_ratio20 = None
        if volumes and len(volumes) >= 2:
            clean = [float(item) for item in volumes if _positive_float(item) is not None]
            prior = clean[-21:-1] if len(clean) >= 21 else clean[:-1]
            if prior and sum(prior) > 0:
                volume_ratio20 = clean[-1] / (sum(prior) / len(prior))

        alpha_score = reference_return
        if relative is not None:
            alpha_score = 0.65 * relative + 0.35 * reference_return
        if volume_ratio20 is not None:
            alpha_score += min(max(volume_ratio20 - 1.0, -1.0), 3.0) * 0.02

        signal = "NEUTRAL"
        if benchmark is not None and benchmark < 0 and relative is not None and relative >= 0.08 and values[-1] >= float(features.get("sma_window") or values[-1]):
            signal = "CRISIS_RELATIVE_STRENGTH"
        elif relative is not None and relative >= 0.10:
            signal = "MARKET_RELATIVE_STRENGTH"
        elif reference_return >= 0.10:
            signal = "ABSOLUTE_MOMENTUM"

        features.update({
            "return_5": r5,
            "return_20": r20,
            "return_30": r30,
            "drawdown_from_20d_high": values[-1] / high20 - 1.0 if high20 > 0 else None,
            "breakout_distance_20": values[-1] / high20 - 1.0 if high20 > 0 else None,
            "volume_ratio_20": volume_ratio20,
            "benchmark_return_window": benchmark,
            "benchmark_relative_return": relative,
            "alpha_relative_strength_score": alpha_score,
            "alpha_signal_class": signal,
        })
        stamp = as_of or base.as_of
        payload_hash = canonical_hash({
            "security_id": security_id,
            "as_of": stamp,
            "features": features,
            "version": self.version,
        })
        return TechnicalFeatures(
            security_id,
            stamp,
            features,
            self.version,
            tuple(source_artifact_ids),
            payload_hash,
        )


_MONTHS = "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
_DATE_RE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}})(?:,\s*|\s+)(20\d{{2}})\b", re.IGNORECASE)
_DOLLAR_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|mm|m|b)?\b", re.IGNORECASE)
_PERCENT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*%")


def _parse_event_date(text: str) -> str | None:
    matches = list(_DATE_RE.finditer(text))
    for match in matches:
        raw = match.group(0)
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                parsed = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                return parsed.isoformat().replace("+00:00", "Z")
            except ValueError:
                continue
    return None


def _quantified_transmission(text: str, metric: str) -> dict[str, Any] | None:
    dollar = _DOLLAR_RE.search(text)
    if dollar:
        value = float(dollar.group(1))
        scale = str(dollar.group(2) or "").lower()
        if scale in {"billion", "bn", "b"}:
            value *= 1_000_000_000
        elif scale in {"million", "mm", "m"}:
            value *= 1_000_000
        return {"metric": metric, "direction": "POSITIVE", "amount": value}
    percent = _PERCENT_RE.search(text)
    if percent:
        return {"metric": metric, "direction": "POSITIVE", "percent": float(percent.group(1))}
    return None


def _grounded_catalyst(payload: dict[str, Any]) -> dict[str, Any] | None:
    content = str(payload.get("content") or "")
    title = str(payload.get("title") or "")
    text = re.sub(r"\s+", " ", f"{title}. {content}").strip()
    lowered = text.casefold()
    if not text:
        return None

    if "memorandum of understanding" in lowered or re.search(r"\bmou\b", lowered):
        event_type, metric, binding = "MOU", "commercial_value", "NOT_BINDING"
    elif "letter of intent" in lowered or re.search(r"\bloi\b", lowered):
        event_type, metric, binding = "LOI", "commercial_value", "NOT_BINDING"
    elif any(token in lowered for token in ("contract award", "awarded a contract", "contract valued", "new order", "purchase order", "backlog")):
        event_type, metric, binding = "CONTRACT_AWARD", "revenue_or_backlog", "BINDING"
    elif any(token in lowered for token in ("raised guidance", "raises guidance", "increased guidance", "raises outlook", "raised outlook")):
        event_type, metric, binding = "GUIDANCE_RAISE", "guidance", "NOT_APPLICABLE"
    elif any(token in lowered for token in ("share repurchase", "stock repurchase", "buyback")):
        event_type, metric, binding = "BUYBACK", "capital_return", "NOT_APPLICABLE"
    elif any(token in lowered for token in ("refinancing", "refinanced", "debt refinancing")):
        event_type, metric, binding = "REFINANCING", "financing_cost_or_maturity", "BINDING"
    elif any(token in lowered for token in ("capacity expansion", "expand capacity", "production expansion", "increased capacity")):
        event_type, metric, binding = "CAPACITY_EXPANSION", "capacity", "NOT_APPLICABLE"
    elif any(token in lowered for token in ("regulatory approval", "fda approval", "approved by the fda")):
        event_type, metric, binding = "REGULATORY_APPROVAL", "addressable_revenue", "NOT_APPLICABLE"
    elif any(token in lowered for token in ("beat expectations", "beats expectations", "record revenue", "revenue grew", "revenue increased")):
        event_type, metric, binding = "EARNINGS_RESULT", "revenue_or_eps", "NOT_APPLICABLE"
    else:
        return None

    transmission = _quantified_transmission(text, metric)
    if transmission is None:
        return None
    source_time = payload.get("source_observed_at") or payload.get("observed_at")
    source_url = payload.get("source_url") or payload.get("url")
    if not source_time or not source_url:
        return None
    source_class = str(payload.get("source_class") or "").upper()
    verification = "OFFICIAL" if source_class == "COMPANY_IR" else "VERIFIED"
    event_at = _parse_event_date(text) or str(source_time)
    return {
        "catalyst_id": f"ALPHA-{canonical_hash({'url': source_url, 'type': event_type})[:16]}",
        "event_type": event_type,
        "event_at": event_at,
        "verification_status": verification,
        "binding_status": binding,
        "economic_transmission": transmission,
        "confirmation_metric": f"Confirm subsequent filing/earnings realization of {metric}",
        "source_url": source_url,
        "source_observed_at": source_time,
        "alpha_enrichment_version": ALPHA_DISCOVERY_VERSION,
    }


class AlphaCompositeResearchEvidenceProvider(_BaseCompositeResearchEvidenceProvider):
    """Structure real source text for CatalystGate; never invent a source."""

    provider_name = "composite-research-alpha-v13"

    def fetch(self, subject_id: str, query: dict[str, Any] | None = None) -> RawArtifact:
        artifact = super().fetch(subject_id, query or {})
        payload = dict(artifact.payload)
        if not isinstance(payload.get("catalysts"), list) or not payload.get("catalysts"):
            catalyst = _grounded_catalyst(payload)
            if catalyst is not None:
                payload["catalysts"] = [catalyst]
                payload["alpha_catalyst_enrichment"] = {
                    "version": ALPHA_DISCOVERY_VERSION,
                    "grounding": "SOURCE_TEXT_ONLY",
                    "auto_grade": False,
                    "auto_action": False,
                }
        payload_hash = canonical_hash(payload)
        return RawArtifact(
            artifact_id=f"artifact-alpha-research-{payload_hash[:32]}",
            provider=artifact.provider,
            artifact_type=artifact.artifact_type,
            subject_id=artifact.subject_id,
            observed_at=artifact.observed_at,
            payload=payload,
            payload_hash=payload_hash,
            source_observed_at=artifact.source_observed_at,
            retrieved_at=artifact.retrieved_at,
        )


class AlphaProductionStockAgent(_BaseProductionStockAgent):
    """Strict agent with discovery-only recall/freshness corrections."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._alpha_active_mode: RunMode | None = None
        self.technical_calculator = AlphaTechnicalFeatureCalculator(self.config.market_data_provider)

    def _run_strict(self, mode: RunMode, data: dict[str, Any]):
        previous = self._alpha_active_mode
        self._alpha_active_mode = mode
        try:
            return super()._run_strict(mode, data)
        finally:
            self._alpha_active_mode = previous

    def _rules(self, data: dict[str, Any]) -> EffectiveRuleSet:
        base = super()._rules(data)
        # Authorized overrides remain authoritative and are never rewritten by
        # this discovery bootstrap.
        if base.override_id:
            return base
        research_hours = base.max_age_research_hours
        if self._alpha_active_mode == RunMode.HUNT_ONLY:
            # Discovery horizon is 1-8 weeks.  A 7-day blanket freshness rule
            # can discard a still-future, source-proven catalyst before it is
            # even researched.  Execution review deliberately retains 7 days.
            research_hours = max(research_hours, 24 * 45)
        # SEC submissions naturally follow quarterly/8-K cadence; 31 days
        # treated normal issuers as stale.  120d keeps the latest quarterly
        # filing usable while the full forensic stage still examines what was
        # actually filed and never fabricates a new observation.
        return replace(
            base,
            max_age_sec_hours=max(base.max_age_sec_hours, 24 * 120),
            max_age_research_hours=research_hours,
        )


_INSTALLED = False


def install_alpha_discovery_policy() -> None:
    """Install once before ``stock_agent.cli`` imports concrete classes."""
    global _INSTALLED
    if _INSTALLED:
        return
    adapters_module.CompositeLiveMarketContextProvider = AlphaCompositeLiveMarketContextProvider
    adapters_module.CompositeResearchEvidenceProvider = AlphaCompositeResearchEvidenceProvider
    runtime_module.ProductionStockAgent = AlphaProductionStockAgent
    _INSTALLED = True
