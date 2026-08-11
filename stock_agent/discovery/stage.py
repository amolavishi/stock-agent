from __future__ import annotations

from dataclasses import dataclass

from .features import known_field, value
from .schemas import CandidateFeatureSnapshot


@dataclass(frozen=True)
class DiscoveryStageRules:
    stage3_return_1d_pct: float = 25.0
    stage3_return_5d_pct: float = 35.0
    stage3_return_20d_pct: float = 50.0
    stage3_distance_ma20_pct: float = 20.0
    stage3_atr_multiple: float = 3.0


class DiscoveryStageEngine:
    def __init__(self, rules: DiscoveryStageRules | None = None):
        self.rules = rules or DiscoveryStageRules()

    def classify(self, candidate: CandidateFeatureSnapshot) -> str:
        required = ("return_20d_pct", "ma20", "ma50", "atr_pct", "current_price")
        if not all(known_field(candidate, field) for field in required):
            return "DISCOVERY_STAGE_UNKNOWN"
        current, ma20 = value(candidate, "current_price"), value(candidate, "ma20")
        ma50, atr = value(candidate, "ma50"), value(candidate, "atr_pct")
        if min(current, ma20, ma50, atr) <= 0:
            return "DISCOVERY_STAGE_UNKNOWN"
        r1, r5, r20 = (value(candidate, name) for name in ("return_1d_pct", "return_5d_pct", "return_20d_pct"))
        extension = (current / ma20 - 1) * 100
        if ((r1 is not None and r1 >= self.rules.stage3_return_1d_pct)
                or (r5 is not None and r5 >= self.rules.stage3_return_5d_pct)
                or (r20 is not None and r20 >= self.rules.stage3_return_20d_pct)
                or extension >= max(self.rules.stage3_distance_ma20_pct, self.rules.stage3_atr_multiple * atr)):
            return "DISCOVERY_STAGE_3"
        range_contraction = value(candidate, "range_contraction_20d")
        relative_volume = value(candidate, "relative_volume_completed_bar")
        if current < ma20 and current < ma50:
            return "DISCOVERY_STAGE_MINUS_1" if (r20 or 0) <= 0 else "DISCOVERY_STAGE_4"
        if range_contraction is True and (relative_volume is None or relative_volume <= 1.5):
            return "DISCOVERY_STAGE_0"
        if current >= ma20 and current <= ma50 * 1.03 and (r5 or 0) >= 0:
            return "DISCOVERY_STAGE_1"
        if current >= ma20 and current >= ma50:
            return "DISCOVERY_STAGE_2"
        return "DISCOVERY_STAGE_4"

    def apply(self, candidate: CandidateFeatureSnapshot) -> CandidateFeatureSnapshot:
        candidate.stage = self.classify(candidate)
        candidate.gate_results["stage_gate"] = "FAIL" if candidate.stage in {
            "DISCOVERY_STAGE_3", "DISCOVERY_STAGE_UNKNOWN"} else "PASS"
        if candidate.stage == "DISCOVERY_STAGE_3":
            candidate.risk_flags.append("REJECT_NEW_ENTRY")
        if candidate.stage == "DISCOVERY_STAGE_UNKNOWN" and "stage" not in candidate.unknown_fields:
            candidate.unknown_fields.append("stage")
        return candidate
