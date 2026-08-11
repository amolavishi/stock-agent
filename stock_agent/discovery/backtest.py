from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .schemas import DailyBar


class PITUniverseProvider(Protocol):
    def records(self, as_of: str) -> Iterable: ...


class PITBarProvider(Protocol):
    def daily_bars(self, ticker: str, completed_bar_cutoff: str) -> list[DailyBar]: ...


@dataclass(frozen=True)
class BacktestResult:
    ticker: str
    as_of: str
    horizon_days: int
    forward_return: float | None
    benchmark_excess_return: float | None
    mfe: float | None
    mae: float | None
    hit_10: bool | None
    hit_20: bool | None
    hit_30: bool | None
    drawdown_10: bool | None
    reason: str = ""


class PITBacktester:
    HORIZONS = (5, 10, 20, 40)

    def __init__(self, universe_provider: PITUniverseProvider, bar_provider: PITBarProvider,
                 historical_membership_available: bool = False):
        self.universe_provider = universe_provider
        self.bar_provider = bar_provider
        self.historical_membership_available = historical_membership_available

    def run(self, as_of: str, tickers: list[str], benchmark_returns: dict[int, float] | None = None) -> dict:
        benchmark_returns = benchmark_returns or {}
        results: list[BacktestResult] = []
        for ticker in tickers:
            bars = [bar for bar in self.bar_provider.daily_bars(ticker, as_of) if bar.session_date <= as_of[:10]]
            if not bars:
                for horizon in self.HORIZONS:
                    results.append(BacktestResult(ticker, as_of, horizon, None, None, None, None, None, None, None, None, "NO_PIT_BAR"))
                continue
            entry = bars[-1].adjusted_close or bars[-1].close
            future = [bar for bar in self.bar_provider.daily_bars(ticker, "9999-12-31") if bar.session_date > as_of[:10]]
            for horizon in self.HORIZONS:
                window = future[:horizon]
                if entry is None or len(window) < horizon:
                    results.append(BacktestResult(ticker, as_of, horizon, None, None, None, None, None, None, None, None, "INSUFFICIENT_FORWARD_DATA"))
                    continue
                prices = [(bar.adjusted_close or bar.close) for bar in window]
                returns = [(price / entry - 1) * 100 for price in prices if price is not None]
                forward = returns[-1]
                results.append(BacktestResult(ticker, as_of, horizon, round(forward, 6),
                    round(forward - benchmark_returns.get(horizon, 0), 6), round(max(returns), 6),
                    round(min(returns), 6), max(returns) >= 10, max(returns) >= 20,
                    max(returns) >= 30, min(returns) <= -10))
        return {"as_of": as_of, "results": results,
                "survivorship_bias_risk": "NONE" if self.historical_membership_available else "SURVIVORSHIP_BIAS_RISK"}
