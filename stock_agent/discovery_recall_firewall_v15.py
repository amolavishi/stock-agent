"""Discovery Recall metadata firewall for V8 blind certification.

The forensic audit requires identical Step18 certification for identical
canonical evidence packets regardless of the path used to discover a ticker.
These fields are therefore routing metadata only and are stripped before blind
adversarial/certification packets are formed.
"""
from __future__ import annotations

from . import v8_primary

DISCOVERY_RECALL_FIREWALL_VERSION = "V8_DISCOVERY_RECALL_FIREWALL_V1.5"
DISCOVERY_ONLY_KEYS = {
    "research_value",
    "signal_strength",
    "scanner_id",
    "scanner_name",
    "scanner_priority",
    "scanner_receipt",
    "secondary_status",
    "secondary_queue",
    "near_miss",
    "near_miss_status",
    "rejection_sentinel",
    "sentinel_history",
    "discovery_disposition",
    "recommended_discovery_action",
    "verification_path",
    "recheck_trigger",
}
_INSTALLED = False


def install_discovery_recall_firewall_v15() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    v8_primary._BLIND_KEYS.update(DISCOVERY_ONLY_KEYS)
    v8_primary._DISCOVERY_SCORE_KEYS.update(DISCOVERY_ONLY_KEYS)
    _INSTALLED = True
