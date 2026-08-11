from __future__ import annotations

import re

from .schemas import EvidenceItem
from .validation import AnalysisIncompleteError


CLAIM_DOMAINS = frozenset({
    "CAPITAL_STRUCTURE", "FINANCIAL_FACT", "MARKET_TECHNICAL", "MARKET_PRICE",
    "SEC_FILING", "XBRL_FACT", "PORTFOLIO_STATE", "SYSTEM_STATE", "KNOWLEDGE_HISTORY",
})
CLAIM_TYPES = frozenset({
    "FACT", "NUMERIC", "EVENT", "CAPITAL", "TECHNICAL", "PRICE", "RISK",
    "COMPARATIVE", "INFERENCE", "DECISION",
})
EVIDENCE_GRADES = frozenset({"A", "B", "C", "D", "UNCLASSIFIED"})


def _evidence_domains(item: EvidenceItem) -> set[str]:
    source = str(item.source_type).upper()
    domains = set()
    if source in {"SEC", "EDGAR", "MOCK_SEC", "MOCK_IR", "MOCK_NEWS"}:
        domains.add("SEC_FILING")
    if source in {"XBRL", "XBRL_FACT", "SEC_XBRL", "COMPANYFACTS"}:
        domains.add("XBRL_FACT")
    if source in {"TOSS", "TOSS_OPEN_API", "MARKET", "MARKET_DATA"}:
        domains.update({"MARKET_PRICE", "MARKET_TECHNICAL"})
    if source in {"PORTFOLIO", "PAPER"}:
        domains.add("PORTFOLIO_STATE")
    if source in {"SYSTEM", "SQLITE"}:
        domains.add("SYSTEM_STATE")
    if source in {"OBSIDIAN", "KNOWLEDGE"}:
        domains.add("KNOWLEDGE_HISTORY")
    return domains or {"UNKNOWN"}


def _claim_domain(claim: dict) -> str:
    explicit = str(claim.get("domain") or claim.get("claim_domain") or "").upper()
    return explicit


def _domain_compatible(expected: str, actual: set[str]) -> bool:
    if not expected:
        return True
    if expected == "FINANCIAL_FACT":
        return bool(actual & {"XBRL_FACT", "SEC_FILING"})
    if expected == "CAPITAL_STRUCTURE":
        return bool(actual & {"XBRL_FACT", "SEC_FILING"})
    if expected == "MARKET_TECHNICAL":
        return bool(actual & {"MARKET_TECHNICAL", "MARKET_PRICE"})
    if expected == "MARKET_PRICE":
        return bool(actual & {"MARKET_PRICE", "MARKET_TECHNICAL"})
    return expected in actual


_SEMANTIC_STOPWORDS = {
    "about", "above", "against", "based", "company", "current", "does", "from",
    "have", "into", "that", "the", "this", "with", "will", "would", "claim",
}
_DOMAIN_TERMS = {
    "FINANCIAL_FACT": {"revenue", "gross", "margin", "cash", "debt", "income", "shares",
                       "burn", "runway", "capex", "profit", "loss", "ebitda"},
    "CAPITAL_STRUCTURE": {"atm", "warrant", "shelf", "convertible", "dilution", "offering",
                          "outstanding", "authorized", "issuance", "shares", "equity",
                          "merger", "acquisition", "transaction", "consideration"},
    "MARKET_TECHNICAL": {"ma20", "ma50", "ma200", "moving", "average", "volume", "stage",
                          "relative", "strength", "trend"},
    "MARKET_PRICE": {"price", "quote", "close", "trade", "market"},
}


def _semantic_terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z][a-z0-9_-]{2,}", value.lower())
            if term not in _SEMANTIC_STOPWORDS}


def _relevance_failure(claim: dict, item: EvidenceItem, expected_domain: str) -> bool:
    explicit_terms = {str(term).lower() for term in (claim.get("semantic_keywords") or [])}
    required = explicit_terms | _DOMAIN_TERMS.get(expected_domain, set())
    if not required:
        return False
    evidence_text = " ".join(str(value or "") for value in (
        item.title, item.summary, item.normalized_fact, item.category,
        item.semantic_classification, str(item.facts)))
    evidence_terms = _semantic_terms(evidence_text)
    return not (required & evidence_terms)


