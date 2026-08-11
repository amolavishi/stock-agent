from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from urllib.parse import urlparse

from .schemas import EvidenceItem, EvidenceRequest, MarketSnapshot
from .validation import validate_ticker
from .edgar_documents import (DocumentCache, EdgarDocumentDownloader, EvidenceClassifier,
                              ExhibitResolver, RelevantSectionExtractor)
from .sec import EdgarMetadataCollector


class LiveEdgarEvidenceCollector:
    def __init__(self, cache_dir: str, user_agent: str):
        self.metadata = EdgarMetadataCollector(user_agent=user_agent)
        self.downloader = EdgarDocumentDownloader(DocumentCache(cache_dir), user_agent)
        self.extractor = RelevantSectionExtractor()
        self.classifier = EvidenceClassifier()
        self.exhibits = ExhibitResolver()

    def _process(self, item: EvidenceItem, keywords=None) -> list[EvidenceItem]:
        content = self.downloader.download(item)
        item.raw_document_hash = hashlib.sha256(content).hexdigest()
        item.lifecycle_status = "FETCHED"
        section = self.extractor.extract(content, keywords=keywords)
        item.summary = section[:1500] or item.summary
        item = self.classifier.classify(item, section)
        output = [item]
        exhibit_links = self.exhibits.resolve_links(item.source_url, content) if item.document_type == "8-K" else []
        for link in exhibit_links:
            suffix = hashlib.sha256(link.encode()).hexdigest()[:12]
            exhibit = EvidenceItem(
                evidence_id=f"{item.evidence_id}_EX99_{suffix}", ticker=item.ticker,
                source_type="SEC", document_type="EX-99", published_at=item.published_at,
                title=f"{item.ticker} Exhibit 99", source_url=link,
                evidence_grade="UNCLASSIFIED", category="EXHIBIT",
                summary="SEC Exhibit 99 pending processing", source_reliability="PRIMARY",
                data_quality="PARTIAL", is_mock=False, accession=item.accession,
                filed_at=item.filed_at, parent_evidence_id=item.evidence_id,
                extraction_method="8K_EXHIBIT_RESOLVER",
            )
            exhibit_content = self.downloader.download(exhibit)
            exhibit.raw_document_hash = hashlib.sha256(exhibit_content).hexdigest()
            exhibit.lifecycle_status = "FETCHED"
            exhibit_section = self.extractor.extract(
                exhibit_content,
                keywords=keywords or ("revenue", "earnings", "guidance", "margin", "cash", "agreement"),
            )
            exhibit.summary = exhibit_section[:1500] or exhibit.summary
            output.append(self.classifier.classify(exhibit, exhibit_section))
        item.exhibits_resolved = (item.document_type != "8-K" or
                                  not exhibit_links or all(value.lifecycle_status == "READY_FOR_ANALYSIS"
                                                           for value in output[1:]))
        if exhibit_links and not item.exhibits_resolved:
            item.lifecycle_status = "FAILED"
            item.ready_for_analysis_at = ""
        return output

    def collect(self, ticker: str) -> list[EvidenceItem]:
        items = self.metadata.collect(ticker)
        output: list[EvidenceItem] = []
        for item in items:
            output.extend(self._process(item))
        return output

    def collect_for_request(self, ticker: str, request: EvidenceRequest) -> list[EvidenceItem]:
        forms = {value.upper() for value in request.target_forms} or None
        items = self.metadata.collect(ticker, limit=12, target_forms=forms,
                                      date_from=request.date_from, date_to=request.date_to)
        output: list[EvidenceItem] = []
        for item in items:
            item.query_request_id = request.request_id
            processed = self._process(item, request.keywords or None)
            for value in processed:
                value.query_request_id = request.request_id
            output.extend(processed)
        return output


