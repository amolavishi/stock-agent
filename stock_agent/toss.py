from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .schemas import CompanyState, MarketSnapshot
from .market_quality import assess_market_quality
from .validation import validate_ticker
from .stage import StageDetector


class TossAPIError(RuntimeError):
    pass


class TossClient:
    BASE_URL = "https://openapi.tossinvest.com"

    def __init__(self, client_id: str, client_secret: str, timeout: float = 20,
                 base_url: str | None = None, opener=None):
        if not client_id or not client_secret:
            raise TossAPIError("Toss credentials are missing")
        self.client_id, self.client_secret = client_id, client_secret
        self.timeout = timeout
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.opener = opener or urllib.request.urlopen
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _request(self, request: urllib.request.Request) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(3):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code != 429 and exc.code < 500:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
        code = getattr(last, "code", "NETWORK")
        raise TossAPIError(f"Toss request failed ({code})") from last

    def access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token
            body = urllib.parse.urlencode({
                "grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret,
            }).encode()
            request = urllib.request.Request(f"{self.base_url}/oauth2/token", data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            payload = self._request(request)
            token = payload.get("access_token")
            if not token:
                raise TossAPIError("Toss token response has no access_token")
            self._token = str(token)
            self._expires_at = time.time() + int(payload.get("expires_in", 3600))
            return self._token

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.access_token()}"})
        return self._request(request)

    def prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        payload = self.get("/api/v1/prices", {"symbols": ",".join(map(validate_ticker, symbols))})
        return list(payload.get("result", payload if isinstance(payload, list) else []))

    def candles(self, symbol: str, count: int = 80) -> list[dict[str, Any]]:
        payload = self.get("/api/v1/candles", {
            "symbol": validate_ticker(symbol), "interval": "1d", "count": min(count, 200), "adjusted": "true",
        })
        result = payload.get("result", payload)
        return list(result.get("candles", [])) if isinstance(result, dict) else []


class TossMarketDataProvider:
    def __init__(self, client: TossClient):
        self.client = client

    @staticmethod
    def _number(row: dict, *names: str) -> float:
        for name in names:
            if row.get(name) is not None:
                return float(row[name])
        raise TossAPIError(f"missing numeric field: {names[0]}")

    def snapshot(self, ticker: str) -> MarketSnapshot:
        ticker = validate_ticker(ticker)
        prices = self.client.prices([ticker])
        candles = self.client.candles(ticker, 80)
        if not prices or len(candles) < 50:
            raise TossAPIError("insufficient Toss price/candle data")
        api_received = datetime.now(timezone.utc).isoformat()
        rows = sorted(candles, key=lambda x: str(x.get("timestamp", "")))
        observed = str(prices[0].get("timestamp") or api_received)
        latest_bar_end = str(rows[-1].get("timestamp") or "")
        initial_quality = assess_market_quality(
            api_received_at=api_received, provider_observed_at=observed,
            bar_end_at=latest_bar_end,
            volume=int(self._number(rows[-1], "volume")),
            average_volume=max(1, round(sum(
                int(self._number(item, "volume")) for item in rows[-21:-1]) / 20)),
            transport_status="OK")
        metric_rows = rows if initial_quality.bar_completeness == "COMPLETE" else rows[:-1]
        if len(metric_rows) < 50:
            raise TossAPIError("insufficient completed Toss candle data")
        rows = metric_rows
        closes = [self._number(x, "closePrice", "close") for x in rows]
        highs = [self._number(x, "highPrice", "high") for x in rows]
        lows = [self._number(x, "lowPrice", "low") for x in rows]
        volumes = [int(self._number(x, "volume")) for x in rows]
        current = self._number(prices[0], "lastPrice", "price")
        def ret(days: int) -> float:
            base = closes[-(days + 1)]
            return round((current / base - 1) * 100, 4)
        true_ranges = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                       for i in range(1, len(rows))]
        ma20 = round(sum(closes[-20:]) / 20, 4)
        ma50 = round(sum(closes[-50:]) / 50, 4)
        atr14 = round(sum(true_ranges[-14:]) / 14, 4)
        r20 = ret(20)
        stage = (StageDetector().detect(current, ma20, ma50, r20, atr14 / current * 100)
                 if initial_quality.indicator_readiness == "READY" else "UNCERTIFIED")
        completed_bar_end = str(rows[-1].get("timestamp") or latest_bar_end)
        average_volume = round(sum(volumes[-21:-1]) / 20) if len(volumes) >= 21 else round(
            sum(volumes[-20:]) / 20)
        return MarketSnapshot(ticker=ticker, timestamp=observed, current=current,
            change_1d_pct=ret(1), return_5d_pct=ret(5), return_20d_pct=r20,
            volume=volumes[-1], avg_20d_volume=average_volume, market_cap_usd=0,
            ma20=ma20, ma50=ma50, atr_14=atr14, source="TOSS_OPEN_API",
            stage=stage, data_quality=initial_quality.data_quality, observed_at=observed,
            candle_as_of=completed_bar_end, is_mock=False,
            transport_status=initial_quality.transport_status,
            quote_freshness=initial_quality.quote_freshness,
            candle_freshness=initial_quality.candle_freshness,
            market_session=initial_quality.market_session,
            bar_completeness=initial_quality.bar_completeness,
            volume_validity=initial_quality.volume_validity,
            indicator_readiness=initial_quality.indicator_readiness,
            api_received_at=api_received, provider_observed_at=observed,
            bar_end_at=latest_bar_end, market_session_date=completed_bar_end[:10],
            relative_volume_certified=initial_quality.volume_validity == "VALID")

    def company_state(self, ticker: str) -> CompanyState:
        ticker = validate_ticker(ticker)
        return CompanyState(ticker=ticker, last_updated=datetime.now(timezone.utc).date().isoformat(),
            revenue_growth=0, gross_margin=0, market_cap_usd=0, atm_active=False,
            dilution_risk=0, catalysts=[], known_risks=["SEC CompanyFacts 통합 전 미확인"])
