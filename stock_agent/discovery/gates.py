from __future__ import annotations

from dataclasses import dataclass

from .features import known_field, value
from .schemas import CandidateFeatureSnapshot


@dataclass(frozen=True)
class DiscoveryGateRules:
    min_price: float = 3.0
    min_market_cap_usd: float = 300_000_000.0
    min_adv20_usd: float = 10_000_000.0


def _status(reasons: list[str]) -> tuple[str, list[str]]:
    hard_reasons = {
        "UNSUPPORTED_EXCHANGE", "PRICE_BELOW_HARD_FLOOR", "MARKET_CAP_BELOW_HARD_FLOOR",
        "ADV20_BELOW_HARD_FLOOR", "ACTIVE_OR_UNRESOLVED_ATM", "CAPITAL_OVERHANG_HIGH_RISK",
        "STAGE_NOT_EXECUTABLE", "FUEL_GATE_FAIL", "NOT_COMMON_STOCK",
    }
    if any(reason in hard_reasons for reason in reasons):
        return "INELIGIBLE", reasons
    if reasons:
        return "REVIEW_REQUIRED", reasons
    return "ELIGIBLE", []


def _market_reasons(candidate: CandidateFeatureSnapshot, rules: DiscoveryGateRules) -> list[str]:
    reasons: list[str] = []
    if candidate.security.exchange.upper() not in {"NYSE", "NASDAQ", "NYSE AMERICAN", "NYSEAMERICAN", "AMEX"}:
        reasons.append("UNSUPPORTED_EXCHANGE")
    if not known_field(candidate, "current_price"):
        reasons.append("PRICE_UNKNOWN")
    elif value(candidate, "current_price") < rules.min_price:
        reasons.append("PRICE_BELOW_HARD_FLOOR")
    if not known_field(candidate, "market_cap_usd"):
        reasons.append("MARKET_CAP_UNVERIFIED")
    elif value(candidate, "market_cap_usd") < rules.min_market_cap_usd:
        reasons.append("MARKET_CAP_BELOW_HARD_FLOOR")
    if not known_field(candidate, "adv20_usd"):
        reasons.append("ADV20_UNVERIFIED")
    elif value(candidate, "adv20_usd") < rules.min_adv20_usd:
        reasons.append("ADV20_BELOW_HARD_FLOOR")
    if candidate.security.sector_canonical == "UNKNOWN":
        reasons.append("SECTOR_UNVERIFIED")
    if candidate.stage in {"DISCOVERY_STAGE_3", "DISCOVERY_STAGE_UNKNOWN"}:
        reasons.append("STAGE_NOT_EXECUTABLE")
    if candidate.gate_results.get("stage_gate") == "FAIL" and "STAGE_NOT_EXECUTABLE" not in reasons:
        reasons.append("STAGE_NOT_EXECUTABLE")
    return reasons


def market_screen_gate(candidate: CandidateFeatureSnapshot, rules: DiscoveryGateRules) -> tuple[str, list[str]]:
    """Cheap market gate; fuel and capital are intentionally out of scope."""
    status, reasons = _status(_market_reasons(candidate, rules))
    candidate.gate_results["market_gate"] = "PASS" if status == "ELIGIBLE" else status
    candidate.gate_results["market_gate_status"] = status
    return status, reasons


def final_candidate_gate(candidate: CandidateFeatureSnapshot, rules: DiscoveryGateRules,
                         require_capital: bool = True) -> tuple[str, list[str]]:
    """Final gate after enrichment; this is where fuel becomes a hard veto."""
    reasons = _market_reasons(candidate, rules)
    if "atm_status" in candidate.fields:
        if not candidate.fields["atm_status"].known:
            reasons.append("ATM_UNVERIFIED")
        elif str(candidate.fields["atm_status"].value).upper() in {"ACTIVE", "UNKNOWN"}:
            reasons.append("ACTIVE_OR_UNRESOLVED_ATM")
    if "capital_overhang_status" in candidate.fields:
        if not candidate.fields["capital_overhang_status"].known:
            reasons.append("CAPITAL_OVERHANG_UNVERIFIED")
        elif str(candidate.fields["capital_overhang_status"].value).upper() in {"HIGH_RISK", "UNKNOWN"}:
            reasons.append("CAPITAL_OVERHANG_HIGH_RISK")
    if candidate.gate_results.get("fuel_gate") != "PASS":
        reasons.append("FUEL_GATE_FAIL")
    if not require_capital:
        reasons = [reason for reason in reasons if reason not in {
            "ATM_UNVERIFIED", "ACTIVE_OR_UNRESOLVED_ATM",
            "CAPITAL_OVERHANG_UNVERIFIED", "CAPITAL_OVERHANG_HIGH_RISK",
        }]
    return _status(reasons)


def global_gate(candidate: CandidateFeatureSnapshot, rules: DiscoveryGateRules) -> tuple[str, list[str]]:
    """Backward-compatible alias for the strict final gate."""
    return final_candidate_gate(candidate, rules, require_capital=True)


def route_gate(candidate: CandidateFeatureSnapshot, scanner_name: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    r20 = value(candidate, "return_20d_pct")
    if scanner_name == "MOMENTUM_INFLECTION" and r20 is not None and r20 >= 40:
        reasons.append("MOMENTUM_ALREADY_EXTENDED")
    if scanner_name == "GENERAL_INFLECTION" and candidate.stage not in {"DISCOVERY_STAGE_0", "DISCOVERY_STAGE_1", "DISCOVERY_STAGE_4"}:
        reasons.append("GENERAL_STAGE_ROUTE_MISMATCH")
    return not reasons, reasons