def market_snapshot_evidence(snapshot: MarketSnapshot) -> EvidenceItem:
    """Represent the authoritative market snapshot as claim-addressable evidence."""
    observed_at = snapshot.observed_at or snapshot.timestamp
    digest = hashlib.sha256(snapshot.snapshot_id.encode("utf-8")).hexdigest()[:12]
    evidence_id = f"MARKET_{snapshot.ticker}_{digest}"
    summary = (
        f"{snapshot.ticker} market snapshot price={snapshot.current} "
        f"change_1d_pct={snapshot.change_1d_pct} return_5d_pct={snapshot.return_5d_pct} "
        f"return_20d_pct={snapshot.return_20d_pct} volume={snapshot.volume} "
        f"avg_20d_volume={snapshot.avg_20d_volume} ma20={snapshot.ma20} "
        f"ma50={snapshot.ma50} atr_14={snapshot.atr_14} stage={snapshot.stage}"
    )
    if (snapshot.data_quality in {"OK", "COMPLETE"} and
            snapshot.indicator_readiness == "READY" and
            snapshot.volume_validity != "INVALID"):
        evidence_grade = "B"
    elif (snapshot.current > 0 and snapshot.quote_freshness == "FRESH" and
          snapshot.candle_freshness == "FRESH"):
        evidence_grade = "C"
    else:
        evidence_grade = "UNCLASSIFIED"
    return EvidenceItem(
        evidence_id=evidence_id, ticker=snapshot.ticker, source_type="MARKET_DATA",
        document_type="MARKET_SNAPSHOT", published_at=observed_at[:10],
        title=f"{snapshot.ticker} market snapshot", source_url=f"market://{evidence_id}",
        evidence_grade=evidence_grade,
        category="MARKET_SNAPSHOT", summary=summary,
        facts={
            "price": snapshot.current, "change_1d_pct": snapshot.change_1d_pct,
            "return_5d_pct": snapshot.return_5d_pct, "return_20d_pct": snapshot.return_20d_pct,
            "volume": snapshot.volume, "avg_20d_volume": snapshot.avg_20d_volume,
            "ma20": snapshot.ma20, "ma50": snapshot.ma50, "atr_14": snapshot.atr_14,
            "stage": snapshot.stage, "snapshot_id": snapshot.snapshot_id,
        },
        source_reliability="SIMULATED" if snapshot.is_mock else "PRIMARY",
        data_quality=snapshot.data_quality, is_mock=snapshot.is_mock,
        filed_at=observed_at, normalized_fact=summary,
        grade_reason="Authoritative market snapshot for price and technical claims",
        freshness="FRESH", extraction_method="MARKET_PROVIDER_SNAPSHOT",
        lifecycle_status="READY_FOR_ANALYSIS", semantic_classification="MARKET_DATA",
        validated_at=observed_at, ready_for_analysis_at=observed_at,
        source_span=summary,
    )


