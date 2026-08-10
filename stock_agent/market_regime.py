from __future__ import annotations

from .schemas import MarketRegime, MarketSnapshot
from .analysis_context import MarketRegimeContext
from .schemas import now_iso


class MarketRegimeEngine:
    def evaluate(self, snapshots: dict[str, MarketSnapshot]) -> MarketRegime:
        required = ("QQQ", "IWM", "SOXX")
        if any(t not in snapshots for t in required):
            return MarketRegime.UNKNOWN
        score = sum(1 if snapshots[t].current >= snapshots[t].ma20 else -1 for t in required)
        score += sum(1 if snapshots[t].return_20d_pct > 0 else -1 for t in required)
        if score >= 4:
            return MarketRegime.RISK_ON
        if score <= -4:
            return MarketRegime.RISK_OFF
        return MarketRegime.NEUTRAL

    def context(self, snapshots: dict[str, MarketSnapshot],
                ticker_snapshot: MarketSnapshot | None = None) -> MarketRegimeContext:
        regime = self.evaluate(snapshots)
        benchmark_returns = {
            ticker: snapshots[ticker].return_20d_pct if ticker in snapshots else None
            for ticker in ("QQQ", "IWM", "SOXX")
        }
        ticker_return = ticker_snapshot.return_20d_pct if ticker_snapshot else None
        relative = {
            ticker: (round(ticker_return - value, 4)
                     if ticker_return is not None and value is not None else None)
            for ticker, value in benchmark_returns.items()
        }
        available = sum(value is not None for value in benchmark_returns.values())
        confidence = round(available / 3 * 100)
        as_of = max((snap.observed_at for snap in snapshots.values()), default=now_iso())
        return MarketRegimeContext(regime.value, as_of, benchmark_returns, relative,
                                   "UNKNOWN", confidence)
