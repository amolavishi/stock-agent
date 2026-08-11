from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .schemas import EvidenceItem, now_iso
from .readiness import classify_offering_event


@dataclass
class ProvenancedValue:
    value: Any = None
    status: str = "UNKNOWN"
    source_accession: str = ""
    source_span: str = ""
    as_of: str = ""
    calculation_method: str = ""
    confidence: int = 0


def unknown(method: str = "NOT_OBSERVED") -> ProvenancedValue:
    return ProvenancedValue(status="UNKNOWN", calculation_method=method)


@dataclass
class CapitalStructureSnapshot:
    ticker: str
    as_of: str
    shares_outstanding: float | None = None
    share_growth_yoy: float | None = None
    atm_authorized_capacity: ProvenancedValue = field(default_factory=unknown)
    atm_active: ProvenancedValue = field(default_factory=unknown)
    atm_used_amount: ProvenancedValue = field(default_factory=unknown)
    atm_remaining_amount: ProvenancedValue = field(default_factory=unknown)
    atm_shares_issued: ProvenancedValue = field(default_factory=unknown)
    atm_average_sale_price: ProvenancedValue = field(default_factory=unknown)
    atm_last_verified_at: str = ""
    shelf_registered_capacity: ProvenancedValue = field(default_factory=unknown)
    shelf_effective: ProvenancedValue = field(default_factory=unknown)
    shelf_used_amount: ProvenancedValue = field(default_factory=unknown)
    shelf_remaining_estimate: ProvenancedValue = field(default_factory=unknown)
    warrant_authorized: ProvenancedValue = field(default_factory=unknown)
    warrant_offerable: ProvenancedValue = field(default_factory=unknown)
    warrant_outstanding: ProvenancedValue = field(default_factory=unknown)
    warrant_exercisable: ProvenancedValue = field(default_factory=unknown)
    warrant_share_equivalent: ProvenancedValue = field(default_factory=unknown)
    warrant_exercise_price: ProvenancedValue = field(default_factory=unknown)
    warrant_expiration: ProvenancedValue = field(default_factory=unknown)
    convertible_authorized: ProvenancedValue = field(default_factory=unknown)
    convertible_offerable: ProvenancedValue = field(default_factory=unknown)
    convertible_outstanding: ProvenancedValue = field(default_factory=unknown)
    convertible_principal: ProvenancedValue = field(default_factory=unknown)
    convertible_conversion_rate: ProvenancedValue = field(default_factory=unknown)
    convertible_conversion_price: ProvenancedValue = field(default_factory=unknown)
    convertible_share_equivalent: ProvenancedValue = field(default_factory=unknown)
    convertible_maturity: ProvenancedValue = field(default_factory=unknown)
    stock_compensation: float | None = None
    cash: float | None = None
    cash_burn: float | None = None
    runway_months: float | None = None
    known_fields: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    estimated_fields: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    integrity_conflicts: list[dict[str, Any]] = field(default_factory=list)
    offering_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def atm_capacity(self) -> float | None:
        return self.atm_authorized_capacity.value

    @property
    def recent_atm_usage(self) -> float | None:
        return self.atm_used_amount.value

    @property
    def warrants(self) -> str:
        if self.warrant_outstanding.status == "KNOWN":
            return "OUTSTANDING"
        if self.warrant_offerable.status == "KNOWN":
            return "OFFERABLE"
        return "UNKNOWN"

    @property
    def convertibles(self) -> str:
        if self.convertible_outstanding.status == "KNOWN":
            return "OUTSTANDING"
        if self.convertible_offerable.status == "KNOWN":
            return "OFFERABLE"
        if self.convertible_authorized.status == "KNOWN":
            return "AUTHORIZED"
        return "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["atm_capacity"] = self.atm_capacity
        payload["recent_atm_usage"] = self.recent_atm_usage
        payload["warrants"] = self.warrants
        payload["convertibles"] = self.convertibles
        payload["capital_overhang_status"] = self.capital_overhang_status
        return payload

    @property
    def capital_overhang_status(self) -> str:
        if self.atm_active.value is True or self.convertible_outstanding.value is True:
            return "HIGH_RISK"
        if self.warrant_outstanding.value is not None:
            return "REVIEW_REQUIRED"
        if any(event.get("offering_type") == "SELLING_STOCKHOLDER_RESALE" for event in self.offering_events):
            return "CLEAR"
        return "UNKNOWN"


def _fact_value(facts: dict[str, Any], name: str) -> float | None:
    row = facts.get(name)
    try:
        return float(row.get("value")) if row and row.get("value") is not None else None
    except (TypeError, ValueError):
        return None


def _money(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, flags=re.I | re.S)
    if not match:
        return None
    context = text[max(0, match.start() - 80):match.end() + 40]
    if re.search(r"(?:per\s+share|share\s+price|exercise\s+price|strike\s+price)",
                 context, flags=re.I):
        return None
    if re.search(r"\d\s*%", context):
        return None
    raw = match.group(1).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if value < 0:
        return None
    suffix = (match.group(2) or "").lower()
    return value * ({"b": 1e9, "m": 1e6, "k": 1e3}.get(suffix, 1))


