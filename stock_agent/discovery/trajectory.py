from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateTrajectory:
    ticker: str
    score_t: float
    score_t_minus_1: float | None
    score_delta: float | None
    stage_transition: str
    scanner_hit_delta: int
    fuel_added: tuple[str, ...]
    fuel_expired: tuple[str, ...]
    rank_delta: int | None
    repeated_without_new_fuel: bool


def compare_snapshots(current: dict, previous: dict | None) -> CandidateTrajectory:
    ticker = current["ticker"]
    if not previous:
        return CandidateTrajectory(ticker, current.get("score", 0), None, None,
                                   f"UNKNOWN->{current.get('stage', 'UNKNOWN')}",
                                   len(current.get("scanner_hits", [])), tuple(current.get("fuel", [])), (), None, False)
    current_fuel, previous_fuel = set(current.get("fuel", [])), set(previous.get("fuel", []))
    current_hits, previous_hits = set(current.get("scanner_hits", [])), set(previous.get("scanner_hits", []))
    return CandidateTrajectory(
        ticker=ticker, score_t=current.get("score", 0), score_t_minus_1=previous.get("score", 0),
        score_delta=current.get("score", 0) - previous.get("score", 0),
        stage_transition=f"{previous.get('stage', 'UNKNOWN')}->{current.get('stage', 'UNKNOWN')}",
        scanner_hit_delta=len(current_hits) - len(previous_hits),
        fuel_added=tuple(sorted(current_fuel - previous_fuel)),
        fuel_expired=tuple(sorted(previous_fuel - current_fuel)),
        rank_delta=(previous.get("rank", 0) - current.get("rank", 0)),
        repeated_without_new_fuel=bool(current_fuel == previous_fuel and ticker == previous.get("ticker")))
