"""V8-style catalyst evidence acquisition for PRIMARY HUNT.

RUN-20260831-009 proved that Discovery and cheap capital screening were no
longer the bottleneck, but every prescreen survivor stopped at CatalystGate.
The failure was evidence acquisition: the research adapter returned one
issuer/media page and the deterministic extractor only saw that one page.

This layer fixes recall without weakening CatalystGate. It:
- scans a bounded set of issuer-identifiable Yahoo RSS items instead of taking
  the first item;
- always attempts issuer IR and a secondary media lane when available;
- extracts only source-grounded, event-local quantified catalyst facts;
- preserves every source URL/timestamp in one immutable research bundle;
- never emits Research Grade, PRE-A readiness, ExecutionAction, or sizing.

UNKNOWN still means UNKNOWN. A catalyst reaches the existing gate only when
source text itself supports the event type, timing/provenance and a quantified
economic transmission.
"""
from __future__ import annotations

import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from . import adapters as adapters_module
from .adapters import ProviderError, IssuerIRWebEvidenceProvider
from .models import RawArtifact, canonical_hash, utc_now


CATALYST_ACQUISITION_VERSION = "catalyst-evidence-acquisition-v1.5"
DEFAULT_NEWS_SCAN_LIMIT = 30
MAX_NEWS_SCAN_LIMIT = 100

_MONTHS = (
    "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    "Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
_DATE_RE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}})(?:,\s*|\s+)(20\d{{2}})\b", re.I)
_DOLLAR_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*(trillion|billion|million|tn|bn|mm|m|b)?\b", re.I)
_PERCENT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*%")
_BPS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:bps|basis\s+points?)\b", re.I)
_UNIT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(gw|mw|gwh|mwh|tb|pb|units?|locations?|customers?)\b", re.I)

_EVENT_RULES: tuple[tuple[str, re.Pattern[str], str, str, str], ...] = (
    (
        "MOU",
        re.compile(r"\b(?:memorandum\s+of\s+understanding|MOU)\b", re.I),
        "commercial_value",
        "NOT_BINDING",
        "Confirm a binding definitive agreement and quantified economics",
    ),
    (
        "LOI",
        re.compile(r"\b(?:letter\s+of\s+intent|LOI)\b", re.I),
        "commercial_value",
        "NOT_BINDING",
        "Confirm a binding definitive agreement and quantified economics",
    ),
    (
        "CONTRACT_AWARD",
        re.compile(r"\b(?:contract\s+award(?:ed)?|awarded\s+(?:a|the)\s+contract|contract\s+valued|purchase\s+order|new\s+order|backlog\s+(?:rose|increased|grew|reached))\b", re.I),
        "revenue_or_backlog",
        "BINDING",
        "Confirm backlog/revenue conversion in a filing or earnings release",
    ),
    (
        "GUIDANCE_RAISE",
        re.compile(r"\b(?:raised|raises|increased|increases)\s+(?:full[- ]year\s+|annual\s+)?(?:guidance|outlook)|\b(?:guidance|outlook)\s+(?:raised|increased)\b", re.I),
        "guidance",
        "NOT_APPLICABLE",
        "Confirm the raised range and subsequent estimate revisions",
    ),
    (
        "BUYBACK",
        re.compile(r"\b(?:share|stock)\s+repurchase|\bbuyback\b|repurchase\s+authorization", re.I),
        "capital_return",
        "NOT_APPLICABLE",
        "Confirm authorization size and actual repurchase activity",
    ),
    (
        "REFINANCING",
        re.compile(r"\b(?:refinanc(?:e|ed|ing)|debt\s+refinancing|extended\s+(?:its\s+)?debt\s+maturity|repaid\s+(?:its\s+)?debt)\b", re.I),
        "financing_cost_or_maturity",
        "BINDING",
        "Confirm closing terms, maturity and interest-cost impact",
    ),
    (
        "CAPACITY_EXPANSION",
        re.compile(r"\b(?:capacity\s+expansion|expand(?:s|ed|ing)?\s+capacity|production\s+expansion|additional\s+capacity|new\s+(?:plant|facility|data\s+center))\b", re.I),
        "capacity",
        "NOT_APPLICABLE",
        "Confirm commissioning timing and utilization/revenue conversion",
    ),
    (
        "REGULATORY_APPROVAL",
        re.compile(r"\b(?:regulatory\s+approval|FDA\s+approval|approved\s+by\s+the\s+FDA|receiv(?:e|ed|es)\s+FDA\s+approval)\b", re.I),
        "addressable_revenue",
        "NOT_APPLICABLE",
        "Confirm launch timing, label scope and commercial uptake",
    ),
    (
        "CUSTOMER_WIN",
        re.compile(r"\b(?:new\s+(?:major|large|strategic)\s+customer|selected\s+as\s+(?:a|the)\s+supplier|customer\s+award|multi[- ]year\s+customer\s+agreement)\b", re.I),
        "customer_revenue",
        "BINDING",
        "Confirm customer contribution and concentration change",
    ),
    (
        "EARNINGS_RESULT",
        re.compile(r"\b(?:beat(?:s)?\s+(?:analyst\s+)?expectations|record\s+revenue|revenue\s+(?:grew|increased|rose)|EPS\s+(?:grew|increased|rose)|margin\s+(?:expanded|increased))\b", re.I),
        "revenue_or_eps",
        "NOT_APPLICABLE",
        "Confirm estimate revisions and persistence into the next reporting period",
    ),
)


