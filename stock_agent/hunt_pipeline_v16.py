"""V8 HUNT V1.6: evidence-first research and post-research catalyst certification.

This layer addresses the RUN-009/RUN-010 structural failure without weakening
any final investment gate. The legacy strict runtime used CatalystGate as a
research-admission veto. When the first research artifact did not already
contain a fully structured 1-8 week catalyst, the candidate never reached
Deep Research or Audit.

V1.6 changes *research sequencing*, not certification:

* initial CatalystGate insufficiency becomes explicit EVIDENCE_DEBT and is
  allowed to enter research;
* research acquisition attempts issuer IR, secondary media, safe full-article
  text, SEC 8-K/10-Q/10-K, and optional configured government/customer/
  industry sources;
* a broader refresh/retry occurs before SOURCE_EXHAUSTED;
* the original strict CatalystGate is rerun after Deep Research + Full SEC;
* the latest post-research strict receipt is the only receipt that can satisfy
  SQLite QualifiedCandidatePool eligibility;
* malformed individual media links are skipped instead of killing the lane;
* per-stage entered/pass/fail/not-evaluated/refreshed telemetry is persisted;
* a research queue that cannot reach Deep Research (or Full SEC cannot reach
  Audit) is PIPELINE_STARVATION, not a clean NO_TRADE conclusion.

Discovery Priority != Research Grade != PRE-A Readiness != Execution Action.
Nothing in this module writes an A/A- grade, grants STARTER, changes position
size, or permits a broker write.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from . import adapters as adapters_module
from . import runtime as runtime_module
from . import shadow as shadow_module
from .adapters import IssuerIRWebEvidenceProvider, ProviderError
from .catalyst import CatalystGate, extract_catalyst_packet
from .catalyst_acquisition_v15 import extract_grounded_catalysts
from .models import Evidence, GateDecision, RawArtifact, RunOutcome, canonical_hash, utc_now


HUNT_PIPELINE_VERSION = "V8_HUNT_PIPELINE_V1.6"
DEFAULT_REFRESH_NEWS_LIMIT = 100
MAX_PACKET_SOURCES = 80
MAX_SOURCE_CONTENT = 180_000

_ALLOWED_AUX_SOURCE_CLASSES = {
    "COMPANY_IR", "GOVERNMENT", "REGULATOR", "CUSTOMER", "INDUSTRY",
}

_LANE_TERMS: dict[str, tuple[str, ...]] = {
    "02": ("contract", "guidance", "earnings", "buyback", "refinancing", "customer", "approval"),
    "03": ("ipo", "listing", "lockup", "guidance", "earnings", "profitability"),
    "04": ("turnaround", "margin", "profitability", "earnings", "guidance", "cash flow"),
    "05": ("defense", "nuclear", "uranium", "critical mineral", "policy", "award", "contract"),
    "06": ("space", "defense", "ISR", "aerospace", "award", "contract", "backlog"),
    "07": ("profitability", "margin", "free cash flow", "earnings", "guidance"),
    "08": ("secondary", "block trade", "offering", "placement", "overhang", "ATM"),
    "09": ("insider", "repurchase", "buyback", "share purchase", "capital return"),
    "10": ("refinancing", "debt", "maturity", "credit agreement", "liquidity", "bankruptcy"),
    "11": ("earnings", "guidance", "estimate", "revision", "revenue", "EPS", "margin"),
    "12": ("customer", "supplier", "award", "contract", "concentration", "multi-year"),
    "13": ("fintech", "healthcare", "software", "guidance", "earnings", "customer"),
    "14": ("AI", "capacity", "data center", "GPU", "power", "MW", "backlog", "contract"),
    "GENERIC": ("contract", "guidance", "earnings", "customer", "buyback", "refinancing", "approval", "capacity"),
}


def evidence_plan_for_lane(lane: str | None) -> dict[str, Any]:
    code = str(lane or "GENERIC").upper().strip()
    if code not in _LANE_TERMS:
        code = "GENERIC"
    required = ["ISSUER_IR", "SECONDARY_MEDIA", "SEC_8K", "SEC_PERIODIC"]
    if code in {"05"}:
        required.append("GOVERNMENT_OR_REGULATOR")
    if code in {"06", "12", "14"}:
        required.append("CUSTOMER_OR_INDUSTRY")
    if code in {"10"}:
        required.append("SEC_CREDIT_OR_FINANCING")
    if code in {"11", "04", "07", "13"}:
        required.append("EARNINGS_OR_GUIDANCE")
    return {
        "version": HUNT_PIPELINE_VERSION,
        "lane": code,
        "query_terms": list(_LANE_TERMS[code]),
        "requested_source_lanes": required,
        "grade_authority": False,
        "execution_authority": False,
    }


def infer_v8_lane(candidate: Mapping[str, Any] | None) -> str:
    value = dict(candidate or {})
    for key in ("v8_lane", "discovery_lane", "lane"):
        raw = str(value.get(key) or "").strip().upper()
        if raw in _LANE_TERMS:
            return raw
    text = " ".join(str(value.get(key) or "") for key in ("rationale", "proposed_stage", "thesis", "signal")).casefold()
    patterns = (
        ("12", ("customer", "concentration")),
        ("10", ("refinanc", "debt", "bankrupt", "maturity")),
        ("09", ("buyback", "repurchase", "insider")),
        ("08", ("secondary", "block", "offering", "atm")),
        ("14", ("ai", "gpu", "data center", "capacity", "power")),
        ("06", ("space", "aerospace", "defense", "isr")),
        ("05", ("uranium", "nuclear", "critical mineral", "policy")),
        ("11", ("revision", "post-earnings", "estimate")),
        ("04", ("turnaround",)),
        ("07", ("profitability", "margin")),
        ("13", ("fintech", "healthcare", "software")),
        ("03", ("ipo",)),
    )
    for lane, terms in patterns:
        if any(term in text for term in terms):
            return lane
    return "02"


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _latest_time(values: list[Any]) -> str | None:
    parsed = [(str(value), _parse_time(value)) for value in values if value not in (None, "")]
    valid = [(raw, dt) for raw, dt in parsed if dt is not None]
    if not valid:
        return None
    return max(valid, key=lambda pair: pair[1])[1].isoformat().replace("+00:00", "Z")


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in sources:
        if not isinstance(raw, dict):
            continue
        source = dict(raw)
        content = str(source.get("content") or "")[:MAX_SOURCE_CONTENT]
        source["content"] = content
        key = canonical_hash({
            "url": source.get("source_url"),
            "time": source.get("source_observed_at"),
            "title": source.get("title"),
            "content": content,
        })
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
        if len(result) >= MAX_PACKET_SOURCES:
            break
    return result


def _artifact_sources(artifact: RawArtifact) -> list[dict[str, Any]]:
    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    sources: list[dict[str, Any]] = []
    for item in payload.get("evidence_items") or []:
        if isinstance(item, dict) and item.get("source_url") and item.get("content") not in (None, "", [], {}):
            sources.append(dict(item))
    if payload.get("source_url") and payload.get("content") not in (None, "", [], {}):
        sources.append({
            "security_id": payload.get("security_id") or artifact.subject_id,
            "source_class": payload.get("source_class") or artifact.provider,
            "source_url": payload.get("source_url"),
            "source_observed_at": payload.get("source_observed_at") or artifact.source_observed_at,
            "title": payload.get("title"),
            "content": payload.get("content"),
            "origin_artifact_id": artifact.artifact_id,
        })
    return _dedupe_sources(sources)


def _source_rank(source: dict[str, Any]) -> tuple[int, int, float]:
    catalyst = int(bool(extract_grounded_catalysts(source)))
    source_class = str(source.get("source_class") or "").upper()
    authority = 4 if source_class in {"SEC", "SEC_EDGAR", "GOVERNMENT", "REGULATOR"} else 3 if source_class == "COMPANY_IR" else 2 if source_class in {"CUSTOMER", "INDUSTRY"} else 1
    parsed = _parse_time(source.get("source_observed_at"))
    return catalyst, authority, parsed.timestamp() if parsed else 0.0


class V16YahooEvidenceProvider:
    """Catalyst-aware Yahoo RSS with item-local fault isolation and full text.

    The v1.5 provider aborted the entire lane when one RSS link failed strict
    URL validation. V1.6 skips that item, records the error, and keeps scanning.
    Valid finance.yahoo.com article pages are fetched for visible full text;
    failures fall back to the source-identifiable RSS title/description.
    """

    provider_name = "yahoo-finance-news-v16"
    BASE_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"

    def __init__(self, *, timeout: float = 20.0, max_bytes: int = 1_500_000, user_agent: str = "StockAgent/1.6 research") -> None:
        self.timeout = max(1.0, float(timeout))
        self.max_bytes = max(100_000, int(max_bytes))
        self.user_agent = str(user_agent)
        IssuerIRWebEvidenceProvider._validate_url(self.BASE_URL)

    @staticmethod
    def _parse_date(value: str) -> datetime | None:
        import email.utils
        text = str(value or "").strip()
        if not text:
            return None
        for parser in (
            lambda: email.utils.parsedate_to_datetime(text),
            lambda: datetime.fromisoformat(text.replace("Z", "+00:00")),
        ):
            try:
                parsed = parser()
            except (TypeError, ValueError, OverflowError):
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).replace(microsecond=0)
        return None

    def _fetch_article_text(self, url: str) -> tuple[str | None, str | None]:
        request = urllib.request.Request(url, headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    return None, "article_response_too_large"
                final_url = adapters_module._response_final_url(response, url)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return None, f"article_fetch:{type(exc).__name__}"
        try:
            IssuerIRWebEvidenceProvider._validate_url(str(final_url))
            if not IssuerIRWebEvidenceProvider._host_allowed(str(final_url), ["finance.yahoo.com"]):
                return None, "article_redirect_outside_yahoo"
            parser = adapters_module._IssuerIRHTMLParser()
            parser.feed(raw.decode("utf-8", errors="replace"))
            parser.close()
            _, body = IssuerIRWebEvidenceProvider._title_and_body(parser)
            body = re.sub(r"\s+", " ", body).strip()
            return (body[:MAX_SOURCE_CONTENT] if body else None), None
        except Exception as exc:
            return None, f"article_parse:{type(exc).__name__}"

    def fetch(self, subject_id: str, query: dict[str, Any] | None = None) -> RawArtifact:
        sid = str(subject_id).upper().strip()
        if not sid or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sid):
            raise ProviderError("Yahoo news ticker is malformed")
        query = dict(query or {})
        try:
            limit = int(query.get("catalyst_news_scan_limit") or 30)
        except (TypeError, ValueError) as exc:
            raise ProviderError("catalyst news scan limit must be an integer") from exc
        limit = max(1, min(100, limit))
        terms = [str(item).casefold() for item in (query.get("v8_lane_terms") or []) if str(item).strip()]
        params = urllib.parse.urlencode({"s": sid, "region": "US", "lang": "en-US"})
        feed_url = f"{self.BASE_URL}?{params}"
        request = urllib.request.Request(feed_url, headers={"Accept": "application/rss+xml,application/xml", "User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise ProviderError("Yahoo news response exceeds configured size limit")
                final_url = adapters_module._response_final_url(response, feed_url)
        except ProviderError:
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"Yahoo news request failed for {sid}: {exc}") from exc
        try:
            IssuerIRWebEvidenceProvider._validate_url(str(final_url))
        except ProviderError as exc:
            raise ProviderError("Yahoo RSS endpoint returned an invalid redirect") from exc
        if not IssuerIRWebEvidenceProvider._host_allowed(str(final_url), ["feeds.finance.yahoo.com"]):
            raise ProviderError("Yahoo news redirect crossed configured host boundary")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ProviderError("Yahoo news RSS payload is malformed") from exc

        candidates: list[dict[str, Any]] = []
        item_errors: list[dict[str, str]] = []
        now = datetime.now(timezone.utc)
        for item in root.findall(".//item"):
            title = str(item.findtext("title") or "").strip()
            link = str(item.findtext("link") or "").strip()
            description = str(item.findtext("description") or "").strip()
            published = self._parse_date(str(item.findtext("pubDate") or item.findtext("published") or ""))
            if not title or not link or published is None:
                continue
            if published > now + adapters_module.timedelta(minutes=5):
                item_errors.append({"url": link[:240], "error": "future_publication_timestamp"})
                continue
            try:
                IssuerIRWebEvidenceProvider._validate_url(link)
            except ProviderError as exc:
                item_errors.append({"url": link[:240], "error": f"invalid_item_url:{str(exc)[:120]}"})
                continue
            if not IssuerIRWebEvidenceProvider._host_allowed(link, ["finance.yahoo.com"]):
                item_errors.append({"url": link[:240], "error": "non_yahoo_item_url"})
                continue
            identity_text = f"{title} {description}".casefold()
            if sid.casefold() not in identity_text:
                continue
            score = sum(1 for term in terms if term in identity_text)
            candidates.append({"title": title, "link": link, "description": description, "published": published, "lane_score": score})

        if not candidates:
            raise ProviderError(f"Yahoo news feed has no issuer-identifiable article for {sid}")
        candidates.sort(key=lambda item: (int(item["lane_score"]), item["published"].timestamp()), reverse=True)
        selected = candidates[:limit]
        items: list[dict[str, Any]] = []
        article_fetch_success = 0
        for item in selected:
            rss_content = re.sub(r"\s+", " ", f"{item['title']}. {item['description']}").strip()
            full_text, fetch_error = self._fetch_article_text(str(item["link"]))
            if full_text:
                article_fetch_success += 1
            source = {
                "security_id": sid,
                "source_class": "MAJOR_MEDIA",
                "source_url": item["link"],
                "source_observed_at": item["published"].isoformat().replace("+00:00", "Z"),
                "title": str(item["title"])[:1000],
                "content": (full_text or rss_content)[:MAX_SOURCE_CONTENT],
                "content_depth": "FULL_ARTICLE" if full_text else "RSS_SNIPPET",
                "lane_score": item["lane_score"],
            }
            if fetch_error:
                source["article_fetch_error"] = fetch_error
            source["catalysts"] = extract_grounded_catalysts(source)
            items.append(source)

        catalyst_items = [item for item in items if item.get("catalysts")]
        chosen = max(catalyst_items or items, key=_source_rank)
        catalysts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            for catalyst in item.get("catalysts") or []:
                key = canonical_hash(catalyst)
                if key in seen:
                    continue
                seen.add(key)
                catalysts.append(dict(catalyst))
        content_hash = canonical_hash({"source_url": chosen["source_url"], "content": chosen["content"]})
        fetched_at = utc_now()
        payload = {
            "security_id": sid,
            "evidence_type": "NEWS_CATALYST_FULLTEXT_SCAN",
            "source_class": "MAJOR_MEDIA",
            "source_url": chosen["source_url"],
            "source_observed_at": chosen["source_observed_at"],
            "provider": self.provider_name,
            "title": chosen["title"],
            "content": chosen["content"],
            "content_hash": content_hash,
            "feed_url": feed_url,
            "evidence_items": [{key: value for key, value in item.items() if key != "catalysts"} for item in items],
            "catalysts": catalysts,
            "evidence_acquisition": {
                "version": HUNT_PIPELINE_VERSION,
                "strategy": "RSS_RANK_THEN_SAFE_FULL_ARTICLE",
                "scan_limit": limit,
                "issuer_identifiable_items_scanned": len(selected),
                "full_article_fetch_success": article_fetch_success,
                "item_errors": item_errors[:100],
                "grounded_catalyst_count": len(catalysts),
                "cost_cap_applied": False,
            },
            "fetched_at": fetched_at,
        }
        payload["raw_artifact_id"] = f"artifact-yahoo-v16-{canonical_hash(payload)[:32]}"
        return RawArtifact(
            payload["raw_artifact_id"], self.provider_name, "RESEARCH_EVIDENCE", sid,
            chosen["source_observed_at"], payload, canonical_hash(payload),
            chosen["source_observed_at"], fetched_at,
        )


def _fetch_auxiliary_sources(subject_id: str, query: dict[str, Any], *, timeout: float, max_bytes: int, user_agent: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    sources: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for raw in query.get("additional_sources") or []:
        if not isinstance(raw, dict):
            continue
        source_class = str(raw.get("source_class") or "").upper()
        url = str(raw.get("source_url") or "").strip()
        hosts = [str(item).casefold().rstrip(".") for item in (raw.get("allowed_hosts") or []) if str(item).strip()]
        markers = [str(item).casefold() for item in (raw.get("issuer_markers") or [subject_id]) if str(item).strip()]
        if source_class not in _ALLOWED_AUX_SOURCE_CLASSES or not url or not hosts:
            failures.append({"lane": source_class or "AUX", "error": "aux_source_contract_incomplete"})
            continue
        try:
            IssuerIRWebEvidenceProvider._validate_url(url)
            if not IssuerIRWebEvidenceProvider._host_allowed(url, hosts):
                raise ProviderError("aux source URL outside allowlist")
            request = urllib.request.Request(url, headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": user_agent})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ProviderError("aux source response exceeds size limit")
                final_url = adapters_module._response_final_url(response, url)
            IssuerIRWebEvidenceProvider._validate_url(str(final_url))
            if not IssuerIRWebEvidenceProvider._host_allowed(str(final_url), hosts):
                raise ProviderError("aux source redirect outside allowlist")
            parser = adapters_module._IssuerIRHTMLParser()
            parser.feed(body.decode("utf-8", errors="replace")); parser.close()
            title, content = IssuerIRWebEvidenceProvider._title_and_body(parser)
            identity_text = f"{title} {content}".casefold()
            if markers and not any(marker in identity_text for marker in markers):
                raise ProviderError("aux source issuer identity marker missing")
            explicit_time = raw.get("source_observed_at")
            observed = _parse_time(explicit_time) if explicit_time else IssuerIRWebEvidenceProvider._parse_source_time(parser)
            if observed is None:
                raise ProviderError("aux source timestamp unavailable")
            sources.append({
                "security_id": subject_id,
                "source_class": source_class,
                "source_url": url,
                "source_observed_at": observed.isoformat().replace("+00:00", "Z"),
                "title": title or str(raw.get("title") or subject_id),
                "content": content[:MAX_SOURCE_CONTENT],
            })
        except (ProviderError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            failures.append({"lane": source_class or "AUX", "error": str(exc)[:240]})
    return sources, failures


class V16MultiSourceResearchProvider:
    """Research packet provider with refresh/retry and SEC fallback.

    This wrapper is constructed inside the production agent so it can use the
    already-configured SEC provider without changing CLI secrets or adding a
    new external dependency. It never manufactures a PASS; it only returns raw
    source material and deterministic acquisition metadata.
    """

    provider_name = "v16-multisource-research"

    def __init__(self, delegate: Any, sec_provider: Any, lane_resolver: Callable[[str], str] | None = None) -> None:
        self.delegate = delegate
        self.sec_provider = sec_provider
        self.lane_resolver = lane_resolver or (lambda _sid: "02")
        secondary = getattr(delegate, "secondary_provider", None)
        if secondary is not None:
            timeout = float(getattr(secondary, "timeout", 20.0))
            max_bytes = int(getattr(secondary, "max_bytes", 1_500_000))
            user_agent = str(getattr(secondary, "user_agent", "StockAgent/1.6 research"))
            delegate.secondary_provider = V16YahooEvidenceProvider(timeout=timeout, max_bytes=max_bytes, user_agent=user_agent)

    @staticmethod
    def _sec_source(artifact: RawArtifact) -> dict[str, Any] | None:
        payload = artifact.payload if isinstance(artifact.payload, dict) else {}
        content = payload.get("document")
        url = payload.get("source_url") or payload.get("url")
        observed = payload.get("filing_date") or artifact.source_observed_at
        if not url or content in (None, "", [], {}) or _parse_time(observed) is None:
            return None
        return {
            "security_id": artifact.subject_id,
            "source_class": "SEC",
            "source_url": url,
            "source_observed_at": str(observed),
            "title": f"SEC {payload.get('form') or artifact.artifact_type}",
            "content": str(content)[:MAX_SOURCE_CONTENT],
            "origin_artifact_id": artifact.artifact_id,
            "form": payload.get("form"),
        }

    def _fetch_sec_sources(self, sid: str) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
        sources: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        artifact_ids: list[str] = []
        try:
            cik = self.sec_provider.resolve_cik(sid) if callable(getattr(self.sec_provider, "resolve_cik", None)) else None
        except Exception as exc:
            failures.append({"lane": "SEC_IDENTITY", "error": str(exc)[:240]})
            return sources, failures, artifact_ids
        identity = {"security_id": sid, "cik": cik}
        for form in ("8-K", "10-Q", "10-K"):
            try:
                artifact = self.sec_provider.fetch_filings(identity, {"form": form})
                artifact_ids.append(artifact.artifact_id)
                source = self._sec_source(artifact)
                if source is not None:
                    sources.append(source)
                else:
                    failures.append({"lane": f"SEC_{form}", "error": "no_full_document_source"})
            except Exception as exc:
                failures.append({"lane": f"SEC_{form}", "error": str(exc)[:240]})
        return sources, failures, artifact_ids

    def fetch(self, subject_id: str, query: dict[str, Any] | None = None) -> RawArtifact:
        sid = str(subject_id).upper().strip()
        query = dict(query or {})
        lane = str(self.lane_resolver(sid) or "02")
        plan = evidence_plan_for_lane(lane)
        query["v8_lane"] = plan["lane"]
        query["v8_lane_terms"] = plan["query_terms"]

        sources: list[dict[str, Any]] = []
        origin_artifact_ids: list[str] = []
        attempts: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        delegate_artifacts: list[RawArtifact] = []

        def delegate_attempt(number: int, scan_limit: int | None = None) -> None:
            attempt_query = dict(query)
            if scan_limit is not None:
                attempt_query["catalyst_news_scan_limit"] = scan_limit
            try:
                artifact = self.delegate.fetch(sid, attempt_query)
                delegate_artifacts.append(artifact)
                origin_artifact_ids.append(artifact.artifact_id)
                extracted = _artifact_sources(artifact)
                sources.extend(extracted)
                attempts.append({"attempt": number, "lane": "ISSUER_IR_AND_MEDIA", "status": "SUCCEEDED", "sources": len(extracted), "scan_limit": scan_limit})
            except Exception as exc:
                failures.append({"lane": "ISSUER_IR_AND_MEDIA", "error": str(exc)[:240]})
                attempts.append({"attempt": number, "lane": "ISSUER_IR_AND_MEDIA", "status": "FAILED", "sources": 0, "scan_limit": scan_limit})

        delegate_attempt(1, None)
        sec_sources, sec_failures, sec_artifact_ids = self._fetch_sec_sources(sid)
        sources.extend(sec_sources); failures.extend(sec_failures); origin_artifact_ids.extend(sec_artifact_ids)
        attempts.append({"attempt": 1, "lane": "SEC_8K_10Q_10K", "status": "SUCCEEDED" if sec_sources else "FAILED", "sources": len(sec_sources)})

        aux_sources, aux_failures = _fetch_auxiliary_sources(
            sid, query,
            timeout=float(query.get("aux_timeout_sec") or 30.0),
            max_bytes=int(query.get("aux_max_bytes") or 2_000_000),
            user_agent=str(query.get("aux_user_agent") or "StockAgent/1.6 research"),
        )
        sources.extend(aux_sources); failures.extend(aux_failures)
        if query.get("additional_sources"):
            attempts.append({"attempt": 1, "lane": "CONFIGURED_AUX", "status": "SUCCEEDED" if aux_sources else "FAILED", "sources": len(aux_sources)})

        sources = _dedupe_sources(sources)
        initial_catalysts = [c for source in sources for c in extract_grounded_catalysts(source)]
        refresh_attempts = 0
        if not initial_catalysts:
            refresh_attempts = 1
            delegate_attempt(2, int(query.get("catalyst_refresh_news_limit") or DEFAULT_REFRESH_NEWS_LIMIT))
            sources = _dedupe_sources(sources)

        if not sources:
            raise ProviderError(
                f"research sources unavailable for {sid}: "
                + "; ".join(f"{item['lane']}={item['error']}" for item in failures[:8])
            )

        catalysts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source in sources:
            for catalyst in extract_grounded_catalysts(source):
                key = canonical_hash(catalyst)
                if key in seen:
                    continue
                seen.add(key)
                catalysts.append(catalyst)
        chosen = max(sources, key=_source_rank)
        chosen_time = str(chosen.get("source_observed_at") or "")
        if _parse_time(chosen_time) is None:
            raise ProviderError("V1.6 research packet chosen source timestamp invalid")
        chosen_url = str(chosen.get("source_url") or "")
        chosen_content = str(chosen.get("content") or "")
        if not chosen_url or not chosen_content:
            raise ProviderError("V1.6 research packet chosen source lacks URL/content")

        successful_lanes: set[str] = set()
        if any(str(source.get("source_class") or "").upper() == "COMPANY_IR" for source in sources):
            successful_lanes.add("ISSUER_IR")
        if any(str(source.get("source_class") or "").upper() == "MAJOR_MEDIA" for source in sources):
            successful_lanes.add("SECONDARY_MEDIA")
        if any(str(source.get("source_class") or "").upper() == "SEC" and str(source.get("form") or "").upper() == "8-K" for source in sources):
            successful_lanes.add("SEC_8K")
        if any(str(source.get("source_class") or "").upper() == "SEC" and str(source.get("form") or "").upper() in {"10-Q", "10-K"} for source in sources):
            successful_lanes.add("SEC_PERIODIC")
        if any(str(source.get("source_class") or "").upper() in {"GOVERNMENT", "REGULATOR"} for source in sources):
            successful_lanes.add("GOVERNMENT_OR_REGULATOR")
        if any(str(source.get("source_class") or "").upper() in {"CUSTOMER", "INDUSTRY"} for source in sources):
            successful_lanes.add("CUSTOMER_OR_INDUSTRY")
        if catalysts:
            successful_lanes.add("GROUNDED_CATALYST")

        missing_lanes = [item for item in plan["requested_source_lanes"] if item not in successful_lanes]
        acquisition_state = "REFRESHED" if refresh_attempts else "ACQUIRED"
        if not catalysts:
            acquisition_state = "SOURCE_EXHAUSTED_AVAILABLE_LANES"
        fetched_at = utc_now()
        content_hash = canonical_hash({"source_url": chosen_url, "content": chosen_content})
        payload = {
            "security_id": sid,
            "evidence_type": "V8_HUNT_RESEARCH_PACKET_V16",
            "source_class": str(chosen.get("source_class") or "MAJOR_MEDIA"),
            "source_url": chosen_url,
            "source_observed_at": chosen_time,
            "provider": self.provider_name,
            "title": str(chosen.get("title") or sid)[:1000],
            "content": chosen_content[:MAX_SOURCE_CONTENT],
            "content_hash": content_hash,
            "evidence_items": sources,
            "catalysts": catalysts,
            "evidence_acquisition": {
                "version": HUNT_PIPELINE_VERSION,
                "state": acquisition_state,
                "plan": plan,
                "attempts": attempts,
                "refresh_attempts": refresh_attempts,
                "successful_lanes": sorted(successful_lanes),
                "missing_lanes": missing_lanes,
                "failures": failures[:100],
                "source_exhausted": not bool(catalysts),
                "grounded_catalyst_count": len(catalysts),
                "cost_cap_applied": False,
                "grade_authority": False,
                "pre_a_authority": False,
                "execution_authority": False,
            },
            "origin_artifact_ids": sorted(set(origin_artifact_ids)),
            "fetched_at": fetched_at,
        }
        payload["raw_artifact_id"] = f"artifact-v16-research-{canonical_hash(payload)[:32]}"
        return RawArtifact(
            payload["raw_artifact_id"], self.provider_name, "RESEARCH_EVIDENCE", sid,
            chosen_time, payload, canonical_hash(payload), chosen_time, fetched_at,
        )


class _ResearchAdmissionReceipt:
    """Temporary research-admission view of a canonical CatalystGate receipt.

    `as_dict()` always exposes the real canonical decision. Only the in-memory
    `.decision` property is temporarily PASS while evidence debt is unresolved,
    solely so the legacy runtime can enter research. After Full SEC, `resolve`
    swaps in the final canonical receipt; qualification reads the persisted
    latest strict receipt, never this admission shortcut.
    """

    def __init__(self, initial: Any) -> None:
        self.initial = initial
        self.final: Any | None = None

    @property
    def decision(self) -> GateDecision:
        if self.final is not None:
            return self.final.decision
        return self.initial.decision if self.initial.decision == GateDecision.PASS else GateDecision.PASS

    def as_dict(self) -> dict[str, Any]:
        receipt = (self.final or self.initial).as_dict()
        if self.final is None and self.initial.decision != GateDecision.PASS:
            receipt = dict(receipt)
            receipt["research_admission"] = "DEFERRED_EVIDENCE_DEBT"
            receipt["canonical_decision_at_admission"] = self.initial.decision.value
            receipt["final_authority"] = False
        elif self.final is not None:
            receipt = dict(receipt)
            receipt["research_admission"] = "RESOLVED_POST_RESEARCH"
            receipt["final_authority"] = True
        return receipt


class ResearchAdmissionCatalystGate:
    """Adapter around the unchanged strict CatalystGate for research admission."""

    def __init__(self, strict_gate: CatalystGate, store: Any) -> None:
        self.strict_gate = strict_gate
        self.store = store
        self._receipts: dict[str, _ResearchAdmissionReceipt] = {}

    def _subject(self, packet: dict[str, Any]) -> str | None:
        evidence_id = str(packet.get("evidence_id") or "")
        if not evidence_id:
            return None
        row = self.store.connection.execute("SELECT subject_id FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        return str(row["subject_id"]) if row and row["subject_id"] else None

    def evaluate(self, packet: dict[str, Any], rules: Any, now: datetime | None = None) -> _ResearchAdmissionReceipt:
        strict = self.strict_gate.evaluate(packet, rules, now=now)
        proxy = _ResearchAdmissionReceipt(strict)
        sid = self._subject(packet)
        if sid:
            self._receipts[sid] = proxy
        return proxy

    def resolve(self, subject_id: str, strict_receipt: Any) -> None:
        proxy = self._receipts.get(str(subject_id))
        if proxy is not None:
            proxy.final = strict_receipt


def _payload_sources_for_post_gate(research_payload: dict[str, Any], sec_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for item in research_payload.get("evidence_items") or []:
        if isinstance(item, dict):
            sources.append(dict(item))
    if research_payload.get("source_url") and research_payload.get("content") not in (None, "", [], {}):
        sources.append({
            "source_class": research_payload.get("source_class") or "RESEARCH",
            "source_url": research_payload.get("source_url"),
            "source_observed_at": research_payload.get("source_observed_at"),
            "title": research_payload.get("title"),
            "content": research_payload.get("content"),
        })
    for payload in sec_payloads:
        if not isinstance(payload, dict):
            continue
        content = payload.get("document")
        url = payload.get("source_url") or payload.get("url")
        observed = payload.get("filing_date") or payload.get("source_observed_at")
        if url and content not in (None, "", [], {}) and _parse_time(observed) is not None:
            sources.append({
                "source_class": "SEC",
                "source_url": url,
                "source_observed_at": observed,
                "title": f"SEC {payload.get('form') or 'FILING'}",
                "content": str(content)[:MAX_SOURCE_CONTENT],
            })
    return _dedupe_sources(sources)


def _latest_stage_payloads(store: Any, run_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    for row in store.list_stage_results(run_id):
        sid = str(row.get("subject_id") or "")
        stage = str(row.get("stage") or "")
        if not sid or not stage:
            continue
        try:
            payload = json.loads(row.get("result_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        stamp = str(row.get("created_at") or "") + ":" + str(row.get("result_id") or "")
        key = (stage, sid)
        if key not in latest or stamp >= latest[key][0]:
            latest[key] = (stamp, {"status": row.get("status"), "payload": payload})
    return {key: value for key, (_stamp, value) in latest.items()}


def _classify_stage(stage: str, entry: dict[str, Any]) -> str:
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    status = str(entry.get("status") or "")
    if status != "SUCCEEDED":
        return "FAIL"
    if stage in {"CAPITAL_PRESCREEN_GATE", "CATALYST_GATE"}:
        decision = str(payload.get("decision") or "")
        if decision in {"PASS", "PASS_WITH_CONSTRAINTS"}:
            return "PASS"
        if decision == "INSUFFICIENT_EVIDENCE":
            evaluation = str(payload.get("evaluation_status") or "")
            return "NOT_EVALUATED" if evaluation in {"NOT_EVALUATED_CATALYST_EVIDENCE", "EVIDENCE_DEBT_REMAINS"} else "FAIL"
        return "FAIL"
    if stage == "DEEP_RESEARCH":
        return "PASS" if payload.get("research_status") == "COMPLETE" else "FAIL"
    if stage == "FULL_SEC_FORENSIC":
        return "PASS" if payload.get("status") == "COMPLETE" else "FAIL"
    if stage == "ADVERSARIAL_AUDIT":
        return "PASS" if payload.get("audit_recommendation") in {"SUPPORTS_CONTINUATION", "SUPPORTS_WITH_CONDITIONS"} else "FAIL"
    return "PASS"


def _record_stage_telemetry(store: Any, run_id: str, latest: dict[tuple[str, str], dict[str, Any]], stage: str) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {"ENTERED": [], "PASS": [], "FAIL": [], "NOT_EVALUATED": [], "REFRESHED": []}
    for (row_stage, sid), entry in latest.items():
        if row_stage != stage:
            continue
        buckets["ENTERED"].append(sid)
        buckets[_classify_stage(stage, entry)].append(sid)
    for name in ("ENTERED", "PASS", "FAIL", "NOT_EVALUATED"):
        store.record_funnel(run_id, f"V16_{stage}_{name}", len(buckets[name]), {"security_ids": sorted(buckets[name])[:300]})
    return buckets


def _starvation_state(funnel: dict[str, int]) -> tuple[int, str | None]:
    queue = int(funnel.get("CAPITAL_PRESCREEN_PASS", 0))
    deep = int(funnel.get("DEEP_RESEARCH", 0))
    full_sec = int(funnel.get("FULL_SEC_FORENSIC", 0))
    audit = int(funnel.get("ADVERSARIAL_AUDIT", 0))
    if queue > 0 and deep == 0:
        return queue, "PRESCREEN_SURVIVORS_NEVER_REACHED_DEEP_RESEARCH"
    if deep > 0 and full_sec == 0:
        return deep, "DEEP_RESEARCH_NEVER_REACHED_FULL_SEC"
    if full_sec > 0 and audit == 0:
        return full_sec, "FULL_SEC_NEVER_REACHED_ADVERSARIAL_AUDIT"
    return 0, None


def install_hunt_pipeline_v16() -> type:
    """Install V1.6 after Alpha/V8 policies and before CLI imports classes."""
    current_base = runtime_module.ProductionStockAgent
    if getattr(current_base, "hunt_pipeline_version", None) == HUNT_PIPELINE_VERSION:
        return current_base

    class V16ProductionStockAgent(current_base):  # type: ignore[misc,valid-type]
        hunt_pipeline_version = HUNT_PIPELINE_VERSION

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._v16_lane_by_sid: dict[str, str] = {}
            self._v16_research_payload_by_sid: dict[str, dict[str, Any]] = {}
            self._v16_strict_catalyst_gate = CatalystGate()
            self.catalyst_gate = ResearchAdmissionCatalystGate(self._v16_strict_catalyst_gate, self.store)
            research = getattr(self.config, "research_provider", None)
            sec = getattr(self.config, "sec_provider", None)
            if research is not None and sec is not None and not isinstance(research, V16MultiSourceResearchProvider):
                self.config.research_provider = V16MultiSourceResearchProvider(
                    research,
                    sec,
                    lane_resolver=lambda sid: self._v16_lane_by_sid.get(str(sid).upper(), "02"),
                )

        def _record_evidence_lifecycle(self, run: Any, sid: str, research_payload: dict[str, Any], dependency_ids: list[str]) -> None:
            acquisition = research_payload.get("evidence_acquisition") if isinstance(research_payload, dict) else None
            if not isinstance(acquisition, dict):
                return
            payload = {
                "security_id": sid,
                "pipeline_version": HUNT_PIPELINE_VERSION,
                "state": acquisition.get("state"),
                "refresh_attempts": int(acquisition.get("refresh_attempts") or 0),
                "source_exhausted": bool(acquisition.get("source_exhausted")),
                "successful_lanes": list(acquisition.get("successful_lanes") or []),
                "missing_lanes": list(acquisition.get("missing_lanes") or []),
                "grounded_catalyst_count": int(acquisition.get("grounded_catalyst_count") or 0),
                "grade_authority": False,
                "pre_a_authority": False,
                "execution_authority": False,
            }
            dep_hash = self.store.dependency_hash(dependency_ids, run.rule_set.rule_set_hash, run.context_manifest_hash)
            self.store.record_stage_result(
                run.run_id, None, "EVIDENCE_DEBT", sid, payload, dependency_ids,
                dep_hash, self.store.current_evidence_epoch_for(dependency_ids),
            )
            if payload["refresh_attempts"] > 0:
                refresh = dict(payload); refresh["state"] = "REFRESHED"
                self.store.record_stage_result(
                    run.run_id, None, "EVIDENCE_REFRESH", sid, refresh, dependency_ids,
                    dep_hash, self.store.current_evidence_epoch_for(dependency_ids),
                )

        def _record_post_research_catalyst(self, run: Any, sid: str, sec_stage_payload: dict[str, Any], dependency_ids: list[str]) -> None:
            research_payload = self._v16_research_payload_by_sid.get(sid) or {}
            raw_input = sec_stage_payload.get("raw_input") if isinstance(sec_stage_payload, dict) else {}
            sec_payloads = list((raw_input or {}).get("sec_artifacts") or []) if isinstance(raw_input, dict) else []
            sources = _payload_sources_for_post_gate(research_payload, sec_payloads)
            catalysts: list[dict[str, Any]] = []
            seen: set[str] = set()
            for raw in research_payload.get("catalysts") or []:
                if isinstance(raw, dict):
                    key = canonical_hash(raw)
                    if key not in seen:
                        seen.add(key); catalysts.append(dict(raw))
            for source in sources:
                for catalyst in extract_grounded_catalysts(source):
                    key = canonical_hash(catalyst)
                    if key in seen:
                        continue
                    seen.add(key); catalysts.append(catalyst)
            source_time = _latest_time(
                [source.get("source_observed_at") for source in sources]
                + [research_payload.get("source_observed_at")]
            )
            if source_time is None:
                source_time = utc_now()
            acquisition = research_payload.get("evidence_acquisition") if isinstance(research_payload.get("evidence_acquisition"), dict) else {}
            packet_payload = {
                "security_id": sid,
                "pipeline_version": HUNT_PIPELINE_VERSION,
                "catalysts": catalysts,
                "source_count": len(sources),
                "source_urls": sorted({str(source.get("source_url")) for source in sources if source.get("source_url")})[:100],
                "source_exhausted": bool(acquisition.get("source_exhausted")),
                "refresh_attempts": int(acquisition.get("refresh_attempts") or 0),
                "source_classes": sorted({str(source.get("source_class") or "UNKNOWN") for source in sources}),
            }
            packet_hash = canonical_hash(packet_payload)
            artifact = RawArtifact(
                f"artifact-v16-catalyst-packet-{packet_hash[:32]}",
                "python-v16-evidence-packet", "CATALYST_RESEARCH_PACKET", sid,
                source_time, packet_payload, packet_hash, source_time, utc_now(),
            )
            self.store.save_raw_artifact(artifact)
            evidence_id = f"E-V16-CATALYST:{packet_hash}"
            self.store.upsert_evidence(Evidence(
                evidence_id, sid, "PYTHON_DERIVED", source_time, 0,
                artifact.payload_hash, "DERIVED", raw_artifact_id=artifact.artifact_id,
            ))
            if evidence_id not in dependency_ids:
                dependency_ids.append(evidence_id)
            packet = extract_catalyst_packet(
                packet_payload,
                artifact_id=artifact.artifact_id,
                evidence_id=evidence_id,
                fallback_source_observed_at=source_time,
            )
            receipt = self._v16_strict_catalyst_gate.evaluate(packet, run.rule_set)
            result = receipt.as_dict()
            result["pipeline_version"] = HUNT_PIPELINE_VERSION
            result["evaluation_phase"] = "POST_DEEP_RESEARCH_AND_FULL_SEC"
            result["source_exhausted"] = bool(acquisition.get("source_exhausted"))
            result["refresh_attempts"] = int(acquisition.get("refresh_attempts") or 0)
            if receipt.decision == GateDecision.PASS:
                result["evaluation_status"] = "POST_RESEARCH_VERIFIED"
            elif bool(acquisition.get("source_exhausted")):
                result["evaluation_status"] = "SOURCE_EXHAUSTED"
                result["reason_code"] = "NO_VALID_1_8W_QUANTIFIED_CATALYST_AFTER_RESEARCH"
            else:
                result["evaluation_status"] = "EVIDENCE_DEBT_REMAINS"
                result["reason_code"] = "POST_RESEARCH_CATALYST_EVIDENCE_INCOMPLETE"
            dep_hash = self.store.dependency_hash(dependency_ids, run.rule_set.rule_set_hash, run.context_manifest_hash)
            self.store.record_stage_result(
                run.run_id, None, "CATALYST_GATE", sid, result, dependency_ids,
                dep_hash, self.store.current_evidence_epoch_for(dependency_ids),
            )
            self.catalyst_gate.resolve(sid, receipt)

        def _work_stage(
            self,
            run,
            stage: str,
            prompt_id: str,
            payload: dict[str, Any],
            subject_id: str | None,
            dependency_ids: list[str],
            context_inputs: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if stage == "DEEP_RESEARCH" and subject_id:
                raw_input = payload.get("raw_input") if isinstance(payload, dict) else {}
                research_payload = (raw_input or {}).get("research_artifact") if isinstance(raw_input, dict) else None
                if isinstance(research_payload, dict):
                    sid = str(subject_id).upper()
                    self._v16_research_payload_by_sid[sid] = dict(research_payload)
                    self._record_evidence_lifecycle(run, sid, research_payload, dependency_ids)
            result = super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            if stage == "STOCK_DISCOVERY" and isinstance(result, dict):
                for candidate in result.get("candidates") or []:
                    if isinstance(candidate, dict) and candidate.get("security_id"):
                        self._v16_lane_by_sid[str(candidate["security_id"]).upper()] = infer_v8_lane(candidate)
            if stage == "FULL_SEC_FORENSIC" and subject_id:
                self._record_post_research_catalyst(run, str(subject_id).upper(), payload, dependency_ids)
            return result

        def _run_strict(self, mode, data):
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            latest = _latest_stage_payloads(self.store, run_id)
            telemetry: dict[str, dict[str, list[str]]] = {}
            for stage in ("CAPITAL_PRESCREEN_GATE", "EVIDENCE_DEBT", "EVIDENCE_REFRESH", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "CATALYST_GATE", "ADVERSARIAL_AUDIT"):
                telemetry[stage] = _record_stage_telemetry(self.store, run_id, latest, stage)

            prescreen_pass = len(telemetry["CAPITAL_PRESCREEN_GATE"]["PASS"])
            deep_pass = len(telemetry["DEEP_RESEARCH"]["PASS"])
            full_sec_pass = len(telemetry["FULL_SEC_FORENSIC"]["PASS"])
            audit_entered = len(telemetry["ADVERSARIAL_AUDIT"]["ENTERED"])
            catalyst_pass = len(telemetry["CATALYST_GATE"]["PASS"])
            catalyst_ne = len(telemetry["CATALYST_GATE"]["NOT_EVALUATED"])
            catalyst_fail = len(telemetry["CATALYST_GATE"]["FAIL"])
            refreshed = len(telemetry["EVIDENCE_REFRESH"]["ENTERED"])

            self.store.record_funnel(run_id, "CAPITAL_PRESCREEN_PASS", prescreen_pass)
            self.store.record_funnel(run_id, "DEEP_RESEARCH", deep_pass)
            self.store.record_funnel(run_id, "FULL_SEC_FORENSIC", full_sec_pass)
            self.store.record_funnel(run_id, "ADVERSARIAL_AUDIT", audit_entered)
            self.store.record_funnel(run_id, "CATALYST_PASS", catalyst_pass)
            self.store.record_funnel(run_id, "CATALYST_NOT_EVALUATED", catalyst_ne)
            self.store.record_funnel(run_id, "CATALYST_UNKNOWN", catalyst_ne + catalyst_fail)
            self.store.record_funnel(run_id, "V16_EVIDENCE_REFRESHED", refreshed, {"security_ids": sorted(telemetry["EVIDENCE_REFRESH"]["ENTERED"])[:300]})
            self.store.record_funnel(run_id, "V16_POST_RESEARCH_CATALYST_REJECT", catalyst_fail, {"security_ids": sorted(telemetry["CATALYST_GATE"]["FAIL"])[:300]})
            self.store.record_funnel(run_id, "V16_PIPELINE_VERSION", 1, {
                "version": HUNT_PIPELINE_VERSION,
                "catalyst_gate_relaxed": False,
                "qualification_uses_latest_post_research_strict_receipt": True,
                "pre_a_auto_promotion": False,
                "broker_write_authority": False,
            })

            funnel = {str(row["funnel_stage"]): int(row["count"]) for row in self.store.list_funnel(run_id)}
            starvation_count, starvation_reason = _starvation_state(funnel)
            self.store.record_funnel(run_id, "V8_PIPELINE_STARVATION", starvation_count, {
                "status": "ENGINEERING_INCIDENT" if starvation_count else "PASS",
                "reason": starvation_reason,
                "pipeline_version": HUNT_PIPELINE_VERSION,
                "prescreen_pass": prescreen_pass,
                "deep_research_pass": deep_pass,
                "full_sec_pass": full_sec_pass,
                "audit_entered": audit_entered,
            })
            if starvation_count and str(getattr(outcome, "outcome", "")) in {"NO_QUALIFIED_CANDIDATE", "BLOCKED_BY_EVIDENCE_GAP"}:
                self.store.finish_run(run_id, "BLOCKED_BY_PIPELINE_STARVATION")
                return RunOutcome(run_id, mode, "BLOCKED_BY_PIPELINE_STARVATION", blocked_reason=str(starvation_reason))
            return outcome

    runtime_module.ProductionStockAgent = V16ProductionStockAgent

    base_shadow_runner = shadow_module.DailyShadowRunner
    if getattr(base_shadow_runner, "hunt_pipeline_version", None) != HUNT_PIPELINE_VERSION:
        class V16DailyShadowRunner(base_shadow_runner):  # type: ignore[misc,valid-type]
            hunt_pipeline_version = HUNT_PIPELINE_VERSION

            def _run_log(self, shadow_run_id: str, hunt_run_id: str, execution_run_id: str | None, health: dict[str, Any], status: str) -> dict[str, Any]:
                log = super()._run_log(shadow_run_id, hunt_run_id, execution_run_id, health, status)
                funnel = {str(row["funnel_stage"]): int(row["count"]) for row in self.store.list_funnel(hunt_run_id)}
                starvation = int(funnel.get("V8_PIPELINE_STARVATION", 0))
                if starvation > 0:
                    row = next((item for item in self.store.list_funnel(hunt_run_id) if str(item.get("funnel_stage")) == "V8_PIPELINE_STARVATION"), None)
                    details = self._decode((row or {}).get("details_json") or "{}")
                    reason = str((details or {}).get("reason") or "PIPELINE_STARVATION") if isinstance(details, dict) else "PIPELINE_STARVATION"
                    log["hunt_contract"]["status"] = "ENGINEERING_INCIDENT_PIPELINE_STARVATION"
                    log["pipeline_health"] = {"status": "DEGRADED", "reason": reason, "count": starvation, "version": HUNT_PIPELINE_VERSION}
                    incident_id = f"incident-{canonical_hash([shadow_run_id, 'PIPELINE_STARVATION', reason])[:24]}"
                    exists = self.store.connection.execute("SELECT 1 FROM shadow_incidents WHERE incident_id=?", (incident_id,)).fetchone()
                    if exists is None:
                        self.store.append_shadow_incident(shadow_run_id, {
                            "incident_id": incident_id,
                            "detected_at": utc_now(),
                            "run_id": shadow_run_id,
                            "shadow_version": self.shadow_version,
                            "severity": "S1",
                            "component": "HUNT_PIPELINE",
                            "stage": "V8_PIPELINE_STARVATION",
                            "failure_code": reason,
                            "retryable": True,
                            "description": f"V8 HUNT pipeline starvation: {reason}",
                            "impact": "NO_TRADE_NOT_CLEAN_OPPORTUNITY_CONCLUSION",
                            "status": "OPEN",
                        })
                return log

        shadow_module.DailyShadowRunner = V16DailyShadowRunner

    return V16ProductionStockAgent
