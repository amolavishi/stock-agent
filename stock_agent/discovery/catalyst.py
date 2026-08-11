from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class CatalystEvent:
    event_id: str
    ticker: str
    event_type: str
    event_at: str
    first_seen_at: str
    half_life_days: float
    source_evidence_id: str
    materiality: str = "SUPPORTING"
    status: str = "KNOWN"
    expiry_at: str = ""

    def freshness_weight(self, as_of: str) -> float:
        age = max(0.0, (datetime.fromisoformat(as_of.replace("Z", "+00:00"))
                        - datetime.fromisoformat(self.event_at.replace("Z", "+00:00"))).total_seconds() / 86400)
        return round(math.exp(-math.log(2) * age / max(self.half_life_days, 0.0001)), 8)


def catalyst_expired(event: CatalystEvent, as_of: str) -> bool:
    return bool(event.expiry_at and event.expiry_at[:10] < as_of[:10])