def _news_scan_limit(query: dict[str, Any] | None) -> int:
    raw = (query or {}).get("catalyst_news_scan_limit")
    if raw in (None, ""):
        raw = os.getenv("STOCK_AGENT_CATALYST_NEWS_SCAN_LIMIT", str(DEFAULT_NEWS_SCAN_LIMIT))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ProviderError("catalyst news scan limit must be an integer") from exc
    if not 1 <= value <= MAX_NEWS_SCAN_LIMIT:
        raise ProviderError(f"catalyst news scan limit must be 1..{MAX_NEWS_SCAN_LIMIT}")
    return value


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        try:
            import email.utils
            parsed = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _event_date(window: str, source_time: str) -> str:
    match = _DATE_RE.search(window)
    if match:
        raw = match.group(0)
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
            except ValueError:
                pass
    # These rules describe announced/realized events. If the article does not
    # state a separate future event date, publication time is the conservative
    # event timestamp. CatalystGate still applies its recent-event grace.
    return source_time


def _quantified_transmission(window: str, metric: str) -> dict[str, Any] | None:
    dollar = _DOLLAR_RE.search(window)
    if dollar:
        value = float(dollar.group(1))
        scale = str(dollar.group(2) or "").casefold()
        if scale in {"trillion", "tn"}:
            value *= 1_000_000_000_000
        elif scale in {"billion", "bn", "b"}:
            value *= 1_000_000_000
        elif scale in {"million", "mm", "m"}:
            value *= 1_000_000
        if math.isfinite(value) and value > 0:
            return {"metric": metric, "direction": "POSITIVE", "amount": value}
    percent = _PERCENT_RE.search(window)
    if percent:
        value = float(percent.group(1))
        if math.isfinite(value) and value > 0:
            return {"metric": metric, "direction": "POSITIVE", "percent": value}
    bps = _BPS_RE.search(window)
    if bps:
        value = float(bps.group(1))
        if math.isfinite(value) and value > 0:
            return {"metric": metric, "direction": "POSITIVE", "bps": value}
    units = _UNIT_RE.search(window)
    if units:
        value = float(units.group(1))
        if math.isfinite(value) and value > 0:
            return {"metric": metric, "direction": "POSITIVE", "amount": value, "unit": units.group(2).upper()}
    return None


