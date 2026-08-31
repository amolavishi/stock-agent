"""Targeted V1.6 catalyst extraction hardening.

This augments the existing source-grounded V1.5 extractor without changing
CatalystGate.  It covers common contract language where the quantified amount
appears *inside* the award phrase (for example "awarded a $250 million
contract"), which V1.5's literal phrase matcher misses.

The augmentation is intentionally narrow: the money value must be syntactically
bound to the contract phrase.  A remote analyst target or valuation number can
never satisfy this extractor.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .catalyst_acquisition_v15 import extract_grounded_catalysts as _v15_extract
from .models import canonical_hash


V16_EXTRACTOR_VERSION = "catalyst-extractor-v1.6"
_AMOUNT = r"\$\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<scale>trillion|billion|million|tn|bn|mm|m|b)?"
_CONTRACT_PATTERNS = (
    re.compile(rf"\b(?:awarded|won|secured|received)\s+(?:a\s+|an\s+|the\s+)?{_AMOUNT}\s+(?:multi[- ]year\s+)?contract\b", re.I),
    re.compile(rf"\bcontract\s+(?:award\s+)?(?:valued\s+at|worth|of|for)\s+{_AMOUNT}\b", re.I),
    re.compile(rf"\b{_AMOUNT}\s+(?:multi[- ]year\s+)?contract\s+(?:award|win)\b", re.I),
)
_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(\d{1,2}),\s+(20\d{2})\b",
    re.I,
)


def _scaled_amount(match: re.Match[str]) -> float:
    value = float(match.group("value"))
    scale = str(match.group("scale") or "").casefold()
    if scale in {"trillion", "tn"}:
        value *= 1_000_000_000_000
    elif scale in {"billion", "bn", "b"}:
        value *= 1_000_000_000
    elif scale in {"million", "mm", "m"}:
        value *= 1_000_000
    return value


def _event_date(text: str, source_time: str) -> str:
    match = _DATE.search(text)
    if match:
        try:
            parsed = datetime.strptime(match.group(0), "%B %d, %Y").replace(tzinfo=timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return source_time


def extract_grounded_catalysts_v16(source: dict[str, Any]) -> list[dict[str, Any]]:
    catalysts = [dict(item) for item in _v15_extract(source)]
    title = str(source.get("title") or "")
    content = str(source.get("content") or "")
    text = re.sub(r"\s+", " ", f"{title}. {content}").strip()
    source_url = str(source.get("source_url") or source.get("url") or "").strip()
    source_time = str(source.get("source_observed_at") or source.get("published_at") or source.get("observed_at") or "").strip()
    if not text or not source_url or not source_time:
        return catalysts

    source_class = str(source.get("source_class") or "MAJOR_MEDIA").upper()
    verification = "OFFICIAL" if source_class in {"COMPANY_IR", "SEC", "SEC_EDGAR", "GOVERNMENT", "REGULATOR"} else "VERIFIED"
    seen = {canonical_hash(item) for item in catalysts}
    for pattern in _CONTRACT_PATTERNS:
        for match in pattern.finditer(text):
            amount = _scaled_amount(match)
            if amount <= 0:
                continue
            start = max(0, match.start() - 180)
            end = min(len(text), match.end() + 220)
            local = text[start:end]
            event_at = _event_date(local, source_time)
            payload = {
                "event_type": "CONTRACT_AWARD",
                "event_at": event_at,
                "verification_status": verification,
                "binding_status": "BINDING",
                "economic_transmission": {
                    "metric": "revenue_or_backlog",
                    "direction": "POSITIVE",
                    "amount": amount,
                },
                "confirmation_metric": "Confirm backlog/revenue conversion in a filing or earnings release",
                "source_url": source_url,
                "source_observed_at": source_time,
                "source_class": source_class,
                "acquisition_version": V16_EXTRACTOR_VERSION,
            }
            payload["catalyst_id"] = f"V16-{canonical_hash(payload)[:20]}"
            key = canonical_hash(payload)
            if key not in seen:
                seen.add(key)
                catalysts.append(payload)
    return catalysts


def install_v16_extractor(module: Any) -> None:
    """Replace only V1.6's local extractor reference; V1.5 remains unchanged."""
    module.extract_grounded_catalysts = extract_grounded_catalysts_v16
