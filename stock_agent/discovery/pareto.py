from __future__ import annotations

from .schemas import CandidateFeatureSnapshot


PARETO_AXES = ("catalyst_quality", "entry_readiness", "capital_structure_safety",
               "fundamental_inflection", "data_confidence")


def pareto_filter(candidates: list[CandidateFeatureSnapshot]) -> list[CandidateFeatureSnapshot]:
    survivors = []
    for candidate in candidates:
        if candidate.eligibility != "ELIGIBLE":
            continue
        dominated = any(_dominates(other, candidate) for other in candidates
                       if other is not candidate and other.eligibility == "ELIGIBLE")
        if not dominated:
            survivors.append(candidate)
        else:
            candidate.risk_flags.append("PARETO_DOMINATED")
            candidate.discovery_bucket = "REJECT"
    return survivors


def _dominates(a: CandidateFeatureSnapshot, b: CandidateFeatureSnapshot) -> bool:
    shared = [axis for axis in PARETO_AXES if axis in a.scores and axis in b.scores]
    if not shared:
        return False
    av = [a.scores[axis] for axis in shared]
    bv = [b.scores[axis] for axis in shared]
    return all(left >= right for left, right in zip(av, bv)) and any(left > right for left, right in zip(av, bv))
