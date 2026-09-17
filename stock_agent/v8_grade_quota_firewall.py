"""Grade-quota firewall for V8 MAIN.

The legacy V8 bridge exposed ``target_verified_a_minus_or_better=5`` in its
Discovery telemetry.  Even though the current Step16..18 packet builder does
not intentionally consume that funnel row, retaining grade-supply language in
an active production contract is unnecessary anchoring risk.

This patch converts that field into a pure search-expansion invariant and adds
all grade-supply/quota keys to the blind-packet scrub set.  It does not change
any A/A- threshold or create a candidate.
"""
from __future__ import annotations

from typing import Any

from . import v8_primary

V8_GRADE_QUOTA_FIREWALL_VERSION = "V8_GRADE_QUOTA_FIREWALL_V1.0"
_FORBIDDEN_QUOTA_KEYS = {
    "target_verified_a_minus_or_better",
    "verified_a_minus_or_better_count",
    "verified_a_count",
    "verified_a_minus_count",
    "candidate_shortage",
    "grade_quota",
    "grade_target",
    "required_a_count",
    "remaining_a_needed",
}
_INSTALLED = False
_ORIGINAL_BUILD = v8_primary.build_v8_discovery_contract


def build_quota_free_v8_discovery_contract(candidate_count: int) -> dict[str, Any]:
    packet = dict(_ORIGINAL_BUILD(candidate_count))
    for key in _FORBIDDEN_QUOTA_KEYS:
        packet.pop(key, None)
    packet.update({
        "grade_quota_forbidden": True,
        "a_count_is_output_not_target": True,
        "candidate_shortage_may_only_expand_search": True,
        "candidate_shortage_may_never_relax_certification": True,
        "search_expansion_trigger": "INSUFFICIENT_VERIFIED_SUPPLY_OR_SEARCH_DEBT",
    })
    if _FORBIDDEN_QUOTA_KEYS.intersection(packet):
        raise ValueError("V8 grade-quota firewall failed")
    return packet


def install_v8_grade_quota_firewall() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    v8_primary._DISCOVERY_SCORE_KEYS.update(_FORBIDDEN_QUOTA_KEYS)
    v8_primary._BLIND_KEYS.update(_FORBIDDEN_QUOTA_KEYS)
    v8_primary.build_v8_discovery_contract = build_quota_free_v8_discovery_contract  # type: ignore[assignment]
    _INSTALLED = True
