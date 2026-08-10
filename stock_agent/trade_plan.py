from __future__ import annotations

from .schemas import MarketSnapshot, TradePlan


def build_heuristic_trade_plan(market: MarketSnapshot) -> TradePlan:
    entry = market.current
    # MA50 can sit above the current price after a sharp decline. Cap the
    # technical stop below entry with an ATR/5% buffer so sizing risk is valid.
    atr_buffer = max(market.atr_14, entry * 0.05)
    fallback_stop = entry - atr_buffer
    technical_stop = market.ma50 * 0.92 if market.ma50 > 0 else fallback_stop
    stop = round(max(0.01, min(technical_stop, fallback_stop)), 2)
    target_1 = round(market.current * 1.16, 2)
    target_2 = round(market.current * 1.32, 2)
    expected_reward = round(max(0.0, target_1 - entry), 2)
    expected_risk = round(max(0.01, entry - stop), 2)
    return TradePlan(
        entry_price=entry,
        preferred_price_min=round(market.ma20 * 0.96, 2),
        preferred_price_max=round(market.ma20 * 1.01, 2),
        stop_price=stop,
        target_1=target_1,
        target_2=target_2,
        expected_reward=expected_reward,
        expected_risk=expected_risk,
        reward_risk=round(expected_reward / expected_risk, 2),
        heuristic=True,
    )