def validate_claim_schema(claim: dict) -> None:
    """Fail closed before evidence matching can be bypassed by inferred metadata."""
    if not isinstance(claim, dict):
        raise AnalysisIncompleteError("material claim must be a JSON object")
    if str(claim.get("verification_status") or "").upper() == "UNVERIFIED":
        return
    materiality = str(claim.get("materiality") or "MATERIAL").upper()
    if materiality not in {"MATERIAL", "SUPPORTING", "NON_MATERIAL"}:
        raise AnalysisIncompleteError(f"invalid claim materiality: {materiality}")
    if materiality == "NON_MATERIAL":
        return
    domain = _claim_domain(claim)
    claim_type = str(claim.get("claim_type") or "").upper()
    minimum_grade = str(claim.get("minimum_evidence_grade") or "").upper()
    missing = [name for name, value in (
        ("domain", domain), ("claim_type", claim_type),
        ("minimum_evidence_grade", minimum_grade)) if not value]
    if missing:
        raise AnalysisIncompleteError(
            "material claim contract missing required fields: " + ", ".join(missing))
    if domain not in CLAIM_DOMAINS:
        raise AnalysisIncompleteError(f"unsupported material claim domain: {domain}")
    if claim_type not in CLAIM_TYPES:
        raise AnalysisIncompleteError(f"unsupported material claim type: {claim_type}")
    if minimum_grade not in EVIDENCE_GRADES:
        raise AnalysisIncompleteError(
            f"unsupported minimum evidence grade: {minimum_grade}")


def validate_claim_evidence(claims: list[dict], evidence: list[EvidenceItem],
                            min_claims: int = 0,
                            additional_source_ids: set[str] | None = None) -> None:
    known = {item.evidence_id for item in evidence} | set(additional_source_ids or set())
    by_id = {item.evidence_id: item for item in evidence}
    missing: list[str] = []
    verified_material = 0
    for claim in claims:
        validate_claim_schema(claim)
        ids = claim.get("evidence_ids") or claim.get("source_ids")
        if ids is None and claim.get("evidence_id"):
            ids = [claim["evidence_id"]]
        if claim.get("verification_status") == "UNVERIFIED":
            continue
        if not ids:
            missing.append("<NO_EVIDENCE_ID>")
        else:
            missing.extend(str(item) for item in ids if item not in known)
            expected_domain = _claim_domain(claim)
            incompatible = [evidence_id for evidence_id in ids
                            if evidence_id in by_id and not _domain_compatible(
                                expected_domain, _evidence_domains(by_id[evidence_id]))]
            if incompatible:
                raise AnalysisIncompleteError(
                    f"claim-evidence domain mismatch: expected={expected_domain}, ids={incompatible}")
            relevant = [evidence_id for evidence_id in ids if evidence_id in by_id and
                        _relevance_failure(claim, by_id[evidence_id], expected_domain)]
            if relevant:
                raise AnalysisIncompleteError(
                    f"claim-evidence semantic relevance failed: expected={expected_domain}, ids={relevant}")
            minimum_grade = str(claim.get("minimum_evidence_grade") or "").upper()
            if minimum_grade:
                rank = {"A": 4, "B": 3, "C": 2, "D": 1, "UNCLASSIFIED": 0}
                weak = [evidence_id for evidence_id in ids if evidence_id in by_id and
                        rank.get(by_id[evidence_id].evidence_grade, 0) < rank.get(minimum_grade, 0)]
                if weak:
                    raise AnalysisIncompleteError(
                        f"claim-evidence strength insufficient: minimum={minimum_grade}, ids={weak}")
            if not any(item not in known for item in ids):
                verified_material += 1
    if missing:
        raise AnalysisIncompleteError(f"claim-evidence validation failed: {missing}")
    if verified_material < min_claims:
        raise AnalysisIncompleteError(
            f"verified material claims {verified_material} is below minimum {min_claims}")
