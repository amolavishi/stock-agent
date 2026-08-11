from __future__ import annotations

from .features import known_field, value
from .schemas import CandidateFeatureSnapshot


DEFAULT_WEIGHTS = {"signal_strength": 0.10, "catalyst_quality": 0.16, "expectation_gap": 0.12,
                   "surge_elasticity": 0.08, "entry_readiness": 0.14, "fundamental_inflection": 0.12,
                   "sector_regime_fit": 0.08, "capital_structure_safety": 0.08,
                   "liquidity_quality": 0.05, "strategy_fit": 0.05}


def preliminary_priority_score(candidate: CandidateFeatureSnapshot) -> tuple[float, float]:
    """Rank cheap market survivors for enrichment without a market-cap bonus.

    The returned coverage is the share of known preliminary axes.  Unknown is
    omitted, never converted to a neutral score.
    """
    effective_strengths = [float(event.get("effective_strength", 0) or 0) for event in candidate.fuel_events]
    values = {
        "signal": min(100.0, len(candidate.scanner_hits) * 25 + len(candidate.signal_families) * 15),
        "fuel": min(100.0, max(effective_strengths, default=0.0)),
        "entry": _entry_readiness(candidate),
        "relative": (min(100.0, max(0.0, 50.0 + float(value(candidate, "return_20d_pct")) * 2))
                     if known_field(candidate, "return_20d_pct") else None),
        "sector": _feature_score(candidate, "sector_regime_fit"),
        "convergence": min(100.0, len(candidate.signal_families) * 25.0),
        "confidence": (candidate.data_confidence if candidate.score_coverage_pct else None),
        "overheat": (max(0.0, float(value(candidate, "overheat_penalty")))
                     if known_field(candidate, "overheat_penalty") else 0.0),
    }
    weights = {"signal": .20, "fuel": .20, "entry": .15, "relative": .15,
               "sector": .10, "convergence": .10, "confidence": .10}
    known_weight = sum(weight for name, weight in weights.items() if values[name] is not None)
    score = ((sum(float(values[name]) * weight for name, weight in weights.items()
                  if values[name] is not None) / known_weight) - values["overheat"]
             if known_weight else 0.0)
    return (round(max(0.0, min(100.0, score)), 4),
            round(known_weight / sum(weights.values()) * 100, 4))


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
        raw_scores = {
            "signal_strength": min(100, len(candidate.scanner_hits) * 25 + len(candidate.signal_families) * 15),
            "catalyst_quality": min(100, len(candidate.fuel_events) * 35),
            "expectation_gap": _feature_score(candidate, "expectation_gap"),
            "surge_elasticity": _feature_score(candidate, "surge_elasticity"),
            "entry_readiness": _entry_readiness(candidate),
            "fundamental_inflection": _fundamental_score(candidate),
            "sector_regime_fit": _feature_score(candidate, "sector_regime_fit"),
            "capital_structure_safety": _feature_score(candidate, "capital_structure_safety"),
            "liquidity_quality": _liquidity_score(candidate),
            "strategy_fit": _feature_score(candidate, "strategy_fit"),
        }
        known_weight = sum(weights.get(key, 0) for key, score in raw_scores.items() if score is not None)
        total_weight = sum(weights.values()) or 1.0
        candidate.score_coverage_pct = round(known_weight / total_weight * 100, 4)
        candidate.data_confidence = round(min(100.0, candidate.score_coverage_pct), 4)
        candidate.scores = {key: float(score) for key, score in raw_scores.items() if score is not None}
        candidate.composite_score = round(
            sum(score * weights.get(key, 0) for key, score in raw_scores.items() if score is not None)
            / known_weight * total_weight if known_weight else 0.0, 4)
        mandatory_known = (
            candidate.stage not in {"DISCOVERY_STAGE_UNKNOWN", ""}
            and candidate.gate_results.get("fuel_gate") == "PASS"
            and known_field(candidate, "current_price")
            and known_field(candidate, "market_cap_usd")
            and known_field(candidate, "adv20_usd")
            and known_field(candidate, "capital_overhang_status")
            and candidate.fields.get("capital_overhang_status").known
            and candidate.gate_results.get("final_candidate_gate") == "PASS"
        )
        # Preliminary PENDING_ENRICHMENT is not a final veto.  Only an
        # explicitly evaluated final FAIL may remove a candidate here.
        if candidate.eligibility == "ELIGIBLE" and candidate.gate_results.get("fuel_gate") == "FAIL":
            candidate.eligibility = "INELIGIBLE"
        if candidate.eligibility == "ELIGIBLE" and mandatory_known and candidate.score_coverage_pct >= 70 and candidate.composite_score >= 65:
            candidate.discovery_bucket = "P1_DEEP_ANALYSIS"
        elif candidate.eligibility == "ELIGIBLE":
            candidate.discovery_bucket = "WATCH" if not mandatory_known else "P2_SECONDARY"
        elif candidate.eligibility == "REVIEW_REQUIRED":
            candidate.discovery_bucket = "WATCH"
        else:
            candidate.discovery_bucket = "REJECT"
    return sorted(candidates, key=lambda item: (-item.composite_score, item.security.ticker))


def _feature_score(candidate, name: str) -> float | None:
    return float(value(candidate, name)) if known_field(candidate, name) else None


def _entry_readiness(candidate) -> float | None:
    return {"DISCOVERY_STAGE_0": 80.0, "DISCOVERY_STAGE_1": 80.0,
            "DISCOVERY_STAGE_4": 65.0, "DISCOVERY_STAGE_2": 50.0}.get(candidate.stage)


def _fundamental_score(candidate) -> float | None:
    field_name = ("revenue_growth_acceleration_pp" if known_field(candidate, "revenue_growth_acceleration_pp")
                  else "revenue_growth_acceleration")
    if not known_field(candidate, field_name):
        return None
    return max(0.0, min(100.0, 50 + float(value(candidate, field_name)) * 2))


def _liquidity_score(candidate) -> float | None:
    if not known_field(candidate, "adv20_usd"):
        return None
    return min(100.0, float(value(candidate, "adv20_usd")) / 1_000_000)