def company_facts_evidence(ticker: str, facts: dict) -> EvidenceItem:
    """Expose selected SEC CompanyFacts values as one provenance-addressable item."""
    metric_names = (
        "revenue", "gross_profit", "operating_income", "net_income",
        "operating_cash_flow", "capex", "cash", "debt",
        "shares_outstanding", "stock_based_compensation",
    )
    selected: dict[str, dict] = {}
    source_fact_ids: list[str] = []
    source_accessions: list[str] = []
    for name in metric_names:
        value = facts.get(name)
        if not isinstance(value, dict) or value.get("value") is None:
            continue
        selected[name] = value
        source_fact_ids.extend(str(item) for item in (
            value.get("source_fact_ids") or [value.get("fact_id") or value.get("source_id")]
        ) if item)
        source_accessions.extend(str(item) for item in (
            value.get("provenance", {}).get("source_accessions") or
            ([value.get("accn")] if value.get("accn") else [])
        ) if item)
    derived = facts.get("derived") or {}
    derived_values = {
        name: value for name, value in derived.items()
        if value is not None and name in {"gross_margin_pct", "net_cash", "cash_burn",
                                          "estimated_runway_months"}
    }
    normalized_rows = facts.get("normalized_facts") or []
    for row in normalized_rows:
        if row.get("fact_id"):
            source_fact_ids.append(str(row["fact_id"]))
        if row.get("accn"):
            source_accessions.append(str(row["accn"]))
    source_fact_ids = list(dict.fromkeys(source_fact_ids))
    source_accessions = list(dict.fromkeys(source_accessions))
    identity = "|".join([ticker, *source_fact_ids]) or ticker
    evidence_id = f"XBRL_FACT_{ticker}_{hashlib.sha256(identity.encode()).hexdigest()[:12]}"
    filed_at = max((str(row.get("filed") or "") for row in normalized_rows), default="")
    filed_at = filed_at or datetime.now(timezone.utc).date().isoformat()
    parts = []
    for name, value in selected.items():
        parts.append(f"{name}={value.get('value')} {value.get('unit', '')}".strip())
    parts.extend(f"{name}={value}" for name, value in derived_values.items())
    summary = f"{ticker} SEC CompanyFacts XBRL facts: " + "; ".join(parts)
    return EvidenceItem(
        evidence_id=evidence_id, ticker=ticker, source_type="XBRL_FACT",
        document_type="COMPANYFACTS", published_at=filed_at,
        title=f"{ticker} SEC CompanyFacts", source_url=(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(facts.get('cik', '')).zfill(10)}.json"
        ),
        evidence_grade="B" if selected else "UNCLASSIFIED",
        category="XBRL_FACTS", summary=summary,
        facts={"metrics": selected, "derived": derived_values,
               "source_fact_ids": source_fact_ids, "source_accessions": source_accessions},
        source_reliability="PRIMARY", data_quality="OK" if selected else "PARTIAL",
        is_mock=False, filed_at=filed_at, normalized_fact=summary,
        grade_reason="SEC CompanyFacts values with source fact provenance",
        freshness="FRESH", extraction_method="SEC_COMPANYFACTS_NORMALIZED",
        lifecycle_status="READY_FOR_ANALYSIS",
        semantic_classification="XBRL_FACTS", validated_at=filed_at,
        ready_for_analysis_at=filed_at, source_span=summary,
    )

EVIDENCE_STRATEGIES = {
    "DILUTION": {
        "forms": ["S-1", "S-3", "S-8", "424B3", "424B5", "424B7", "424B8", "8-K", "10-Q", "10-K", "144"],
        "keywords": ["at-the-market", "sales agreement", "equity distribution", "warrant",
                     "convertible", "preferred stock", "selling stockholder", "remaining capacity"],
        "facts": ["shares_outstanding", "stock_based_compensation", "cash"],
    },
    "CONTRACT": {
        "forms": ["8-K", "10-Q", "10-K"],
        "keywords": ["definitive agreement", "purchase order", "minimum purchase", "funded",
                     "award", "termination", "customer", "backlog", "remaining performance obligation"],
        "facts": ["revenue"],
    },
    "MNA": {
        "forms": ["8-K", "S-4", "10-Q", "10-K"],
        "keywords": ["merger", "acquisition", "business combination", "transaction closed",
                     "definitive agreement", "termination fee"],
        "facts": ["cash", "debt", "shares_outstanding"],
    },
    "INSIDER": {
        "forms": ["4", "144", "13D", "13G"],
        "keywords": ["beneficial ownership", "sale", "purchase", "disposing", "acquiring"],
        "facts": ["shares_outstanding"],
    },
}


