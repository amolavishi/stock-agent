from __future__ import annotations

from datetime import datetime, timezone

from .schemas import CompanyState, MarketSnapshot
from .validation import UnsupportedMockTickerError, validate_ticker


class MockMarketDataProvider:
    """Deterministic fixtures. Unknown tickers fail instead of receiving fabricated data."""

    FIXTURES = {
        "IONQ": dict(price=41.25, change=-3.41, r5=-8.22, r20=14.71, volume=15_820_000,
                     avg=9_200_000, cap=8_900_000_000, ma20=39.81, ma50=35.44, atr=2.84,
                     growth=95.0, margin=52.0, sector="Quantum Computing", stage="2", atm=False,
                     dilution=54, catalysts=["정부·기업 양자컴퓨팅 수요 확대", "상용화 로드맵 진전"],
                     risks=["높은 기대치", "높은 변동성", "상용화 시점 불확실성"]),
        "SOUN": dict(price=8.40, change=2.10, r5=7.6, r20=21.2, volume=28_000_000,
                     avg=18_500_000, cap=2_100_000_000, ma20=7.75, ma50=6.90, atr=0.86,
                     growth=45.0, margin=45.1, sector="AI Software", stage="3", atm=True,
                     dilution=78, catalysts=["자동차 고객 확대", "매출 성장 지속"],
                     risks=["ATM 희석", "현금 소진", "인수 관련 주식 발행"]),
    }

    def _row(self, ticker: str) -> tuple[str, dict]:
        ticker = validate_ticker(ticker)
        if ticker not in self.FIXTURES:
            raise UnsupportedMockTickerError(f"unsupported mock ticker: {ticker}")
        return ticker, self.FIXTURES[ticker]

    def snapshot(self, ticker: str) -> MarketSnapshot:
        ticker, row = self._row(ticker)
        observed = datetime.now(timezone.utc).isoformat()
        return MarketSnapshot(ticker=ticker, timestamp=observed, current=row["price"],
            change_1d_pct=row["change"], return_5d_pct=row["r5"], return_20d_pct=row["r20"],
            volume=row["volume"], avg_20d_volume=row["avg"], market_cap_usd=row["cap"],
            ma20=row["ma20"], ma50=row["ma50"], atr_14=row["atr"], sector_name=row["sector"],
            stage=row["stage"], source="MOCK_MARKET", data_quality="OK", observed_at=observed,
            is_mock=True)

    def company_state(self, ticker: str) -> CompanyState:
        ticker, row = self._row(ticker)
        return CompanyState(ticker=ticker, last_updated=datetime.now(timezone.utc).date().isoformat(),
            revenue_growth=row["growth"], gross_margin=row["margin"], market_cap_usd=row["cap"],
            atm_active=row["atm"], dilution_risk=row["dilution"], catalysts=row["catalysts"],
            known_risks=row["risks"])

