from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Protocol

from .schemas import DailyBar, FieldValue, MarketQuote, UnknownState


class DiscoveryMarketDataProvider(Protocol):
    def batch_quotes(self, tickers: list[str], as_of: str) -> dict[str, MarketQuote]: ...
    def daily_bars(self, ticker: str, completed_bar_cutoff: str) -> list[DailyBar]: ...


class DiscoveryFundamentalProvider(Protocol):
    def fundamentals(self, tickers: list[str], as_of: str) -> dict[str, dict[str, FieldValue]]: ...


class EmptyDiscoveryMarketDataProvider:
    def batch_quotes(self, tickers: list[str], as_of: str) -> dict[str, MarketQuote]:
        return {}

    def daily_bars(self, ticker: str, completed_bar_cutoff: str) -> list[DailyBar]:
        return []


class InMemoryDiscoveryMarketDataProvider:
    def __init__(self, quotes: Iterable[MarketQuote], bars: Iterable[DailyBar]):
        self.quotes = {quote.ticker: quote for quote in quotes}
        self.bars = defaultdict(list)
        for bar in bars:
            self.bars[bar.ticker].append(bar)
        for ticker in self.bars:
            self.bars[ticker].sort(key=lambda bar: bar.session_date)
        self.quote_calls: list[tuple[str, ...]] = []
        self.bar_calls: list[tuple[str, str]] = []

    def batch_quotes(self, tickers: list[str], as_of: str) -> dict[str, MarketQuote]:
        self.quote_calls.append(tuple(tickers))
        return {ticker: self.quotes[ticker] for ticker in tickers if ticker in self.quotes
                and self.quotes[ticker].observed_at <= as_of}

    def daily_bars(self, ticker: str, completed_bar_cutoff: str) -> list[DailyBar]:
        self.bar_calls.append((ticker, completed_bar_cutoff))
        return [bar for bar in self.bars.get(ticker, []) if bar.session_date <= completed_bar_cutoff[:10]]


def known(value, source: str, observed_at: str, source_ids: tuple[str, ...] = ()) -> FieldValue:
    return FieldValue(value=value, state=UnknownState.KNOWN.value, source=source,
                      observed_at=observed_at, ingested_at=datetime.now(timezone.utc).isoformat(),
                      calculation_version="discovery_ingestion_v1", source_ids=source_ids)


def unknown(state: str = UnknownState.UNKNOWN_NOT_FETCHED.value, source: str = "") -> FieldValue:
    return FieldValue(value=None, state=state, source=source,
                      ingested_at=datetime.now(timezone.utc).isoformat(),
                      calculation_version="discovery_ingestion_v1")
