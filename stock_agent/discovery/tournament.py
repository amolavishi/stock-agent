from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PairwiseComparison:
    winner: str
    loser: str
    reason_codes: tuple[str, ...]
    material_advantages: tuple[str, ...]
    material_disadvantages: tuple[str, ...]


SCORECARD_AXES = ("signal_strength", "catalyst_quality", "expectation_gap",
                  "surge_elasticity", "entry_readiness", "capital_structure_safety",
                  "strategy_fit", "data_confidence", "reward_risk")
MANDATORY_SCORECARD_AXES = ("signal_strength", "catalyst_quality", "expectation_gap",
                            "entry_readiness", "capital_structure_safety", "reward_risk")
DEFAULT_MIN_SCORECARD_COVERAGE_PCT = 75.0
DEFAULT_MIN_REWARD_RISK = 1.5


def scorecard_metadata(scores: dict, min_coverage_pct: float = DEFAULT_MIN_SCORECARD_COVERAGE_PCT) -> dict:
    known = {axis for axis in SCORECARD_AXES if _numeric(scores.get(axis)) is not None}
    missing_mandatory = [axis for axis in MANDATORY_SCORECARD_AXES if axis not in known]
    total = len(SCORECARD_AXES)
    return {
        "final_scorecard_known_axes": len(known),
        "final_scorecard_total_axes": total,
        "final_scorecard_coverage_pct": round(len(known) / total * 100, 4),
        "final_scorecard_missing_mandatory": missing_mandatory,
        "final_scorecard_coverage_pass": len(known) / total * 100 >= float(min_coverage_pct),
    }


def compare_candidates(a: dict, b: dict) -> PairwiseComparison:
    if a.get("certified") is not True or b.get("certified") is not True:
        return PairwiseComparison("NONE", "NONE", ("CERTIFICATION_REQUIRED",), (), ())
    axes = ("signal_strength", "catalyst_quality", "expectation_gap", "surge_elasticity",
            "entry_readiness", "capital_structure_safety", "strategy_fit", "data_confidence")
    a_meta = scorecard_metadata(a.get("scores", {}), a.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT))
    b_meta = scorecard_metadata(b.get("scores", {}), b.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT))
    a_coverage = float(a.get("final_scorecard_coverage_pct", a_meta["final_scorecard_coverage_pct"]))
    b_coverage = float(b.get("final_scorecard_coverage_pct", b_meta["final_scorecard_coverage_pct"]))
    a_known = {axis for axis in SCORECARD_AXES if _numeric(a.get("scores", {}).get(axis)) is not None}
    b_known = {axis for axis in SCORECARD_AXES if _numeric(b.get("scores", {}).get(axis)) is not None}
    if a_known != b_known:
        if a_coverage > b_coverage:
            return PairwiseComparison(a["ticker"], b["ticker"],
                                      ("SCORECARD_COVERAGE_ADVANTAGE",), (), ())
        if b_coverage > a_coverage:
            return PairwiseComparison(b["ticker"], a["ticker"],
                                      ("SCORECARD_COVERAGE_ADVANTAGE",), (), ())
        return PairwiseComparison("NONE", "NONE", ("SCORECARD_NOT_COMPARABLE_MISSINGNESS",), (), ())
    if abs(a_coverage - b_coverage) >= 20:
        winner, loser = (a, b) if a_coverage > b_coverage else (b, a)
        return PairwiseComparison(winner["ticker"], loser["ticker"], ("SCORECARD_COVERAGE_ADVANTAGE",), (), ())
    shared = [axis for axis in axes if _numeric(a.get("scores", {}).get(axis)) is not None and
              _numeric(b.get("scores", {}).get(axis)) is not None]
    if not shared:
        return PairwiseComparison("NONE", "NONE", ("NO_COMPARABLE_SCORECARD_AXIS",), (), ())
    a_score = sum(_numeric(a["scores"][axis]) for axis in shared) / len(shared)
    b_score = sum(_numeric(b["scores"][axis]) for axis in shared) / len(shared)
    if a_score == b_score:
        return PairwiseComparison("NONE", "NONE", ("NO_MATERIAL_ADVANTAGE",), (), ())
    winner, loser = (a, b) if a_score > b_score else (b, a)
    winner_rr = _numeric(winner.get("scores", {}).get("reward_risk"))
    loser_rr = _numeric(loser.get("scores", {}).get("reward_risk"))
    if winner_rr is not None and loser_rr is not None and abs(a_score - b_score) < 2:
        if loser_rr > winner_rr:
            winner, loser = loser, winner
            return PairwiseComparison(winner["ticker"], loser["ticker"], ("REWARD_RISK_TIE_BREAK",), (), ())
    return PairwiseComparison(winner["ticker"], loser["ticker"], ("PAIRWISE_SCORE_ADVANTAGE",),
                              tuple(axis for axis in shared if _numeric(winner.get("scores", {}).get(axis)) > _numeric(loser.get("scores", {}).get(axis))),
                              tuple(axis for axis in shared if _numeric(winner.get("scores", {}).get(axis)) < _numeric(loser.get("scores", {}).get(axis))))


