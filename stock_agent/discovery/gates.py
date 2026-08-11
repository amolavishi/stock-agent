from __future__ import annotations

from dataclasses import dataclass

from .features import known_field, value
from .schemas import CandidateFeatureSnapshot


@dataclass(frozen=True)
class DiscoveryGateRules:
    min_price: float = 3.0
    min_market_cap_usd: float = 300_000_000.0
    min_adv20_usd: float = 10_000_000.0


def global_gate(candidate: CandidateFeatureSnapshot, rules: DiscoveryGateRules) -> tuple[str, list[str]]:
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
    if candidate.stage in {"DISCOVERY_STAGE_3", "DISCOVERY_STAGE_UNKNOWN"}:
        reasons.append("STAGE_NOT_EXECUTABLE")
    if reasons:
        return ("REVIEW_REQUIRED" if any(reason.endswith("UNVERIFIED") for reason in reasons) else "INELIGIBLE", reasons)
    return "ELIGIBLE", []


def route_gate(candidate: CandidateFeatureSnapshot, scanner_name: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    r20 = value(candidate, "return_20d_pct")
    if scanner_name == "MOMENTUM_INFLECTION" and r20 is not None and r20 >= 40:
        reasons.append("MOMENTUM_ALREADY_EXTENDED")
    if scanner_name == "GENERAL_INFLECTION" and candidate.stage not in {"DISCOVERY_STAGE_0", "DISCOVERY_STAGE_1", "DISCOVERY_STAGE_4"}:
        reasons.append("GENERAL_STAGE_ROUTE_MISMATCH")
    return not reasons, reasons
