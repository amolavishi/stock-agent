from __future__ import annotations

from .schemas import EvidenceItem
from .validation import AnalysisIncompleteError


def _evidence_domains(item: EvidenceItem) -> set[str]:
    source = str(item.source_type).upper()
    domains = set()
    if source in {"SEC", "EDGAR"}:
        domains.add("SEC_FILING")
    if source in {"XBRL", "SEC_XBRL", "COMPANYFACTS"}:
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
    if explicit:
        return explicit
    text = str(claim.get("claim") or "").lower()
    if any(value in text for value in ("ma20", "ma50", "ma200", "moving average", "relative volume",
                                       "거래량", "이동평균", "stage")):
        return "MARKET_TECHNICAL"
    if any(value in text for value in ("current price", "share price", "현재가", "주가")):
        return "MARKET_PRICE"
    return "UNSPECIFIED"


def _domain_compatible(expected: str, actual: set[str]) -> bool:
    if expected in {"", "UNSPECIFIED", "LLM_INFERENCE"}:
        return True
    if expected == "FINANCIAL_FACT":
        return bool(actual & {"XBRL_FACT", "SEC_FILING"})
    return expected in actual


def validate_claim_evidence(claims: list[dict], evidence: list[EvidenceItem],
                            min_claims: int = 0,
                            additional_source_ids: set[str] | None = None) -> None:
    known = {item.evidence_id for item in evidence} | set(additional_source_ids or set())
    by_id = {item.evidence_id: item for item in evidence}
    missing: list[str] = []
    verified_material = 0
    for claim in claims:
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
