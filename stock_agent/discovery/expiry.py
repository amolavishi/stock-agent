from __future__ import annotations

from datetime import datetime


def is_expired(expires_at: str, as_of: str) -> bool:
    return bool(expires_at and expires_at[:10] < as_of[:10])


def can_promote(candidate: dict, as_of: str) -> tuple[bool, str]:
    if is_expired(candidate.get("expires_at", ""), as_of):
        return False, "CANDIDATE_TTL_EXPIRED"
    if candidate.get("discovery_bucket") == "P1_DEEP_ANALYSIS" and not candidate.get("last_validated_at"):
        return False, "CANDIDATE_NOT_REVALIDATED"
    return True, "PASS"
