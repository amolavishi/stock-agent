from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PairwiseComparison:
    winner: str
    loser: str
    reason_codes: tuple[str, ...]
    material_advantages: tuple[str, ...]
    material_disadvantages: tuple[str, ...]


def compare_candidates(a: dict, b: dict) -> PairwiseComparison:
    if a.get("certified") is not True or b.get("certified") is not True:
        return PairwiseComparison("NONE", "NONE", ("CERTIFICATION_REQUIRED",), (), ())
    axes = ("signal_strength", "catalyst_quality", "expectation_gap", "surge_elasticity",
            "entry_readiness", "capital_structure_safety", "strategy_fit", "reward_risk",
            "data_confidence", "decision_confidence")
    shared = [axis for axis in axes if _numeric(a.get("scores", {}).get(axis)) is not None and
              _numeric(b.get("scores", {}).get(axis)) is not None]
    if not shared:
        return PairwiseComparison("NONE", "NONE", ("NO_COMPARABLE_SCORECARD_AXIS",), (), ())
    a_score = sum(_numeric(a["scores"][axis]) for axis in shared) / len(shared)
    b_score = sum(_numeric(b["scores"][axis]) for axis in shared) / len(shared)
    if a_score == b_score:
        return PairwiseComparison("NONE", "NONE", ("NO_MATERIAL_ADVANTAGE",), (), ())
    winner, loser = (a, b) if a_score > b_score else (b, a)
    return PairwiseComparison(winner["ticker"], loser["ticker"], ("PAIRWISE_SCORE_ADVANTAGE",),
                              tuple(axis for axis in shared if _numeric(winner.get("scores", {}).get(axis)) > _numeric(loser.get("scores", {}).get(axis))),
                              tuple(axis for axis in shared if _numeric(winner.get("scores", {}).get(axis)) < _numeric(loser.get("scores", {}).get(axis))))


def _numeric(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _eligible(candidate: dict) -> bool:
    return (candidate.get("certified") is True and
            candidate.get("decision") in {"BUY", "CONDITIONAL_BUY"} and
            candidate.get("risk_hard_filter_pass") is True and
            candidate.get("trade_plan_valid") is True and
            candidate.get("market_fresh") is True and
            candidate.get("no_material_unresolved_blocker") is True)


def final_selection(candidates: list[dict], portfolio_context: dict | None = None) -> str:
    certified = [candidate for candidate in candidates if _eligible(candidate)]
    if not certified:
        return "NONE"
    portfolio_context = portfolio_context or {}
    if float(portfolio_context.get("remaining_risk_budget_usd", 1) or 0) <= 0:
        return "NONE"
    sector_cap = portfolio_context.get("sector_cap_pct")
    existing_sector = portfolio_context.get("existing_sector_exposure_pct", {})
    existing_ticker = portfolio_context.get("existing_ticker_exposure_pct", {})
    filtered = []
    for candidate in certified:
        ticker = candidate.get("ticker", "")
        if float(existing_ticker.get(ticker, 0) or 0) > 0:
            continue
        if sector_cap is not None and float(existing_sector.get(candidate.get("sector", ""), 0) or 0) >= float(sector_cap):
            continue
        filtered.append(candidate)
    certified = filtered
    if not certified:
        return "NONE"
    pareto_front = [candidate for candidate in certified if not any(
        _dominates(other, candidate) for other in certified if other is not candidate)]
    if len(pareto_front) == 1:
        return pareto_front[0]["ticker"]
    certified = pareto_front
    winner = certified[0]["ticker"]
    for candidate in certified[1:]:
        comparison = compare_candidates(next(item for item in certified if item["ticker"] == winner), candidate)
        if comparison.winner == candidate["ticker"]:
            winner = candidate["ticker"]
        elif comparison.winner == "NONE":
            return "NONE"
    return winner


_PARETO_AXES = ("signal_strength", "catalyst_quality", "expectation_gap",
                "surge_elasticity", "entry_readiness", "capital_structure_safety",
                "strategy_fit", "reward_risk", "data_confidence", "decision_confidence")


def _dominates(a: dict, b: dict) -> bool:
    shared = [axis for axis in _PARETO_AXES
              if _numeric(a.get("scores", {}).get(axis)) is not None
              and _numeric(b.get("scores", {}).get(axis)) is not None]
    if not shared:
        return False
    a_values = [_numeric(a["scores"][axis]) for axis in shared]
    b_values = [_numeric(b["scores"][axis]) for axis in shared]
    return all(left >= right for left, right in zip(a_values, b_values)) and any(
        left > right for left, right in zip(a_values, b_values))
