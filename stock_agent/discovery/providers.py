from __future__ import annotations

from typing import Protocol

from .schemas import FieldValue, MarketQuote, SecurityMasterRecord, DailyBar


class SecurityMasterProvider(Protocol):
    def records(self, as_of: str) -> list[SecurityMasterRecord]: ...


class MarketDataProvider(Protocol):
    def batch_quotes(self, tickers: list[str], as_of: str) -> dict[str, MarketQuote]: ...
    def daily_bars(self, ticker: str, completed_bar_cutoff: str) -> list[DailyBar]: ...


class FundamentalProvider(Protocol):
    def fundamentals(self, tickers: list[str], as_of: str) -> dict[str, dict[str, FieldValue]]: ...


class CatalystProvider(Protocol):
    def events(self, tickers: list[str], as_of: str) -> dict[str, list[dict]]: ...


class ConsensusProvider(Protocol):
    def estimates(self, tickers: list[str], as_of: str) -> dict[str, dict[str, FieldValue]]: ...


class ShortInterestProvider(Protocol):
    def short_interest(self, tickers: list[str], as_of: str) -> dict[str, FieldValue]: ...
