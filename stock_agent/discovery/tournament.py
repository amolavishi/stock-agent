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
    axes = ("catalyst_quality", "expectation_gap", "entry_readiness",
            "capital_structure_safety", "data_confidence")
    a_score = sum(float(a.get("scores", {}).get(axis, 0)) for axis in axes)
    b_score = sum(float(b.get("scores", {}).get(axis, 0)) for axis in axes)
    if a_score == b_score:
        return PairwiseComparison("NONE", "NONE", ("NO_MATERIAL_ADVANTAGE",), (), ())
    winner, loser = (a, b) if a_score > b_score else (b, a)
    return PairwiseComparison(winner["ticker"], loser["ticker"], ("PAIRWISE_SCORE_ADVANTAGE",),
                              tuple(axis for axis in axes if winner.get("scores", {}).get(axis, 0) > loser.get("scores", {}).get(axis, 0)),
                              tuple(axis for axis in axes if winner.get("scores", {}).get(axis, 0) < loser.get("scores", {}).get(axis, 0)))


def final_selection(candidates: list[dict]) -> str:
    certified = [candidate for candidate in candidates if candidate.get("certified") is True]
    if not certified:
        return "NONE"
    winner = certified[0]["ticker"]
    for candidate in certified[1:]:
        comparison = compare_candidates(next(item for item in certified if item["ticker"] == winner), candidate)
        if comparison.winner == candidate["ticker"]:
            winner = candidate["ticker"]
        elif comparison.winner == "NONE":
            return "NONE"
    return winner
