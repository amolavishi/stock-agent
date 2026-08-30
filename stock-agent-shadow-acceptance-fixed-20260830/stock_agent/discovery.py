"""Deterministic broad-universe eligibility filtering.

This module owns only hard universe eligibility arithmetic.  It does not
choose a DiscoveryDecision, catalyst status, execution action, or position
size.  Missing market data fails closed as insufficient evidence rather than
being guessed from an LLM narrative.
"""
from __future__ import annotations

import math
from typing import Any


FILTER_VERSION = "universe-prefilter-v1"
SUPPORTED_US_VENUES = {"NYSE", "NASDAQ", "AMEX", "NYSE AMERICAN", "NYSEAMERICAN"}


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _last_price(row: dict[str, Any]) -> tuple[float | None, str | None]:
    for key in ("price", "current_price", "last_price", "lastPrice"):
        value = _finite_positive(row.get(key))
        if value is not None:
            return value, key
    prices = row.get("prices")
    if isinstance(prices, list) and prices:
        value = _finite_positive(prices[-1])
        if value is not None:
            return value, "prices[-1]"
    return None, None


def _market_cap(row: dict[str, Any], price: float | None) -> tuple[float | None, str | None]:
    for key in ("market_cap", "marketCap"):
        value = _finite_positive(row.get(key))
        if value is not None:
            return value, key
    shares = None
    for key in ("shares_outstanding", "sharesOutstanding"):
        shares = _finite_positive(row.get(key))
        if shares is not None:
            break
    if shares is not None and price is not None:
        return shares * price, "shares_outstanding*price"
    return None, None


def _average_dollar_volume(row: dict[str, Any], price: float | None) -> tuple[float | None, str | None]:
    for key in ("average_dollar_volume", "avg_dollar_volume", "averageDollarVolume", "adv"):
        value = _finite_positive(row.get(key))
        if value is not None:
            return value, key
    for key in ("average_volume", "avg_volume", "averageVolume"):
        volume = _finite_positive(row.get(key))
        if volume is not None and price is not None:
            return volume * price, f"{key}*price"
    volumes = row.get("volumes")
    if isinstance(volumes, list) and volumes and price is not None:
        clean = [_finite_positive(item) for item in volumes]
        clean = [item for item in clean if item is not None]
        if clean:
            return (sum(clean) / len(clean)) * price, "mean(volumes)*price"
    return None, None


def deterministic_universe_prefilter(
    rows: list[dict[str, Any]],
    *,
    min_price: float,
    min_market_cap: float,
    min_average_dollar_volume: float,
) -> dict[str, Any]:
    """Return a deterministic, auditable eligibility packet.

    Counts are cumulative funnel counts: PRICE_PASS is evaluated after basic
    identity/venue support; MARKET_CAP_PASS after price; ADV_PASS after both.
    Unknown required values never pass.  Explicit below-threshold observations
    receive the stable Architecture v1.1 reason codes.
    """
    thresholds = {
        "min_price": float(min_price),
        "min_market_cap": float(min_market_cap),
        "min_average_dollar_volume": float(min_average_dollar_volume),
    }
    evaluations: list[dict[str, Any]] = []
    supported_rows: list[dict[str, Any]] = []
    price_rows: list[dict[str, Any]] = []
    cap_rows: list[dict[str, Any]] = []
    adv_rows: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        sid = str(row.get("security_id") or row.get("ticker") or "")
        venue = str(row.get("venue") or row.get("market") or "").upper().strip()
        security_type = str(row.get("security_type") or row.get("securityType") or "").upper().strip()
        reasons: list[str] = []
        unknown_fields: list[str] = []

        supported = bool(sid) and (not venue or venue in SUPPORTED_US_VENUES)
        if security_type and security_type not in {"COMMON", "COMMON_STOCK", "COMMON SHARE", "COMMON_SHARES", "STOCK"}:
            supported = False
        if not supported:
            reasons.append("UNIVERSE_UNSUPPORTED_SECURITY")
        else:
            supported_rows.append(row)

        price, price_source = _last_price(row)
        cap, cap_source = _market_cap(row, price)
        adv, adv_source = _average_dollar_volume(row, price)

        price_ok = supported and price is not None and price >= thresholds["min_price"]
        if supported and price is None:
            unknown_fields.append("price")
        elif supported and price is not None and not price_ok:
            reasons.append("UNIVERSE_LOW_PRICE")
        if price_ok:
            price_rows.append(row)

        cap_ok = price_ok and cap is not None and cap >= thresholds["min_market_cap"]
        if price_ok and cap is None:
            unknown_fields.append("market_cap")
        elif price_ok and cap is not None and not cap_ok:
            reasons.append("UNIVERSE_LOW_MARKET_CAP")
        if cap_ok:
            cap_rows.append(row)

        adv_ok = cap_ok and adv is not None and adv >= thresholds["min_average_dollar_volume"]
        if cap_ok and adv is None:
            unknown_fields.append("average_dollar_volume")
        elif cap_ok and adv is not None and not adv_ok:
            reasons.append("UNIVERSE_LOW_LIQUIDITY")
        if adv_ok:
            adv_rows.append(row)

        eligible = bool(adv_ok and not reasons and not unknown_fields)
        evaluations.append(
            {
                "row_index": index,
                "security_id": sid or None,
                "venue": venue or None,
                "price": price,
                "price_source": price_source,
                "market_cap": cap,
                "market_cap_source": cap_source,
                "average_dollar_volume": adv,
                "average_dollar_volume_source": adv_source,
                "eligible": eligible,
                "status": "PASS" if eligible else ("INSUFFICIENT_EVIDENCE" if unknown_fields and not reasons else "REJECT"),
                "reason_codes": reasons,
                "unknown_fields": unknown_fields,
            }
        )

    eligible_ids = [entry["security_id"] for entry in evaluations if entry["eligible"] and entry["security_id"]]
    return {
        "version": FILTER_VERSION,
        "thresholds": thresholds,
        "counts": {
            "RAW_UNIVERSE": len(rows),
            "SUPPORTED_SECURITY": len(supported_rows),
            "PRICE_FILTER": len(price_rows),
            "MARKET_CAP_FILTER": len(cap_rows),
            "ADV_FILTER": len(adv_rows),
        },
        "eligible_security_ids": eligible_ids,
        "evaluations": evaluations,
    }