def _provenance(item: EvidenceItem, value: Any, status: str, span: str,
                method: str, confidence: int) -> ProvenancedValue:
    return ProvenancedValue(value, status, item.accession, span[:500],
                            item.filed_at or item.published_at, method, confidence)


def _capital_context(text: str, start: int, end: int) -> str:
    return text[max(0, start - 140):min(len(text), end + 140)]


def _negative_outstanding_context(context: str) -> bool:
    return bool(re.search(
        r"\b(?:no|not|never|without|zero|none)\s+(?:longer\s+)?(?:being\s+)?outstanding\b|"
        r"\bno\s+longer\s+outstanding\b|"
        r"\b(?:fully|completely)\s+converted\b|"
        r"\bpreviously\s+outstanding\b|"
        r"\b(?:redeemed|retired|settled|extinguished|terminated)\b",
        context, flags=re.I))


def build_capital_structure(ticker: str, facts: dict[str, Any],
                            evidence: list[EvidenceItem]) -> CapitalStructureSnapshot:
    normalized = facts.get("normalized_facts", [])
    snapshot = CapitalStructureSnapshot(
        ticker=ticker,
        as_of=max((str(row.get("filed") or "") for row in normalized), default=now_iso()),
        shares_outstanding=_fact_value(facts, "shares_outstanding"),
        stock_compensation=_fact_value(facts, "stock_based_compensation"),
        cash=_fact_value(facts, "cash"),
        cash_burn=facts.get("derived", {}).get("cash_burn"),
        runway_months=facts.get("derived", {}).get("estimated_runway_months"),
    )
    for item in sorted(evidence, key=lambda value: value.filed_at or value.published_at):
        text = (item.normalized_fact or item.summary or "").lower()
        if not text:
            continue
        snapshot.offering_events.append(classify_offering_event(
            item.document_type, text, item.accession, item.filed_at or item.published_at))
        if any(term in text for term in ("at-the-market", "at the market", "equity distribution agreement")):
            snapshot.atm_active = _provenance(item, True, "KNOWN", text,
                                               "EXPLICIT_ATM_AGREEMENT_LANGUAGE", 95)
            snapshot.atm_last_verified_at = item.filed_at or item.published_at
            capacity = _money(text, r"(?:up to|aggregate offering price of up to)\s*\$\s*([0-9,.]+)\s*([bmk]?)")
            if capacity is not None:
                snapshot.atm_authorized_capacity = _provenance(
                    item, capacity, "KNOWN", text, "EXPLICIT_ATM_CAPACITY", 95)
            used = _money(text, r"(?:sold|issued)[^$]{0,120}\$\s*([0-9,.]+)\s*([bmk]?)")
            if used is not None:
                snapshot.atm_used_amount = _provenance(
                    item, used, "KNOWN", text, "EXPLICIT_ATM_UTILIZATION", 85)
            snapshot.evidence_ids.append(item.evidence_id)
        if "shelf registration" in text or item.document_type == "S-3":
            snapshot.shelf_effective = _provenance(item, True, "KNOWN", text,
                                                    "SHELF_REGISTRATION_FILING", 80)
            capacity = _money(text, r"(?:up to|aggregate amount of)\s*\$\s*([0-9,.]+)\s*([bmk]?)")
            if capacity is not None:
                snapshot.shelf_registered_capacity = _provenance(
                    item, capacity, "KNOWN", text, "EXPLICIT_SHELF_CAPACITY", 85)
            snapshot.evidence_ids.append(item.evidence_id)
        if re.search(r"\b(?:authorized|authorize)\b[^.]{0,80}\bwarrants?\b|"
                     r"\bwarrants?\b[^.]{0,80}\b(?:authorized|authorize)\b", text):
            snapshot.warrant_authorized = _provenance(
                item, True, "KNOWN", text, "AUTHORIZED_WARRANT_LANGUAGE", 80)
            snapshot.evidence_ids.append(item.evidence_id)
        if re.search(r"\b(?:may|could)\s+(?:offer|issue)[^.]{0,80}\bwarrants?\b", text):
            snapshot.warrant_offerable = _provenance(
                item, True, "KNOWN", text, "OFFERABLE_LANGUAGE", 90)
            snapshot.evidence_ids.append(item.evidence_id)
        outstanding = re.search(r"(?<![\w-])([0-9,]+)\s+warrants?\s+(?:were\s+)?outstanding\b", text)
        outstanding_context = (_capital_context(text, outstanding.start(), outstanding.end())
                               if outstanding else "")
        outstanding_count = (float(outstanding.group(1).replace(",", ""))
                             if outstanding else 0.0)
        if outstanding and outstanding_count > 0 and not re.search(
                r"\b(?:may|could|up\s+to|authorized|offerable|offered)\b",
                outstanding_context, flags=re.I) and not _negative_outstanding_context(
                    outstanding_context):
            snapshot.warrant_outstanding = _provenance(
                item, float(outstanding.group(1).replace(",", "")), "KNOWN",
                outstanding.group(0), "EXPLICIT_OUTSTANDING_DISCLOSURE", 95)
        for convertible in re.finditer(
                r"\bconvertible\s+(?:notes?|debt|debentures?)\b", text):
            context = _capital_context(text, convertible.start(), convertible.end())
            subject_context = _capital_context(text, convertible.start(),
                                               min(len(text), convertible.end() + 100))
            negative_outstanding = _negative_outstanding_context(context)
            if negative_outstanding:
                if re.search(r"\b(?:may|could)\s+(?:offer|issue)|"
                             r"\b(?:offerable|issuable)\b", subject_context, flags=re.I):
                    snapshot.convertible_offerable = _provenance(
                        item, True, "KNOWN", subject_context,
                        "CONVERTIBLE_OFFERABLE_LANGUAGE", 75)
                elif re.search(r"\b(?:authorized|authorize)\b", subject_context, flags=re.I):
                    snapshot.convertible_authorized = _provenance(
                        item, True, "KNOWN", subject_context,
                        "CONVERTIBLE_AUTHORIZATION_LANGUAGE", 70)
                continue
            after_term = text[convertible.end():min(len(text), convertible.end() + 100)]
            before_term = text[max(0, convertible.start() - 100):convertible.start()]
            has_outstanding_language = bool(
                re.search(r"^\s*(?:are|is|were|was|remain(?:s)?)\s+"
                          r"(?:currently\s+)?(?:issued|outstanding)\b",
                          after_term, flags=re.I) or
                re.search(r"\b(?:issued|outstanding)\s*$", before_term, flags=re.I))
            if has_outstanding_language:
                snapshot.convertible_outstanding = _provenance(
                    item, True, "KNOWN", subject_context,
                    "EXPLICIT_CONVERTIBLE_OUTSTANDING", 85)
            elif re.search(r"\b(?:may|could)\s+(?:offer|issue)|"
                           r"\b(?:offerable|issuable)\b", subject_context, flags=re.I):
                snapshot.convertible_offerable = _provenance(
                    item, True, "KNOWN", subject_context,
                    "CONVERTIBLE_OFFERABLE_LANGUAGE", 75)
            elif re.search(r"\b(?:authorized|authorize)\b", subject_context, flags=re.I):
                snapshot.convertible_authorized = _provenance(
                    item, True, "KNOWN", subject_context,
                    "CONVERTIBLE_AUTHORIZATION_LANGUAGE", 70)

    if (snapshot.atm_authorized_capacity.value is not None and
            snapshot.atm_used_amount.value is not None):
        snapshot.atm_remaining_amount = ProvenancedValue(
            max(0.0, snapshot.atm_authorized_capacity.value - snapshot.atm_used_amount.value),
            "ESTIMATED", snapshot.atm_authorized_capacity.source_accession,
            "capacity - disclosed usage", snapshot.atm_last_verified_at,
            "ATM_CAPACITY_MINUS_DISCLOSED_USAGE", 75)
        snapshot.estimated_fields.append("atm_remaining_amount")

    simple_fields = ("shares_outstanding", "share_growth_yoy", "stock_compensation",
                     "cash", "cash_burn", "runway_months")
    for name in simple_fields:
        (snapshot.known_fields if getattr(snapshot, name) is not None
         else snapshot.unknown_fields).append(name)
    metric_fields = (
        "atm_authorized_capacity", "atm_used_amount", "atm_remaining_amount",
        "shelf_registered_capacity", "shelf_used_amount", "warrant_outstanding",
        "convertible_authorized", "convertible_offerable", "convertible_outstanding",
    )
    for name in metric_fields:
        if getattr(snapshot, name).status == "UNKNOWN":
            snapshot.unknown_fields.append(name.replace("atm_authorized_capacity", "atm_capacity")
                                           .replace("atm_used_amount", "recent_atm_usage"))
        else:
            snapshot.known_fields.append(name)
    snapshot.evidence_ids = list(dict.fromkeys(snapshot.evidence_ids))
    return snapshot


def sector_from_sic(sic: str) -> str:
    try:
        value = int(sic)
    except (TypeError, ValueError):
        return "UNKNOWN"
    ranges = (
        (100, 999, "Natural Resources"), (2000, 3999, "Industrials/Manufacturing"),
        (3570, 3579, "Technology Hardware"), (3600, 3699, "Electronic Technology"),
        (4810, 4899, "Communication Services"), (4900, 4999, "Utilities"),
        (6000, 6799, "Financials"), (7370, 7379, "Software/IT Services"),
        (2830, 2839, "Biotechnology/Pharmaceuticals"), (8000, 8099, "Healthcare Services"),
    )
    for start, end, name in reversed(ranges):
        if start <= value <= end:
            return name
    return "UNKNOWN"
