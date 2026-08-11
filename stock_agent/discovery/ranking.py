from __future__ import annotations

from .features import known_field, value
from .schemas import CandidateFeatureSnapshot


DEFAULT_WEIGHTS = {"signal_strength": 0.10, "catalyst_quality": 0.16, "expectation_gap": 0.12,
                   "surge_elasticity": 0.08, "entry_readiness": 0.14, "fundamental_inflection": 0.12,
                   "sector_regime_fit": 0.08, "capital_structure_safety": 0.08,
                   "liquidity_quality": 0.05, "strategy_fit": 0.05, "data_confidence": 0.02}


def cross_sectional_percentiles(candidates: list[CandidateFeatureSnapshot], field_name: str) -> dict[str, dict[str, float]]:
    """Return universe/sector/size-bucket percentiles without replacing UNKNOWN."""
    known = [(item, value(item, field_name)) for item in candidates if known_field(item, field_name)]
    universe_values = [item_value for _, item_value in known]
    by_sector: dict[str, list[float]] = {}
    by_size: dict[str, list[float]] = {}
    for candidate, item_value in known:
        by_sector.setdefault(candidate.security.sector_canonical, []).append(item_value)
        by_size.setdefault(size_bucket(value(candidate, "market_cap_usd", None)), []).append(item_value)
    return {candidate.security.ticker: {
        "universe_percentile": _percentile(universe_values, item_value),
        "sector_percentile": _percentile(by_sector[candidate.security.sector_canonical], item_value),
        "size_bucket_percentile": _percentile(by_size[size_bucket(value(candidate, "market_cap_usd", None))], item_value),
    } for candidate, item_value in known}


def size_bucket(market_cap_usd) -> str:
    if market_cap_usd is None:
        return "UNKNOWN"
    if market_cap_usd < 300_000_000:
        return "MICRO_SMALL"
    if market_cap_usd < 2_000_000_000:
        return "SMALL_MID"
    if market_cap_usd < 10_000_000_000:
        return "MID"
    return "LARGE"


def _percentile(values: list[float], target: float) -> float:
    if not values:
        return 0.0
    return round(sum(value <= target for value in values) / len(values) * 100, 4)


def rank_candidates(candidates: list[CandidateFeatureSnapshot], weights: dict[str, float] | None = None) -> list[CandidateFeatureSnapshot]:
    weights = weights or DEFAULT_WEIGHTS
    for candidate in candidates:
        candidate.scores = {
            "signal_strength": min(100, len(candidate.scanner_hits) * 25 + len(candidate.signal_families) * 15),
            "catalyst_quality": min(100, len(candidate.fuel_events) * 35),
            "expectation_gap": _feature_score(candidate, "expectation_gap"),
            "surge_elasticity": _feature_score(candidate, "surge_elasticity"),
            "entry_readiness": _entry_readiness(candidate),
            "fundamental_inflection": _fundamental_score(candidate),
            "sector_regime_fit": _feature_score(candidate, "sector_regime_fit"),
            "capital_structure_safety": _feature_score(candidate, "capital_structure_safety"),
            "liquidity_quality": _liquidity_score(candidate),
            "strategy_fit": 50.0,
            "data_confidence": max(0.0, 100.0 - len(set(candidate.unknown_fields)) * 5),
        }
        candidate.composite_score = round(sum(candidate.scores.get(key, 0) * weight for key, weight in weights.items()), 4)
        if candidate.eligibility == "ELIGIBLE":
            candidate.discovery_bucket = "P1_DEEP_ANALYSIS" if candidate.composite_score >= 65 else "P2_SECONDARY"
        elif candidate.eligibility == "REVIEW_REQUIRED":
            candidate.discovery_bucket = "WATCH"
        else:
            candidate.discovery_bucket = "REJECT"
    return sorted(candidates, key=lambda item: (-item.composite_score, item.security.ticker))


def _feature_score(candidate, name: str) -> float:
    return float(value(candidate, name, 50.0)) if known_field(candidate, name) else 50.0


def _entry_readiness(candidate) -> float:
    return {"DISCOVERY_STAGE_0": 80.0, "DISCOVERY_STAGE_1": 80.0,
            "DISCOVERY_STAGE_4": 65.0, "DISCOVERY_STAGE_2": 50.0}.get(candidate.stage, 0.0)


def _fundamental_score(candidate) -> float:
    if not known_field(candidate, "revenue_growth_acceleration"):
        return 0.0
    return max(0.0, min(100.0, 50 + float(value(candidate, "revenue_growth_acceleration")) * 2))


def _liquidity_score(candidate) -> float:
    if not known_field(candidate, "adv20_usd"):
        return 0.0
    return min(100.0, float(value(candidate, "adv20_usd")) / 1_000_000)
