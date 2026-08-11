from __future__ import annotations

from collections import defaultdict
from statistics import median

from .features import known_field, value
from .schemas import CandidateFeatureSnapshot


def rank_sectors(candidates: list[CandidateFeatureSnapshot]) -> list[dict]:
    groups: dict[str, list[CandidateFeatureSnapshot]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.security.sector_canonical].append(candidate)
    rows = []
    for sector, members in groups.items():
        returns = [value(item, "return_20d_pct") for item in members if known_field(item, "return_20d_pct")]
        recent = [value(item, "return_5d_pct") for item in members if known_field(item, "return_5d_pct")]
        if not returns:
            rows.append({"sector": sector, "rotation_phase": "UNAVAILABLE", "rotation_score": None,
                         "member_count": len(members), "coverage_pct": 0.0})
            continue
        level, breadth = median(returns), sum(item > 0 for item in returns) / len(returns) * 100
        acceleration = median(recent) if recent else None
        if level >= 30 and breadth <= 60:
            phase = "OVERHEATED"
        elif (acceleration is not None and acceleration > 0) and level < 15:
            phase = "EARLY_INFLECTION"
        elif level >= 15 and breadth >= 60:
            phase = "CONFIRMED_ROTATION"
        elif level >= 30:
            phase = "MATURE_TREND"
        elif level < -15:
            phase = "ROLLING_OVER"
        else:
            phase = "DORMANT"
        score = round(max(0, min(100, 50 + level + breadth * 0.2 + (acceleration or 0))), 4)
        rows.append({"sector": sector, "rotation_phase": phase, "rotation_score": score,
                     "member_count": len(members), "coverage_pct": round(len(returns) / len(members) * 100, 4),
                     "median_return_20d": level, "breadth_pct_positive_20d": round(breadth, 4),
                     "breadth_acceleration": acceleration})
    return sorted(rows, key=lambda row: (row["rotation_score"] is None, -(row["rotation_score"] or 0), row["sector"]))
