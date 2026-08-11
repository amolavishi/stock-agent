from __future__ import annotations

import hashlib
import json
from typing import Any


REQUIRED_PACKET_FIELDS = ("ticker", "discovery_paths", "scanner_hits", "stage", "market_regime",
                          "sector_rotation_phase", "why_it_can_move", "catalysts", "numeric_evidence",
                          "expectation_gap", "price_position", "capital_structure", "data_unknowns",
                          "bull_case", "bear_case", "failure_scenarios", "certification_status",
                          "certified_action", "invalidation", "evidence_ids", "evidence_cutoff", "packet_expiry")


def build_packet(candidate: dict[str, Any], regime: dict[str, Any], sector: dict[str, Any],
                 certification: dict[str, Any] | None = None) -> dict[str, Any]:
    packet = {
        "ticker": candidate.get("ticker", candidate.get("security", {}).get("ticker", "")),
        "discovery_paths": candidate.get("paths", []), "scanner_hits": candidate.get("scanner_hits", []),
        "stage": candidate.get("stage", "DISCOVERY_STAGE_UNKNOWN"), "market_regime": regime.get("regime", "UNKNOWN"),
        "sector_rotation_phase": sector.get("rotation_phase", "UNAVAILABLE"),
        "why_it_can_move": candidate.get("fuel", candidate.get("fuel_events", [])),
        "catalysts": candidate.get("fuel_events", []), "numeric_evidence": candidate.get("fields", {}),
        "expectation_gap": candidate.get("scores", {}).get("expectation_gap"),
        "price_position": candidate.get("scores", {}).get("entry_readiness"),
        "capital_structure": candidate.get("capital_structure", {}),
        "data_unknowns": candidate.get("unknown_fields", []), "bull_case": [], "bear_case": [],
        "failure_scenarios": [],
        "certification_status": (certification or {}).get("certification_status", "UNKNOWN"),
        "certified_action": (certification or {}).get("action", "NONE"),
        "invalidation": candidate.get("invalidation_conditions", []),
        "evidence_ids": candidate.get("evidence_ids", []),
        "evidence_cutoff": candidate.get("evidence_cutoff", ""),
        "packet_expiry": candidate.get("expires_at", ""),
    }
    return packet


def validate_packet(packet: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [field for field in REQUIRED_PACKET_FIELDS if field not in packet]
    return not missing, missing


def packet_hash(packet: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
