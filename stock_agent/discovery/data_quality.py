from __future__ import annotations

from dataclasses import dataclass

from .schemas import CoverageMetrics


@dataclass(frozen=True)
class DiscoveryCertification:
    status: str
    reason_codes: tuple[str, ...]
    coverage: CoverageMetrics


def certify_input(coverage: CoverageMetrics, market_min_pct: float = 95.0,
                  feature_min_pct: float = 90.0, required_bar_count: int = 20) -> DiscoveryCertification:
    reasons: list[str] = []
    if coverage.eligible_universe_count == 0:
        reasons.append("BLOCKED_UNIVERSE")
    if coverage.market_coverage_pct < market_min_pct:
        reasons.append("BLOCKED_COVERAGE_MARKET")
    if coverage.feature_coverage_pct < feature_min_pct:
        reasons.append("BLOCKED_COVERAGE_FEATURE")
    status = "READY" if not reasons else ("BLOCKED_COVERAGE" if any("COVERAGE" in reason for reason in reasons) else "BLOCKED_UNIVERSE")
    return DiscoveryCertification(status, tuple(reasons), coverage)


def coverage_metrics(total: int, market: int, feature: int, sector: int, fundamental: int) -> CoverageMetrics:
    def pct(count: int) -> float:
        return round(count / total * 100, 4) if total else 0.0
    return CoverageMetrics(total, market, feature, sector, fundamental,
                           pct(market), pct(feature), pct(sector), pct(fundamental))
