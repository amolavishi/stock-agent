from __future__ import annotations

import hashlib
import html
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin
from pathlib import Path

from .schemas import EvidenceItem


class ExhibitResolver:
    MATERIAL_ITEMS = ("item 2.02", "item 7.01", "item 8.01")

    def resolve_links(self, primary_url: str, content: bytes) -> list[str]:
        text = content.decode("utf-8", errors="ignore")
        lowered = html.unescape(re.sub(r"<[^>]+>", " ", text)).lower()
        if not any(item in lowered for item in self.MATERIAL_ITEMS):
            return []
        links = re.findall(r"href\s*=\s*['\"]([^'\"]+)['\"]", text, flags=re.I)
        result = []
        for link in links:
            clean = html.unescape(link).strip()
            label = clean.lower()
            if not re.search(r"(?:ex(?:hibit)?[-_ ]?)?99(?:[-_. ]?\d+)?", label):
                continue
            resolved = urljoin(primary_url, clean)
            if resolved not in result:
                result.append(resolved)
        return result


class DocumentCache:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, evidence_id: str) -> Path:
        safe_id = re.sub(r"[^A-Z0-9_.-]", "_", evidence_id.upper())
        path = (self.root / f"{safe_id}.html").resolve()
        if self.root not in path.parents:
            raise ValueError("cache path escape")
        return path

    def get(self, evidence_id: str) -> bytes | None:
        path = self._path(evidence_id)
        return path.read_bytes() if path.exists() else None

    def put(self, evidence_id: str, content: bytes) -> Path:
        path = self._path(evidence_id)
        path.write_bytes(content)
        return path


class EdgarDocumentDownloader:
    def __init__(self, cache: DocumentCache, user_agent: str, timeout: float = 20.0, max_attempts: int = 3):
        self.cache, self.user_agent, self.timeout = cache, user_agent, timeout
        self.max_attempts = max(1, max_attempts)

    def download(self, item: EvidenceItem) -> bytes:
        cached = self.cache.get(item.evidence_id)
        if cached is not None:
            return cached
        request = urllib.request.Request(item.source_url, headers={"User-Agent": self.user_agent})
        content: bytes | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    content = response.read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.max_attempts:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == self.max_attempts:
                    raise
            time.sleep(0.5 * (2 ** (attempt - 1)))
        if content is None:
            raise RuntimeError("SEC document download failed")
        self.cache.put(item.evidence_id, content)
        return content


class RelevantSectionExtractor:
    KEYWORDS = ("agreement", "contract", "offering", "warrant", "acquisition", "guidance", "customer", "funded")

    def extract(self, content: bytes, max_chars: int = 20_000,
                keywords: tuple[str, ...] | list[str] | None = None) -> str:
        text = content.decode("utf-8", errors="ignore")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.I | re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        text = re.sub(r"\s+", " ", text).strip()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        selected_keywords = tuple(str(value).lower() for value in (keywords or self.KEYWORDS))
        relevant = [s for s in sentences if any(k in s.lower() for k in selected_keywords)]
        return "\n".join(relevant)[:max_chars]


class EvidenceClassifier:
    """Conservative placeholder classifier. Unclear documents remain UNCLASSIFIED."""

    def classify(self, item: EvidenceItem, section_text: str) -> EvidenceItem:
        lowered = section_text.lower()
        grade = "UNCLASSIFIED"
        reason = "Economic event strength could not be confirmed from the extracted text"
        if "completed the acquisition" in lowered or "transaction closed" in lowered:
            grade = "A"
            reason = "Executed or completed transaction language was found"
        elif "definitive agreement" in lowered or "entered into an agreement" in lowered:
            grade = "B"
            reason = "Binding or definitive agreement language was found"
        elif any(value in lowered for value in ("memorandum of understanding", "letter of intent",
                                                 "non-binding")):
            grade = "D"
            reason = "Non-binding MOU/LOI language was found"
        elif (item.document_type == "EX-99"
              and "terms of the agreement" in lowered
              and any(marker in lowered for marker in (
                  "at close of the transaction", "shareholders are receiving",
                  "merger consideration"))):
            grade = "B"
            reason = "Issuer-filed Exhibit 99 contains substantive transaction terms"
        elif (item.document_type in {"S-1", "S-3", "S-8", "424B3", "424B5", "424B7", "424B8"}
              and any(term in lowered for term in ("registration statement", "prospectus",
                                                    "offering", "securities"))):
            grade = "C"
            reason = "Filing indicates prepared or available issuance capacity, not completion"
        elif item.document_type == "EX-99" and any(
                value in lowered for value in ("results of operations", "earnings release",
                                                "revenue", "financial results", "guidance")):
            grade = "B"
            reason = "Issuer-filed Exhibit 99 contains substantive financial disclosure"
        item.evidence_grade = grade
        item.grade_reason = reason
        item.category = "CLASSIFIED_FILING" if grade != "UNCLASSIFIED" else "FILING_REVIEW_REQUIRED"
        digest = hashlib.sha256(section_text.encode("utf-8")).hexdigest()
        item.facts["section_hash"] = digest
        item.content_hash = digest
        item.normalized_fact = section_text[:1500]
        item.extraction_method = item.extraction_method or "KEYWORD_SENTENCE_EXTRACTION"
        item.data_quality = "OK" if section_text else "PARTIAL"
        item.freshness = "FRESH" if item.data_quality == "OK" else "UNKNOWN"
        item.parsed_at = item.parsed_at or __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat()
        item.lifecycle_status = "SEMANTICALLY_CLASSIFIED" if section_text else "FAILED"
        item.semantic_classification = item.category
        item.source_span = section_text[:1500]
        if section_text and grade in {"A", "B", "C"}:
            item.validated_at = item.parsed_at
            item.ready_for_analysis_at = item.parsed_at
            item.lifecycle_status = "READY_FOR_ANALYSIS"
        return item
