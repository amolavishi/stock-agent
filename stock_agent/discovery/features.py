from __future__ import annotations

from statistics import mean
from datetime import datetime, timedelta, timezone
from typing import Any

from .schemas import CandidateFeatureSnapshot, DailyBar, FieldValue, MarketQuote, SecurityMasterRecord, UnknownState


FEATURE_VERSION = "discovery_features_v001"


def _known(value: Any, source: str, as_of: str, source_ids: tuple[str, ...] = ()) -> FieldValue:
    return FieldValue(value=value, state=UnknownState.KNOWN.value, source=source,
                      observed_at=as_of, ingested_at=as_of,
                      calculation_version=FEATURE_VERSION, source_ids=source_ids)


def _unknown(reason: str = UnknownState.UNKNOWN_NOT_AVAILABLE.value) -> FieldValue:
    return FieldValue(value=None, state=reason, source="", calculation_version=FEATURE_VERSION)


def _values(bars: list[DailyBar]) -> list[float]:
    return [float(bar.adjusted_close if bar.adjusted_close is not None else bar.close)
            for bar in bars if bar.usable and (bar.adjusted_close or bar.close)]


def _return(prices: list[float], days: int) -> float | None:
    if len(prices) <= days or prices[-days - 1] <= 0:
        return None
    return round((prices[-1] / prices[-days - 1] - 1) * 100, 4)


def _ma(prices: list[float], days: int) -> float | None:
    return round(mean(prices[-days:]), 6) if len(prices) >= days else None


def _atr_pct(bars: list[DailyBar], days: int = 14) -> float | None:
    usable = [bar for bar in bars if bar.usable]
    if len(usable) < days + 1 or not usable[-1].close:
        return None
    true_ranges = []
    for previous, current in zip(usable[-days - 1:-1], usable[-days:]):
        if None in (previous.close, current.high, current.low):
            return None
        true_ranges.append(max(current.high - current.low,
                               abs(current.high - previous.close),
                               abs(current.low - previous.close)))
    return round(mean(true_ranges) / usable[-1].close * 100, 6)


def _field_map(record: SecurityMasterRecord, quote: MarketQuote, bars: list[DailyBar],
               fundamentals: dict[str, FieldValue] | None, as_of: str) -> dict[str, FieldValue]:
    source = quote.source or "market_provider"
    prices = _values(bars)
    raw: dict[str, Any] = {
        "current_price": quote.current,
        "market_cap_usd": quote.market_cap_usd,
        "sector": _known(record.sector_canonical, "security_master", as_of) if record.sector_canonical != "UNKNOWN" else _unknown(),
        "return_1d_pct": _return(prices, 1), "return_5d_pct": _return(prices, 5),
        "return_20d_pct": _return(prices, 20), "return_60d_pct": _return(prices, 60),
        "ma20": _ma(prices, 20), "ma50": _ma(prices, 50), "ma200": _ma(prices, 200),
        "atr_pct": _atr_pct(bars), "bar_count": len(prices),
        "volume_completed": bars[-1].volume if bars and bars[-1].usable else None,
        "adv20_usd": None, "relative_volume_completed_bar": None,
        "range_contraction_20d": None, "distance_20d_high_pct": None,
        "distance_60d_high_pct": None,
    }
    fields: dict[str, FieldValue] = {}
    for name, item in raw.items():
        if isinstance(item, FieldValue):
            fields[name] = item
        elif item is None:
            fields[name] = _unknown()
        else:
            fields[name] = _known(item, source, as_of)
    usable = [bar for bar in bars if bar.usable]
    if len(usable) >= 20:
        volumes = [bar.volume for bar in usable[-20:] if bar.volume is not None]
        closes = [float(bar.adjusted_close if bar.adjusted_close is not None else bar.close) for bar in usable[-20:]]
        if len(volumes) == 20 and len(closes) == 20:
            fields["adv20_usd"] = _known(mean(v * c for v, c in zip(volumes, closes)), source, as_of)
            fields["relative_volume_completed_bar"] = _known(volumes[-1] / max(mean(volumes), 1), source, as_of)
            ranges = [(bar.high - bar.low) / max(bar.close, 0.0001) for bar in usable[-20:]
                      if bar.high is not None and bar.low is not None and bar.close]
            if len(ranges) == 20:
                fields["range_contraction_20d"] = _known(mean(ranges[-5:]) < mean(ranges[:10]), source, as_of)
    if prices:
        current = prices[-1]
        for days, key in ((20, "distance_20d_high_pct"), (60, "distance_60d_high_pct")):
            if len(prices) >= days:
                fields[key] = _known(round((current / max(max(prices[-days:]), 0.0001) - 1) * 100, 6), source, as_of)
    if fundamentals:
        fields.update(fundamentals)
        cap = fields.get("market_cap_usd")
        shares = fields.get("shares_outstanding")
        current = fields.get("current_price")
        if (cap is not None and not cap.known and shares is not None and shares.known and
                current is not None and current.known and float(shares.value) > 0):
            fields["market_cap_usd"] = _known(
                float(shares.value) * float(current.value),
                "DERIVED_VERIFIED_SHARES", as_of,
                tuple(shares.source_ids))
    return fields


def build_candidate(record: SecurityMasterRecord, quote: MarketQuote, bars: list[DailyBar],
                    run_id: str, as_of: str,
                    fundamentals: dict[str, FieldValue] | None = None) -> CandidateFeatureSnapshot:
    fields = _field_map(record, quote, bars, fundamentals, as_of)
    unknown_fields = sorted(name for name, field in fields.items() if not field.known)
    try:
        expires = (datetime.fromisoformat(as_of.replace("Z", "+00:00")) + timedelta(days=1)).isoformat()
    except ValueError:
        expires = ""
    return CandidateFeatureSnapshot(security=record, as_of=as_of, discovery_run_id=run_id,
                                    feature_version=FEATURE_VERSION, fields=fields,
                                    unknown_fields=unknown_fields, created_at=as_of,
                                    last_validated_at=as_of, first_seen_at=as_of,
                                    last_seen_at=as_of, expires_at=expires)


def value(candidate: CandidateFeatureSnapshot, name: str, default=None):
    field = candidate.fields.get(name)
    return field.value if field and field.known else default


def known_field(candidate: CandidateFeatureSnapshot, name: str) -> bool:
    field = candidate.fields.get(name)
    return bool(field and field.known)
