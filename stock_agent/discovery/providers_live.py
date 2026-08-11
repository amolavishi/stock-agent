from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..toss import TossClient, TossAPIError
from .ingestion import unknown
from .schemas import DailyBar, FieldValue, MarketQuote, SecurityMasterRecord, UnknownState


class SECCompanyTickerSecurityMasterProvider:
    """Fetches the SEC directory once and preserves unverified identity as UNKNOWN.

    The SEC directory is an identity source, not a complete exchange/security-type
    master.  Unless a validated exchange/type enrichment is supplied, records are
    intentionally returned with unknown common-stock flags and are fail-closed by
    UniverseIntegrityEngine.
    """

    URL = "https://www.sec.gov/files/company_tickers_exchange.json"

    def __init__(self, user_agent: str, cache_path: str | Path, opener=None):
        if not user_agent:
            raise ValueError("SEC user agent is required")
        self.user_agent = user_agent
        self.cache_path = Path(cache_path)
        self.opener = opener or urllib.request.urlopen
        self.calls = 0

    def records(self, as_of: str) -> list[SecurityMasterRecord]:
        payload = self._load()
        fields = payload.get("fields", [])
        rows = payload.get("data", [])
        indexes = {name: index for index, name in enumerate(fields)}
        records = []
        for row in rows:
            def column(name: str):
                index = indexes.get(name)
                return row[index] if index is not None and index < len(row) else ""
            ticker = str(column("ticker") or "").upper()
            if not ticker:
                continue
            records.append(SecurityMasterRecord(
                security_id=f"SEC-{column('cik')}-{ticker}", ticker=ticker,
                company_name=str(column("name") or ""), cik=str(column("cik") or ""),
                exchange=str(column("exchange") or ""),
                is_common_stock=None, is_etf=None, is_unit=None, is_warrant=None,
                is_preferred=None, is_adr=None, active_status="ACTIVE", source="SEC_DIRECTORY",
                source_as_of=as_of, ingested_at=datetime.now(timezone.utc).isoformat()))
        return records

    def _load(self) -> dict:
        self.calls += 1
        if self.cache_path.is_file():
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        request = urllib.request.Request(self.URL, headers={"User-Agent": self.user_agent})
        with self.opener(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload


class TossDiscoveryMarketDataProvider:
    """Batch quote + completed daily bar adapter with conservative timestamps."""

    def __init__(self, client: TossClient):
        self.client = client
        self.quote_batches = 0
        self.bar_calls = 0

    @staticmethod
    def _number(row: dict, *names: str):
        for name in names:
            if row.get(name) is not None:
                return float(row[name])
        return None

    def batch_quotes(self, tickers: list[str], as_of: str) -> dict[str, MarketQuote]:
        self.quote_batches += 1
        received_at = datetime.now(timezone.utc).isoformat()
        rows = self.client.prices(tickers)
        output = {}
        for row in rows:
            ticker = str(row.get("symbol") or row.get("ticker") or "").upper()
            current = self._number(row, "lastPrice", "price")
            if not ticker or current is None:
                continue
            observed = str(row.get("timestamp") or received_at)
            cap = self._number(row, "marketCap", "market_cap_usd")
            output[ticker] = MarketQuote(
                ticker=ticker,
                current=FieldValue(current, UnknownState.KNOWN.value, "TOSS_OPEN_API", observed, ingested_at=received_at),
                market_cap_usd=FieldValue(cap, UnknownState.KNOWN.value if cap is not None else UnknownState.UNKNOWN_NOT_AVAILABLE.value,
                                          "TOSS_OPEN_API" if cap is not None else "", observed, ingested_at=received_at),
                observed_at=observed, source="TOSS_OPEN_API", market_session="UNKNOWN")
        return output

    def daily_bars(self, ticker: str, completed_bar_cutoff: str) -> list[DailyBar]:
        self.bar_calls += 1
        rows = self.client.candles(ticker, 200)
        cutoff = completed_bar_cutoff[:10]
        output = []
        for row in rows:
            raw_timestamp = str(row.get("timestamp") or row.get("date") or "")
            session_date = raw_timestamp[:10]
            if not session_date or session_date >= cutoff:
                continue
            close = self._number(row, "closePrice", "close")
            high = self._number(row, "highPrice", "high")
            low = self._number(row, "lowPrice", "low")
            volume = self._number(row, "volume")
            quality = "COMPLETE" if close is not None and high is not None and low is not None and volume is not None else "UNKNOWN"
            output.append(DailyBar(ticker=ticker, session_date=session_date,
                open=self._number(row, "openPrice", "open"), high=high, low=low,
                close=close, adjusted_close=self._number(row, "adjustedClose", "adjusted_close", "closePrice", "close"),
                volume=int(volume) if volume is not None else None, source="TOSS_OPEN_API",
                observed_at=raw_timestamp, ingested_at=datetime.now(timezone.utc).isoformat(),
                quality_status=quality))
        return sorted(output, key=lambda item: item.session_date)
