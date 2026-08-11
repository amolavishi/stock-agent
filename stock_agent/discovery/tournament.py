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
    a_eligible = _eligible(a)
    b_eligible = _eligible(b)
    if a_eligible != b_eligible:
        winner = a if a_eligible else b
        loser = b if a_eligible else a
        return PairwiseComparison(winner["ticker"], loser["ticker"],
                                  ("FINAL_ELIGIBILITY_ADVANTAGE",), (), ())
    if not a_eligible:
        return PairwiseComparison("NONE", "NONE", ("FINAL_ELIGIBILITY_REQUIRED",), (), ())
    axes = ("signal_strength", "catalyst_quality", "expectation_gap", "surge_elasticity",
            "entry_readiness", "capital_structure_safety", "strategy_fit", "data_confidence")
    a_meta = scorecard_metadata(a.get("scores", {}), a.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT))
    b_meta = scorecard_metadata(b.get("scores", {}), b.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT))
    a_coverage = float(a.get("final_scorecard_coverage_pct", a_meta["final_scorecard_coverage_pct"]))
    b_coverage = float(b.get("final_scorecard_coverage_pct", b_meta["final_scorecard_coverage_pct"]))
    a_known = {axis for axis in SCORECARD_AXES if _numeric(a.get("scores", {}).get(axis)) is not None}
    b_known = {axis for axis in SCORECARD_AXES if _numeric(b.get("scores", {}).get(axis)) is not None}
    shared = [axis for axis in axes if _numeric(a.get("scores", {}).get(axis)) is not None and
              _numeric(b.get("scores", {}).get(axis)) is not None]
    if not shared:
        return PairwiseComparison("NONE", "NONE", ("NO_COMPARABLE_SCORECARD_AXIS",), (), ())
    a_score = sum(_numeric(a["scores"][axis]) for axis in shared) / len(shared)
    b_score = sum(_numeric(b["scores"][axis]) for axis in shared) / len(shared)
    if a_score == b_score:
        if a_coverage != b_coverage:
            winner, loser = (a, b) if a_coverage > b_coverage else (b, a)
            return PairwiseComparison(winner["ticker"], loser["ticker"],
                                      ("SCORECARD_COVERAGE_TIE_BREAK",), (), ())
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
    return final_selection_diagnostics(candidates, portfolio_context)["ticker"]


def final_selection_diagnostics(candidates: list[dict], portfolio_context: dict | None = None) -> dict:
    """Return selection plus auditable reasons for an executable NONE."""
    certified = [candidate for candidate in candidates if candidate.get("certified") is True]
    if not certified:
        return {"ticker": "NONE", "reason_codes": ["NO_CERTIFIED_CHILD"],
                "filtered_candidates": []}
    eligible = [candidate for candidate in certified if _eligible(candidate)]
    if not eligible:
        return {"ticker": "NONE", "reason_codes": ["FINAL_SCORECARD_INELIGIBLE"],
                "filtered_candidates": [{"ticker": candidate.get("ticker", ""),
                                         "reason_codes": ["FINAL_SCORECARD_INELIGIBLE"]}
                                        for candidate in certified]}
    portfolio_context = portfolio_context or {}
    if portfolio_context.get("portfolio_context_status") != "READY":
        return {"ticker": "NONE", "reason_codes": ["PORTFOLIO_CONTEXT_UNKNOWN"],
                "filtered_candidates": []}
    if float(portfolio_context.get("remaining_risk_budget_usd", 0) or 0) <= 0:
        return {"ticker": "NONE", "reason_codes": ["PORTFOLIO_RISK_BUDGET_EXHAUSTED"],
                "filtered_candidates": []}
    sector_cap = portfolio_context.get("sector_cap_pct")
    existing_sector = portfolio_context.get("existing_sector_exposure_pct", {})
    pending_sector = portfolio_context.get("pending_sector_exposure_pct", {})
    existing_ticker = portfolio_context.get("existing_ticker_exposure_pct", {})
    pending_ticker = portfolio_context.get("pending_ticker_exposure_pct", {})
    filtered = []
    filtered_candidates = []
    for candidate in eligible:
        ticker = candidate.get("ticker", "")
        block_reasons = []
        if (float(existing_ticker.get(ticker, 0) or 0) +
                float(pending_ticker.get(ticker, 0) or 0)) > 0:
            block_reasons.append("PORTFOLIO_TICKER_EXPOSURE_BLOCK")
        sector = candidate.get("sector", "")
        committed_sector = (float(existing_sector.get(sector, 0) or 0) +
                            float(pending_sector.get(sector, 0) or 0))
        if sector_cap is not None and committed_sector >= float(sector_cap):
            block_reasons.append("PORTFOLIO_SECTOR_CAP_BLOCK")
        if block_reasons:
            filtered_candidates.append({"ticker": ticker, "reason_codes": block_reasons})
            continue
        filtered.append(candidate)
    if not filtered:
        reason_codes = sorted({reason for item in filtered_candidates
                               for reason in item["reason_codes"]})
        reason_codes.append("NO_EXECUTABLE_FINAL")
        return {"ticker": "NONE", "reason_codes": reason_codes,
                "filtered_candidates": filtered_candidates}
    pareto_front = [candidate for candidate in filtered if not any(
        _dominates(other, candidate) for other in filtered if other is not candidate)]
    if len(pareto_front) == 1:
        return {"ticker": pareto_front[0]["ticker"],
                "reason_codes": ["CERTIFIED_CHILD_SELECTED"],
                "filtered_candidates": filtered_candidates}
    tournament_candidates = pareto_front
    winner = tournament_candidates[0]["ticker"]
    for candidate in tournament_candidates[1:]:
        comparison = compare_candidates(next(item for item in tournament_candidates if item["ticker"] == winner), candidate)
        if comparison.winner == candidate["ticker"]:
            winner = candidate["ticker"]
        elif comparison.winner == "NONE":
            return {"ticker": "NONE", "reason_codes": ["TOURNAMENT_NO_WINNER", "NO_EXECUTABLE_FINAL"],
                    "filtered_candidates": filtered_candidates}
    return {"ticker": winner, "reason_codes": ["CERTIFIED_CHILD_SELECTED"],
            "filtered_candidates": filtered_candidates}


_PARETO_AXES = SCORECARD_AXES


def _dominates(a: dict, b: dict) -> bool:
    a_meta = scorecard_metadata(a.get("scores", {}), a.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT))
    b_meta = scorecard_metadata(b.get("scores", {}), b.get("min_scorecard_coverage_pct", DEFAULT_MIN_SCORECARD_COVERAGE_PCT))
    a_known = {axis for axis in SCORECARD_AXES if _numeric(a.get("scores", {}).get(axis)) is not None}
    b_known = {axis for axis in SCORECARD_AXES if _numeric(b.get("scores", {}).get(axis)) is not None}
    # Coverage is an eligibility/confidence signal, not an automatic Pareto
    # win.  Candidates with different known-axis sets must be compared by
    # actual shared score quality in compare_candidates().
    if a_known != b_known:
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
