from __future__ import annotations

import re


TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class InvalidTickerError(ValueError):
    pass


class UnsupportedMockTickerError(ValueError):
    pass


class AnalysisIncompleteError(RuntimeError):
    pass


def validate_ticker(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidTickerError("ticker must be a string")
    ticker = value.upper()
    if not TICKER_PATTERN.fullmatch(ticker) or ".." in ticker:
        raise InvalidTickerError(f"invalid ticker: {value!r}")
    return ticker

