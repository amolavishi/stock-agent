from __future__ import annotations

from statistics import median

from .features import known_field, value
from .schemas import CandidateFeatureSnapshot


class DiscoveryMarketRegimeEngine:
    REQUIRED_BENCHMARKS = ("SPY", "QQQ", "IWM")

    def evaluate(self, candidates: list[CandidateFeatureSnapshot], benchmark_tickers: set[str] | None = None) -> dict:
        benchmark_tickers = benchmark_tickers or set()
        benchmarks = [item for item in candidates if item.security.ticker in benchmark_tickers]
        benchmark_returns = [value(item, "return_20d_pct") for item in benchmarks if known_field(item, "return_20d_pct")]
        returns = [value(item, "return_20d_pct") for item in candidates if known_field(item, "return_20d_pct")]
        if not benchmark_returns or not returns:
            return {"regime": "UNKNOWN", "confidence": 0, "reasons": ["BENCHMARK_OR_BREADTH_UNKNOWN"],
                    "breadth": {"eligible_count": len(candidates)}}
        positive = sum(item > 0 for item in returns) / len(returns) * 100
        benchmark_median = median(benchmark_returns)
        if benchmark_median > 5 and positive >= 60:
            regime = "BROAD_RISK_ON"
        elif benchmark_median > 0 and positive >= 35:
            regime = "SELECTIVE_RISK_ON"
        elif benchmark_median < -10 and positive <= 25:
            regime = "RISK_OFF"
        else:
            regime = "NEUTRAL"
        return {"regime": regime, "confidence": round(min(100, len(benchmark_returns) / 3 * 60 + 40)),
                "reasons": [], "breadth": {"eligible_count": len(candidates),
                                             "pct_positive_20d": round(positive, 4),
                                             "median_return_20d": round(median(returns), 4)}}


class RegimeHysteresis:
    """Require confirmation for ordinary transitions; PANIC remains immediate."""

    def __init__(self, confirmation_required: int = 2):
        self.confirmation_required = max(1, confirmation_required)

    def apply(self, raw_regime: str, previous_certified: str | None,
              confirmation_count: int = 0, transition_strength: float = 0.0) -> dict:
        if raw_regime == "PANIC":
            return {"certified_regime": "PANIC", "confirmation_count": confirmation_count + 1,
                    "transition_strength": transition_strength, "changed": previous_certified != "PANIC"}
        if not previous_certified or previous_certified == raw_regime:
            return {"certified_regime": raw_regime, "confirmation_count": 0,
                    "transition_strength": transition_strength, "changed": previous_certified != raw_regime}
        next_count = confirmation_count + 1
        if next_count >= self.confirmation_required:
            return {"certified_regime": raw_regime, "confirmation_count": 0,
                    "transition_strength": transition_strength, "changed": True}
        return {"certified_regime": previous_certified, "confirmation_count": next_count,
                "transition_strength": transition_strength, "changed": False}