def normalize_evidence_request(payload: dict | str, round_no: int = 1) -> EvidenceRequest:
    if isinstance(payload, str):
        payload = {"question": payload}
    if not isinstance(payload, dict):
        raise TypeError("evidence request must be an object or question string")
    question = str(payload.get("question") or payload.get("request") or payload.get("issue") or "").strip()
    lowered = question.lower()
    if any(word in lowered for word in ("atm", "shelf", "희석", "warrant", "convertible", "발행")):
        strategy = EVIDENCE_STRATEGIES["DILUTION"]
    elif any(word in lowered for word in ("contract", "agreement", "계약", "award", "backlog")):
        strategy = EVIDENCE_STRATEGIES["CONTRACT"]
    elif any(word in lowered for word in ("m&a", "merger", "acquisition", "인수", "합병")):
        strategy = EVIDENCE_STRATEGIES["MNA"]
    elif any(word in lowered for word in ("insider", "ownership", "내부자", "대주주")):
        strategy = EVIDENCE_STRATEGIES["INSIDER"]
    else:
        strategy = {"forms": ["8-K", "10-Q", "10-K"],
                    "keywords": [word for word in lowered.split() if len(word) >= 4][:8], "facts": []}
    return EvidenceRequest(
        request_id=str(payload.get("request_id") or
                       f"ER_{round_no}_{hashlib.sha256(question.encode()).hexdigest()[:12]}"),
        issue_id=str(payload.get("issue_id") or ""), question=question or "Additional SEC verification",
        severity=str(payload.get("severity") or "HIGH"),
        source_scope=list(payload.get("source_scope") or ["SEC"]),
        target_forms=list(payload.get("target_forms") or strategy["forms"]),
        keywords=list(payload.get("keywords") or strategy["keywords"]),
        date_from=str(payload.get("date_from") or ""), date_to=str(payload.get("date_to") or ""),
        company_fact_targets=list(payload.get("company_fact_targets") or strategy["facts"]),
        must_answer=bool(payload.get("must_answer", False)),
        requesting_role=str(payload.get("requesting_role") or "CRITIC"), requested_round=round_no)


def detect_evidence_conflicts(evidence: list[EvidenceItem]) -> list[dict]:
    by_fact: dict[str, list[tuple[str, object]]] = {}
    for item in evidence:
        for name, value in item.facts.items():
            if name in {"section_hash", "accession_number", "primary_document", "cik"}:
                continue
            by_fact.setdefault(name, []).append((item.evidence_id, value))
    conflicts = []
    for topic, rows in by_fact.items():
        fingerprints = {json_fingerprint(value) for _, value in rows}
        if len(rows) > 1 and len(fingerprints) > 1:
            conflicts.append({
                "topic": topic, "evidence_ids": [evidence_id for evidence_id, _ in rows],
                "description": f"Conflicting values were observed for {topic}",
                "severity": "HIGH", "status": "OPEN",
            })
    return conflicts


def json_fingerprint(value: object) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class MockEvidenceCollector:
    def collect(self, ticker: str) -> list[EvidenceItem]:
        ticker = validate_ticker(ticker)
        today = datetime.now(timezone.utc).date().isoformat()
        return [
            EvidenceItem(f"MOCK_SEC_{ticker}_001", ticker, "MOCK_SEC", "8-K", today,
                f"[MOCK] {ticker} Form 8-K", "mock://sec/filing/001", "UNCLASSIFIED",
                "FILING", "[MOCK] 사업 업데이트 시나리오", {"binding": True},
                source_reliability="SIMULATED", is_mock=True),
            EvidenceItem(f"MOCK_IR_{ticker}_001", ticker, "MOCK_IR", "EARNINGS", today,
                f"[MOCK] {ticker} IR update", "mock://ir/update/001", "UNCLASSIFIED",
                "FUNDAMENTAL", "[MOCK] 성장 및 사업 진행 시나리오", {"company_confirmed": True},
                source_reliability="SIMULATED", is_mock=True),
            EvidenceItem(f"MOCK_NEWS_{ticker}_001", ticker, "MOCK_NEWS", "ARTICLE", today,
                f"[MOCK] {ticker} sector scenario", "mock://news/scenario/001", "UNCLASSIFIED",
                "SENTIMENT", "[MOCK] 섹터 관심도 시나리오", {"sentiment": "positive"},
                source_reliability="SIMULATED", is_mock=True),
        ]