def _source_verification(source_class: str) -> str:
    value = str(source_class or "").upper()
    if value in {"COMPANY_IR", "SEC", "SEC_EDGAR", "GOVERNMENT", "REGULATOR"}:
        return "OFFICIAL"
    return "VERIFIED"


def extract_grounded_catalysts(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract event-local quantified catalysts from one immutable source.

    Numbers are searched only in a bounded window around the event phrase.
    A random valuation number elsewhere in the article therefore cannot make
    an unrelated event pass CatalystGate.
    """
    source_url = str(source.get("source_url") or source.get("url") or "").strip()
    source_time_raw = source.get("source_observed_at") or source.get("published_at") or source.get("observed_at")
    parsed_time = _parse_time(source_time_raw)
    title = str(source.get("title") or "")
    content = str(source.get("content") or source.get("description") or "")
    text = re.sub(r"\s+", " ", f"{title}. {content}").strip()
    if not source_url or parsed_time is None or not text:
        return []
    source_time = parsed_time.isoformat().replace("+00:00", "Z")
    source_class = str(source.get("source_class") or "MAJOR_MEDIA")
    verification = _source_verification(source_class)

    catalysts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event_type, pattern, metric, binding, confirmation in _EVENT_RULES:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 360)
            end = min(len(text), match.end() + 520)
            window = text[start:end]
            transmission = _quantified_transmission(window, metric)
            if transmission is None:
                continue
            event_at = _event_date(window, source_time)
            key = canonical_hash({
                "source_url": source_url,
                "event_type": event_type,
                "event_at": event_at,
                "transmission": transmission,
            })
            if key in seen:
                continue
            seen.add(key)
            catalysts.append({
                "catalyst_id": f"V15-{key[:20]}",
                "event_type": event_type,
                "event_at": event_at,
                "verification_status": verification,
                "binding_status": binding,
                "economic_transmission": transmission,
                "confirmation_metric": confirmation,
                "source_url": source_url,
                "source_observed_at": source_time,
                "source_class": source_class,
                "acquisition_version": CATALYST_ACQUISITION_VERSION,
            })
    return catalysts


def _source_from_artifact(artifact: RawArtifact) -> list[dict[str, Any]]:
    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    sources: list[dict[str, Any]] = []
    items = payload.get("evidence_items")
    if isinstance(items, list):
        sources.extend(dict(item) for item in items if isinstance(item, dict))
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
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        key = canonical_hash({
            "url": source.get("source_url"),
            "time": source.get("source_observed_at"),
            "title": source.get("title"),
            "content": source.get("content"),
        })
        if key in seen:
            continue
        seen.add(key)
        source.setdefault("origin_artifact_id", artifact.artifact_id)
        deduped.append(source)
    return deduped


_BaseYahoo = adapters_module.YahooFinanceNewsEvidenceProvider


class CatalystAwareYahooFinanceNewsEvidenceProvider(_BaseYahoo):
    """Scan the issuer RSS set for catalyst evidence instead of first-hit news."""

    provider_name = "yahoo-finance-news-catalyst-v15"

    def fetch(self, subject_id: str, query: dict[str, Any] | None = None) -> RawArtifact:
        sid = str(subject_id).upper().strip()
        if not sid or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sid):
            raise ProviderError("Yahoo news ticker is malformed")
        limit = _news_scan_limit(query)
        params = urllib.parse.urlencode({"s": sid, "region": "US", "lang": "en-US"})
        feed_url = f"{self.BASE_URL}?{params}"
        request = urllib.request.Request(
            feed_url,
            headers={"Accept": "application/rss+xml,application/xml", "User-Agent": self.user_agent},
        )
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
        IssuerIRWebEvidenceProvider._validate_url(str(final_url))
        if not IssuerIRWebEvidenceProvider._host_allowed(str(final_url), ["feeds.finance.yahoo.com"]):
            raise ProviderError("Yahoo news redirect crossed configured host boundary")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ProviderError("Yahoo news RSS payload is malformed") from exc

        items: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for item in root.findall(".//item"):
            if len(items) >= limit:
                break
            title = str(item.findtext("title") or "").strip()
            link = str(item.findtext("link") or "").strip()
            description = str(item.findtext("description") or "").strip()
            published = self._parse_date(str(item.findtext("pubDate") or item.findtext("published") or ""))
            if not title or not link or published is None:
                continue
            if published > now + adapters_module.timedelta(minutes=5):
                raise ProviderError("Yahoo news publication timestamp is in the future")
            IssuerIRWebEvidenceProvider._validate_url(link)
            if not IssuerIRWebEvidenceProvider._host_allowed(link, ["finance.yahoo.com"]):
                continue
            identity_text = f"{title} {description}".casefold()
            if sid.casefold() not in identity_text:
                continue
            content = re.sub(r"\s+", " ", f"{title}. {description}").strip()[:120_000]
            published_text = published.isoformat().replace("+00:00", "Z")
            source = {
                "security_id": sid,
                "source_class": "MAJOR_MEDIA",
                "source_url": link,
                "source_observed_at": published_text,
                "title": title[:1000],
                "content": content,
            }
            source["catalysts"] = extract_grounded_catalysts(source)
            items.append(source)
        if not items:
            raise ProviderError(f"Yahoo news feed has no issuer-identifiable article for {sid}")

        catalyst_items = [item for item in items if item.get("catalysts")]
        chosen = catalyst_items[0] if catalyst_items else items[0]
        catalysts: list[dict[str, Any]] = []
        seen_catalysts: set[str] = set()
        for item in items:
            for catalyst in item.get("catalysts") or []:
                key = canonical_hash(catalyst)
                if key in seen_catalysts:
                    continue
                seen_catalysts.add(key)
                catalysts.append(dict(catalyst))
        content_hash = canonical_hash({"source_url": chosen["source_url"], "content": chosen["content"]})
        fetched_at = utc_now()
        payload = {
            "security_id": sid,
            "evidence_type": "NEWS_CATALYST_SCAN",
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
            "catalyst_acquisition": {
                "version": CATALYST_ACQUISITION_VERSION,
                "strategy": "V8_SOURCE_EXHAUSTION_RSS_SCAN",
                "scan_limit": limit,
                "issuer_identifiable_items_scanned": len(items),
                "grounded_catalyst_count": len(catalysts),
                "cost_cap_applied": False,
            },
            "fetched_at": fetched_at,
        }
        payload["raw_artifact_id"] = f"artifact-yahoo-catalyst-{canonical_hash(payload)[:32]}"
        return RawArtifact(
            payload["raw_artifact_id"], self.provider_name, "RESEARCH_EVIDENCE", sid,
            chosen["source_observed_at"], payload, canonical_hash(payload),
            chosen["source_observed_at"], fetched_at,
        )


class CatalystEvidenceCompositeResearchProvider:
    """V8 research bundle: issuer IR plus catalyst-aware secondary evidence."""

    provider_name = "composite-research-catalyst-v15"

    def __init__(self, issuer_provider: Any, secondary_provider: Any | None = None) -> None:
        self.issuer_provider = issuer_provider
        self.secondary_provider = secondary_provider or CatalystAwareYahooFinanceNewsEvidenceProvider()

    def fetch(self, subject_id: str, query: dict[str, Any] | None = None) -> RawArtifact:
        sid = str(subject_id).upper().strip()
        query = dict(query or {})
        artifacts: list[RawArtifact] = []
        failures: list[dict[str, str]] = []

        # Cost is deliberately not a stopping criterion. For every candidate we
        # try both authority lanes; source exhaustion, not price, ends search.
        for lane, provider in (("ISSUER_IR", self.issuer_provider), ("SECONDARY_MEDIA", self.secondary_provider)):
            try:
                artifacts.append(provider.fetch(sid, query))
            except ProviderError as exc:
                failures.append({"lane": lane, "provider": str(getattr(provider, "provider_name", provider.__class__.__name__)), "error": str(exc)[:240]})
        if not artifacts:
            raise ProviderError(
                f"research sources unavailable for {sid}: " + "; ".join(f"{item['lane']}={item['error']}" for item in failures)
            )

        sources: list[dict[str, Any]] = []
        for artifact in artifacts:
            sources.extend(_source_from_artifact(artifact))
        if not sources:
            raise ProviderError(f"research bundle has no normalized source content for {sid}")

        catalysts: list[dict[str, Any]] = []
        source_catalyst_keys: set[str] = set()
        for source in sources:
            for catalyst in extract_grounded_catalysts(source):
                key = canonical_hash(catalyst)
                if key in source_catalyst_keys:
                    continue
                source_catalyst_keys.add(key)
                catalysts.append(catalyst)

        def source_rank(source: dict[str, Any]) -> tuple[int, int, float]:
            has_catalyst = int(bool(extract_grounded_catalysts(source)))
            authority = 2 if str(source.get("source_class") or "").upper() == "COMPANY_IR" else 1
            parsed = _parse_time(source.get("source_observed_at"))
            stamp = parsed.timestamp() if parsed else 0.0
            return has_catalyst, authority, stamp

        chosen = max(sources, key=source_rank)
        chosen_time = str(chosen.get("source_observed_at") or "")
        if _parse_time(chosen_time) is None:
            raise ProviderError("research bundle chosen source lacks valid source_observed_at")
        chosen_url = str(chosen.get("source_url") or "")
        chosen_content = str(chosen.get("content") or "")
        if not chosen_url or not chosen_content:
            raise ProviderError("research bundle chosen source lacks URL/content")
        content_hash = canonical_hash({"source_url": chosen_url, "content": chosen_content})
        fetched_at = utc_now()
        payload = {
            "security_id": sid,
            "evidence_type": "V8_CATALYST_RESEARCH_BUNDLE",
            "source_class": str(chosen.get("source_class") or "MAJOR_MEDIA"),
            "source_url": chosen_url,
            "source_observed_at": chosen_time,
            "provider": self.provider_name,
            "title": str(chosen.get("title") or sid)[:1000],
            "content": chosen_content[:120_000],
            "content_hash": content_hash,
            "evidence_items": sources[:MAX_NEWS_SCAN_LIMIT + 10],
            "catalysts": catalysts,
            "catalyst_acquisition": {
                "version": CATALYST_ACQUISITION_VERSION,
                "strategy": "V8_AUTHORITY_LANES_THEN_SOURCE_EXHAUSTION",
                "attempted_lanes": ["ISSUER_IR", "SECONDARY_MEDIA"],
                "successful_artifacts": len(artifacts),
                "normalized_sources": len(sources),
                "grounded_catalyst_count": len(catalysts),
                "failures": failures,
                "source_exhausted": True,
                "cost_cap_applied": False,
                "grade_authority": False,
                "execution_authority": False,
            },
            "origin_artifact_ids": [artifact.artifact_id for artifact in artifacts],
            "fetched_at": fetched_at,
        }
        payload["raw_artifact_id"] = f"artifact-catalyst-bundle-{canonical_hash(payload)[:32]}"
        return RawArtifact(
            payload["raw_artifact_id"], self.provider_name, "RESEARCH_EVIDENCE", sid,
            chosen_time, payload, canonical_hash(payload), chosen_time, fetched_at,
        )


_INSTALLED = False


def install_catalyst_evidence_acquisition_v15() -> None:
    """Install after Alpha V1.3/V1.4 and before CLI provider construction."""
    global _INSTALLED
    if _INSTALLED:
        return
    adapters_module.YahooFinanceNewsEvidenceProvider = CatalystAwareYahooFinanceNewsEvidenceProvider
    adapters_module.CompositeResearchEvidenceProvider = CatalystEvidenceCompositeResearchProvider
    _INSTALLED = True