def _numeric(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _eligible(candidate: dict) -> bool:
    metadata = scorecard_metadata(candidate.get("scores", {}),
                                  candidate.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT))
    coverage = float(candidate.get("final_scorecard_coverage_pct", metadata["final_scorecard_coverage_pct"]))
    mandatory_missing = candidate.get("final_scorecard_missing_mandatory",
                                     metadata["final_scorecard_missing_mandatory"])
    reward_risk = _numeric(candidate.get("scores", {}).get("reward_risk"))
    return (candidate.get("certified") is True and
            candidate.get("decision") in {"BUY", "CONDITIONAL_BUY"} and
            candidate.get("risk_hard_filter_pass") is True and
            candidate.get("trade_plan_valid") is True and
            candidate.get("market_fresh") is True and
            candidate.get("no_material_unresolved_blocker") is True and
            coverage >= float(candidate.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT)) and
            not mandatory_missing and
            reward_risk is not None and
            reward_risk >= float(candidate.get("min_reward_risk", DEFAULT_MIN_REWARD_RISK)))


def final_selection(candidates: list[dict], portfolio_context: dict | None = None) -> str:
    certified = [candidate for candidate in candidates if _eligible(candidate)]
    if not certified:
        return "NONE"
    portfolio_context = portfolio_context or {}
    if float(portfolio_context.get("remaining_risk_budget_usd", 1) or 0) <= 0:
        return "NONE"
    sector_cap = portfolio_context.get("sector_cap_pct")
    existing_sector = portfolio_context.get("existing_sector_exposure_pct", {})
    pending_sector = portfolio_context.get("pending_sector_exposure_pct", {})
    existing_ticker = portfolio_context.get("existing_ticker_exposure_pct", {})
    pending_ticker = portfolio_context.get("pending_ticker_exposure_pct", {})
    filtered = []
    for candidate in certified:
        ticker = candidate.get("ticker", "")
        if (float(existing_ticker.get(ticker, 0) or 0) +
                float(pending_ticker.get(ticker, 0) or 0)) > 0:
            continue
        sector = candidate.get("sector", "")
        committed_sector = (float(existing_sector.get(sector, 0) or 0) +
                            float(pending_sector.get(sector, 0) or 0))
        if sector_cap is not None and committed_sector >= float(sector_cap):
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


_PARETO_AXES = SCORECARD_AXES


def _dominates(a: dict, b: dict) -> bool:
    a_meta = scorecard_metadata(a.get("scores", {}), a.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT))
    b_meta = scorecard_metadata(b.get("scores", {}), b.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT))
    a_coverage = float(a.get("final_scorecard_coverage_pct", a_meta["final_scorecard_coverage_pct"]))
    b_coverage = float(b.get("final_scorecard_coverage_pct", b_meta["final_scorecard_coverage_pct"]))
    if b_coverage - a_coverage >= 20:
        return False
    a_known = {axis for axis in SCORECARD_AXES if _numeric(a.get("scores", {}).get(axis)) is not None}
    b_known = {axis for axis in SCORECARD_AXES if _numeric(b.get("scores", {}).get(axis)) is not None}
    if b_known - a_known:
        return False
    shared = [axis for axis in _PARETO_AXES
              if _numeric(a.get("scores", {}).get(axis)) is not None
              and _numeric(b.get("scores", {}).get(axis)) is not None]
    if not shared:
        return False
    a_values = [_numeric(a["scores"][axis]) for axis in shared]
    b_values = [_numeric(b["scores"][axis]) for axis in shared]
    return all(left >= right for left, right in zip(a_values, b_values)) and any(
        left > right for left, right in zip(a_values, b_values))
