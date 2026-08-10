from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .schemas import CompanyState, CriticReview, MarketSnapshot, ResearchAnalysis, RiskResult, TradePlan


def _age_days(iso_value: str, now: datetime | None = None) -> float:
    observed = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - observed.astimezone(timezone.utc)).total_seconds() / 86400


class RiskEngine:
    def __init__(self, rules: dict[str, Any]):
        self.rules = rules

    def evaluate(self, research: ResearchAnalysis, critic: CriticReview, state: CompanyState,
                 market: MarketSnapshot, trade_plan: TradePlan, now: datetime | None = None) -> RiskResult:
        failures: list[str] = []
        warnings: list[str] = []
        if market.current < self.rules["minimum_price_usd"]:
            failures.append("주가가 최소 기준 미만")
        if state.market_cap_usd < self.rules["minimum_market_cap_usd"]:
            failures.append("시가총액이 최소 기준 미만")
        if market.avg_20d_volume * market.current < self.rules["minimum_avg_volume_usd"]:
            failures.append("평균 거래대금이 최소 기준 미만")
        if market.data_quality not in {"OK", "PARTIAL"}:
            failures.append(f"시장 데이터 품질 불충분: {market.data_quality}")
        if _age_days(market.observed_at, now) > self.rules["max_data_age_days"]:
            failures.append("시장 데이터가 STALE 상태")
        if market.stage in {"3", "STAGE_3"}:
            warnings.append("Stage 3 추격 위험")
        elif market.stage == "UNKNOWN":
            warnings.append("Stage를 판정하지 못함")
        if state.atm_active or state.dilution_risk >= 70:
            warnings.append("자본구조·희석 리스크가 높음")
        if market.atr_pct >= self.rules["high_volatility_atr_pct"]:
            warnings.append("ATR 기준 변동성이 높음")
        if critic.verdict == "CHALLENGE":
            warnings.append("Critic이 Research 결론에 중대한 이의를 제기함")
        if trade_plan.reward_risk < self.rules["minimum_reward_risk"]:
            warnings.append(f"Reward/Risk {trade_plan.reward_risk}가 기준 미만")
        if failures:
            decision = "FAIL"
        elif market.stage in {"3", "STAGE_3"}:
            decision = self.rules["stage_3_action"]
        elif market.stage == "UNKNOWN" or critic.verdict == "CHALLENGE" or trade_plan.reward_risk < self.rules["minimum_reward_risk"]:
            decision = "WAIT"
        else:
            decision = "PASS"
        return RiskResult(ticker=market.ticker, hard_filter_pass=not failures,
            warnings=warnings, failures=failures, trade_plan=trade_plan, risk_decision=decision)
