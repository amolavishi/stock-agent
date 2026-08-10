from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class MarketQualityAssessment:
    transport_status: str
    quote_freshness: str
    candle_freshness: str
    market_session: str
    bar_completeness: str
    volume_validity: str
    indicator_readiness: str
    data_quality: str

    @property
    def certifiable(self) -> bool:
        return self.data_quality == "OK"


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def assess_market_quality(*, api_received_at: str, provider_observed_at: str,
                          bar_end_at: str, volume: int, average_volume: int,
                          transport_status: str = "OK") -> MarketQualityAssessment:
    received = _parse(api_received_at)
    observed = _parse(provider_observed_at)
    bar_end = _parse(bar_end_at)
    if transport_status != "OK" or not received or not observed:
        return MarketQualityAssessment(transport_status, "UNKNOWN", "UNKNOWN", "UNKNOWN",
                                       "UNKNOWN", "UNVERIFIED", "UNCERTIFIED", "LOW")

    eastern = ZoneInfo("America/New_York")
    local_observed = observed.astimezone(eastern)
    weekday = local_observed.weekday() < 5
    minute = local_observed.hour * 60 + local_observed.minute
    session = "OPEN" if weekday and 570 <= minute < 960 else "CLOSED"
    quote_age = max(0.0, (received - observed).total_seconds())
    quote_limit = 20 * 60 if session == "OPEN" else 4 * 24 * 3600
    quote_freshness = "FRESH" if quote_age <= quote_limit else "STALE"

    if not bar_end:
        bar_complete, candle_freshness = "UNKNOWN", "UNKNOWN"
    else:
        local_bar = bar_end.astimezone(eastern)
        bar_weekday = local_bar.weekday() < 5
        if not bar_weekday or local_bar.date() > local_observed.date():
            bar_complete = "INCOMPLETE"
        elif local_bar.date() < local_observed.date():
            bar_complete = "COMPLETE"
        else:
            bar_complete = "COMPLETE" if session == "CLOSED" else "INCOMPLETE"
        candle_age_days = (local_observed.date() - local_bar.date()).days
        candle_freshness = "FRESH" if 0 <= candle_age_days <= 4 else "STALE"

    volume_validity = ("VALID" if bar_complete == "COMPLETE" and volume >= 0
                       and average_volume > 0 else "INVALID")
    indicator_readiness = ("READY" if bar_complete == "COMPLETE"
                           and candle_freshness == "FRESH" else "UNCERTIFIED")
    certifiable = all((transport_status == "OK", quote_freshness == "FRESH",
                       candle_freshness == "FRESH", bar_complete == "COMPLETE",
                       volume_validity == "VALID", indicator_readiness == "READY"))
    return MarketQualityAssessment(transport_status, quote_freshness, candle_freshness,
        session, bar_complete, volume_validity, indicator_readiness,
        "OK" if certifiable else "LOW")
