from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


PERIODIC_STATES = {
    "DISCOVERED", "FETCHED", "PARSED", "PRIMARY_DOCUMENT_VALIDATED",
    "XBRL_LINKED", "XBRL_CROSS_VALIDATED", "READY_PARTIAL", "READY_FOR_ANALYSIS", "BLOCKED",
}


def periodic_filing_readiness(item: Any, companyfacts_accessions: set[str]) -> dict[str, Any]:
    """Return an auditable lifecycle state without treating accession lag as total failure."""
    accession = str(getattr(item, "accession", "") or "").replace("-", "")
    filed_at = str(getattr(item, "filed_at", "") or getattr(item, "published_at", "") or "")
    acceptance_datetime = str((getattr(item, "facts", {}) or {}).get("acceptance_datetime") or "")
    raw_hash = str(getattr(item, "raw_document_hash", "") or "")
    parsed = bool(getattr(item, "parsed_at", "") or getattr(item, "normalized_fact", ""))
    if not raw_hash:
        return {"state": "DISCOVERED", "reason_codes": ["RAW_FILING_NOT_FETCHED"],
                "numeric_claims": "BLOCKED", "accession": accession,
                "filed_at": filed_at, "acceptance_datetime": acceptance_datetime}
    if not parsed:
        return {"state": "FETCHED", "reason_codes": ["RAW_FILING_NOT_PARSED"],
                "numeric_claims": "BLOCKED", "accession": accession,
                "filed_at": filed_at, "acceptance_datetime": acceptance_datetime}
    if accession and accession in companyfacts_accessions:
        return {"state": "READY_FOR_ANALYSIS", "reason_codes": ["XBRL_CROSS_VALIDATED"],
                "numeric_claims": "READY", "accession": accession,
                "filed_at": filed_at, "acceptance_datetime": acceptance_datetime}
    return {"state": "READY_PARTIAL", "reason_codes": [
        "COMPANYFACTS_ACCESSION_LAG", "RAW_FILING_PARSED", "NUMERIC_XBRL_NOT_CROSS_VALIDATED"
    ], "numeric_claims": "BLOCKED", "accession": accession,
        "filed_at": filed_at, "acceptance_datetime": acceptance_datetime}


def classify_offering_event(document_type: str, text: str, accession: str = "",
                            filed_at: str = "") -> dict[str, Any]:
    """Classify issuance semantics conservatively; a resale is never an ATM by form alone."""
    lowered = str(text or "").lower()
    form = str(document_type or "").upper()
    if re.search(r"selling\s+stockholder|resale\s+registration|may\s+sell\s+from\s+time", lowered):
        kind = "SELLING_STOCKHOLDER_RESALE"
        creates = False
        proceeds = False
    elif "warrant" in lowered and "resale" in lowered:
        kind, creates, proceeds = "WARRANT_RESALE", False, False
    elif "at-the-market" in lowered or "at the market" in lowered or "equity distribution agreement" in lowered:
        kind, creates, proceeds = "ATM", True, True
    elif form == "S-8" or "employee stock plan" in lowered:
        kind, creates, proceeds = "EMPLOYEE_PLAN_S8", True, True
    elif "convertible" in lowered:
        kind, creates, proceeds = "CONVERTIBLE_OFFERING", True, True
    elif form in {"S-3", "S-1"} and ("shelf" in lowered or "registration statement" in lowered):
        kind, creates, proceeds = "SHELF_CAPACITY", True, True
    elif "block trade" in lowered:
        kind, creates, proceeds = "BLOCK_TRADE", False, False
    elif form in {"424B3", "424B5", "424B7", "424B8"}:
        kind, creates, proceeds = "UNKNOWN_OFFERING", None, None
    else:
        kind, creates, proceeds = "UNKNOWN_OFFERING", None, None
    return {"offering_type": kind, "status": "KNOWN" if kind != "UNKNOWN_OFFERING" else "UNKNOWN",
            "source_accession": accession, "filed_at": filed_at,
            "economic_effect": "ISSUER_PROCEEDS" if proceeds else "SECONDARY_OR_UNKNOWN",
            "new_share_creation_possible": creates,
            "issuer_receives_proceeds": proceeds, "confidence": 90 if kind != "UNKNOWN_OFFERING" else 0,
            "reason_codes": [f"OFFERING_CLASSIFIED_{kind}"], "atm_active": kind == "ATM"}


@dataclass(frozen=True)
class DataReadinessAssessment:
    status: str
    reason_codes: tuple[str, ...]
    periodic: tuple[dict[str, Any], ...] = ()
    offerings: tuple[dict[str, Any], ...] = ()

    @property
    def blocked(self) -> bool:
        return self.status != "READY"


class DataReadinessPreflight:
    @staticmethod
    def _filing_order(item: Any, readiness: dict[str, Any]) -> tuple[str, str, str]:
        """Use filing chronology first; accession is only a tie-breaker."""
        return (str(readiness.get("filed_at") or ""),
                str(readiness.get("acceptance_datetime") or ""),
                str(readiness.get("accession") or ""))

    def evaluate(self, evidence: list[Any], companyfacts_accessions: set[str],
                 capital_structure: dict[str, Any] | None = None) -> DataReadinessAssessment:
        periodic_items = [item for item in evidence if getattr(item, "document_type", "") in {"10-Q", "10-K"}]
        periodic = tuple(periodic_filing_readiness(item, companyfacts_accessions) for item in periodic_items)
        latest = max(periodic, key=lambda row: self._filing_order(None, row), default=None)
        reasons: list[str] = []
        if not latest:
            reasons.append("LATEST_MATERIAL_PERIODIC_FILING_MISSING")
        elif latest["state"] == "READY_PARTIAL":
            reasons.extend(latest["reason_codes"])
        elif latest["state"] != "READY_FOR_ANALYSIS":
            reasons.extend(latest["reason_codes"])
        offerings = tuple(classify_offering_event(
            getattr(item, "document_type", ""),
            getattr(item, "normalized_fact", "") or getattr(item, "summary", ""),
            getattr(item, "accession", ""), getattr(item, "filed_at", "") or getattr(item, "published_at", ""))
            for item in evidence if getattr(item, "document_type", "") in {
                "S-1", "S-3", "S-8", "424B3", "424B5", "424B7", "424B8", "8-K"}
        )
        known_offerings = [row for row in offerings if row["status"] == "KNOWN"]
        critical_unknown = {"atm_capacity", "recent_atm_usage", "warrant_outstanding",
                            "convertible_outstanding"}
        if capital_structure and critical_unknown.intersection(set(capital_structure.get("unknown_fields") or [])):
            reasons.append("MATERIAL_OFFERING_STATE_UNRESOLVED")
        return DataReadinessAssessment("READY" if not reasons else "BLOCKED_DATA",
                                       tuple(dict.fromkeys(reasons)), periodic, offerings)
