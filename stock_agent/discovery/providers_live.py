from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..evidence import LiveEdgarEvidenceCollector
from ..capital_structure import build_capital_structure
from ..sec import SECCompanyFactsProvider
from ..toss import TossClient, TossAPIError
from .ingestion import known, unknown
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


class ValidatedSecurityMasterProvider:
    """Compose SEC listing identity with an explicit validated enrichment file.

    SEC's ticker directory is not a security-type master.  This provider never
    guesses common stock/ETF/warrant/ADR flags.  A row is executable-universe
    eligible only when the enrichment record supplies every identity flag and a
    canonical sector.  Missing rows remain UNKNOWN and are counted in health.
    """

    REQUIRED_FLAGS = ("is_common_stock", "is_etf", "is_unit", "is_warrant", "is_preferred", "is_adr")

    def __init__(self, listing_provider: SECCompanyTickerSecurityMasterProvider,
                 enrichment_path: str | Path | None = None):
        self.listing_provider = listing_provider
        self.enrichment_path = Path(enrichment_path) if enrichment_path else None
        self.calls = 0

    def _enrichment(self) -> dict[str, dict[str, Any]]:
        if not self.enrichment_path or not self.enrichment_path.is_file():
            return {}
        payload = json.loads(self.enrichment_path.read_text(encoding="utf-8"))
        rows = payload.get("records", payload) if isinstance(payload, dict) else payload
        if isinstance(rows, list):
            return {str(row.get("ticker", "")).upper(): row for row in rows if row.get("ticker")}
        return {str(key).upper(): value for key, value in rows.items() if isinstance(value, dict)}

    def records(self, as_of: str) -> list[SecurityMasterRecord]:
        self.calls += 1
        enrichment = self._enrichment()
        result: list[SecurityMasterRecord] = []
        for base in self.listing_provider.records(as_of):
            extra = enrichment.get(base.ticker, {})
            flags = {name: extra.get(name) for name in self.REQUIRED_FLAGS}
            sector = str(extra.get("sector_canonical") or "UNKNOWN")
            result.append(SecurityMasterRecord(
                security_id=base.security_id, ticker=base.ticker,
                company_name=str(extra.get("company_name") or base.company_name),
                cik=str(extra.get("cik") or base.cik), exchange=base.exchange,
                security_type=str(extra.get("security_type") or "COMMON_STOCK"),
                country=str(extra.get("country") or base.country),
                is_adr=flags["is_adr"], is_etf=flags["is_etf"], is_unit=flags["is_unit"],
                is_warrant=flags["is_warrant"], is_preferred=flags["is_preferred"],
                is_common_stock=flags["is_common_stock"],
                sector_canonical=sector,
                industry_canonical=str(extra.get("industry_canonical") or "UNKNOWN"),
                sic=str(extra.get("sic") or base.sic),
                sic_description=str(extra.get("sic_description") or base.sic_description),
                active_status=str(extra.get("active_status") or base.active_status),
                source="SEC_DIRECTORY+VALIDATED_ENRICHMENT" if extra else "SEC_DIRECTORY",
                source_as_of=str(extra.get("source_as_of") or as_of),
                ingested_at=datetime.now(timezone.utc).isoformat(),
                themes=tuple(extra.get("themes") or ())))
        return result

    def health(self, as_of: str) -> dict[str, Any]:
        records = self.records(as_of)
        identity = sum(all(getattr(row, name) is not None for name in self.REQUIRED_FLAGS)
                       for row in records)
        sectors = sum(row.sector_canonical != "UNKNOWN" for row in records)
        return {"raw_count": len(records), "identity_known_count": identity,
                "identity_coverage_pct": round(identity / len(records) * 100, 4) if records else 0.0,
                "sector_known_count": sectors,
                "sector_coverage_pct": round(sectors / len(records) * 100, 4) if records else 0.0,
                "enrichment_path": str(self.enrichment_path) if self.enrichment_path else ""}


class SECDiscoveryFundamentalProvider:
    """Layer-B CompanyFacts hydration for market-screen survivors."""

    def __init__(self, user_agent: str, cache_dir: str | Path = "data/discovery_fundamentals"):
        if not user_agent:
            raise ValueError("SEC user agent is required")
        self.provider = SECCompanyFactsProvider(user_agent)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls: list[str] = []

    @staticmethod
    def _field(value: Any, source: str, as_of: str, source_ids: tuple[str, ...] = ()) -> FieldValue:
        if value is None:
            return unknown(UnknownState.UNKNOWN_NOT_AVAILABLE.value, source)
        return known(value, source, as_of, source_ids)

    def fundamentals(self, tickers: list[str], as_of: str) -> dict[str, dict[str, FieldValue]]:
        output: dict[str, dict[str, FieldValue]] = {}
        for ticker in tickers:
            try:
                facts = self.provider.facts(ticker)
                self.calls.append(ticker)
                rows = facts.get("normalized_facts", [])
                source_ids = tuple(str(row.get("fact_id")) for row in rows if row.get("fact_id"))
                revenue = facts.get("revenue") or {}
                revenue_value = revenue.get("value")
                previous_revenue = self._previous_duration_value(rows, "revenue", revenue)
                growth = None
                if revenue_value is not None and previous_revenue not in (None, 0):
                    growth = (float(revenue_value) / float(previous_revenue) - 1) * 100
                margin = facts.get("derived", {}).get("gross_margin_pct")
                output[ticker] = {
                    "revenue_growth_acceleration": self._field(growth, "SEC_COMPANYFACTS", as_of, source_ids),
                    "margin_delta": self._field(margin, "SEC_COMPANYFACTS", as_of, source_ids),
                    "operating_cash_flow": self._field(self._value(facts.get("operating_cash_flow")), "SEC_COMPANYFACTS", as_of, source_ids),
                    "cash": self._field(self._value(facts.get("cash")), "SEC_COMPANYFACTS", as_of, source_ids),
                    "shares_outstanding": self._field(self._value(facts.get("shares_outstanding")), "SEC_COMPANYFACTS", as_of, source_ids),
                    "trailing_revenue_usd": self._field(self._value(revenue), "SEC_COMPANYFACTS", as_of, source_ids),
                    "primary_financial_evidence": self._field(bool(revenue_value is not None), "SEC_COMPANYFACTS", as_of, source_ids),
                    "financial_evidence_ids": self._field(list(source_ids), "SEC_COMPANYFACTS", as_of, source_ids),
                    "capital_overhang_status": unknown(UnknownState.UNKNOWN_NOT_FETCHED.value, "SEC_PREFLIGHT_REQUIRED"),
                    "companyfacts_as_of": self._field(max((str(row.get("filed") or "") for row in rows), default=""), "SEC_COMPANYFACTS", as_of, source_ids),
                }
            except Exception:
                self.calls.append(f"{ticker}:FAILED")
                output[ticker] = {
                    "primary_financial_evidence": unknown(UnknownState.UNKNOWN_FETCH_FAILED.value, "SEC_COMPANYFACTS"),
                    "capital_overhang_status": unknown(UnknownState.UNKNOWN_NOT_FETCHED.value, "SEC_PREFLIGHT_REQUIRED"),
                }
        return output

    @staticmethod
    def _value(row: dict[str, Any] | None) -> Any:
        return row.get("value") if isinstance(row, dict) else None

    @staticmethod
    def _previous_duration_value(rows: list[dict[str, Any]], name: str,
                                  selected: dict[str, Any]) -> Any:
        concept = selected.get("concept") if isinstance(selected, dict) else None
        end = str(selected.get("end") or "") if isinstance(selected, dict) else ""
        values = [row for row in rows if row.get("concept") == concept and row.get("period_type") == "DURATION"
                  and str(row.get("end") or "") < end]
        if not values:
            return None
        return max(values, key=lambda row: (str(row.get("end") or ""), str(row.get("filed") or ""))).get("value")


class SECDiscoveryCapitalPreflightProvider:
    """Layer-C targeted SEC event/capital hydration for shortlisted survivors."""

    def __init__(self, user_agent: str, cache_dir: str | Path = "data/cache/discovery/sec"):
        if not user_agent:
            raise ValueError("SEC user agent is required")
        self.facts_provider = SECCompanyFactsProvider(user_agent)
        self.collector = LiveEdgarEvidenceCollector(str(cache_dir), user_agent)
        self.calls: list[str] = []

    def preflight(self, tickers: list[str], as_of: str) -> dict[str, dict[str, FieldValue]]:
        output: dict[str, dict[str, FieldValue]] = {}
        for ticker in tickers:
            try:
                evidence = self.collector.collect(ticker)
                facts = self.facts_provider.facts(ticker)
                snapshot = build_capital_structure(ticker, facts, evidence)
                status = snapshot.capital_overhang_status
                state = (UnknownState.KNOWN.value if status in {"CLEAR", "HIGH_RISK", "REVIEW_REQUIRED"}
                         else UnknownState.UNKNOWN_PARSE_FAILED.value)
                events = snapshot.offering_events
                offering = events[-1].get("offering_type") if events else None
                source_ids = tuple(snapshot.evidence_ids)
                output[ticker] = {
                    "capital_overhang_status": FieldValue(status if state == UnknownState.KNOWN.value else None,
                        state, "SEC_CAPITAL_PREFLIGHT", as_of, source_ids=source_ids),
                    "offering_type": FieldValue(offering, UnknownState.KNOWN.value if offering else UnknownState.UNKNOWN_NOT_AVAILABLE.value,
                        "SEC_OFFERING_SEMANTIC_RESOLVER", as_of, source_ids=source_ids),
                    "primary_financial_evidence": FieldValue(bool(facts.get("revenue")),
                        UnknownState.KNOWN.value if facts.get("revenue") else UnknownState.UNKNOWN_NOT_AVAILABLE.value,
                        "SEC_COMPANYFACTS", as_of, source_ids=source_ids),
                    "offering_event_count": FieldValue(len(events), UnknownState.KNOWN.value,
                        "SEC_OFFERING_SEMANTIC_RESOLVER", as_of, source_ids=source_ids),
                }
                self.calls.append(ticker)
            except Exception:
                self.calls.append(f"{ticker}:FAILED")
                output[ticker] = {
                    "capital_overhang_status": unknown(UnknownState.UNKNOWN_FETCH_FAILED.value, "SEC_CAPITAL_PREFLIGHT"),
                    "offering_type": unknown(UnknownState.UNKNOWN_FETCH_FAILED.value, "SEC_OFFERING_SEMANTIC_RESOLVER"),
                    "primary_financial_evidence": unknown(UnknownState.UNKNOWN_FETCH_FAILED.value, "SEC_COMPANYFACTS"),
                }
        return output


class TossDiscoveryMarketDataProvider:
    """Batch quote + completed daily bar adapter with conservative timestamps."""

    def __init__(self, client: TossClient, quote_batch_size: int = 100):
        self.client = client
        self.quote_batch_size = max(1, int(quote_batch_size))
        self.quote_batches = 0
        self.bar_calls = 0
        self.failed_quote_batches = 0

    @staticmethod
    def _number(row: dict, *names: str):
        for name in names:
            if row.get(name) is not None:
                return float(row[name])
        return None

    def batch_quotes(self, tickers: list[str], as_of: str) -> dict[str, MarketQuote]:
        output = {}
        for offset in range(0, len(tickers), self.quote_batch_size):
            chunk = tickers[offset:offset + self.quote_batch_size]
            self.quote_batches += 1
            received_at = datetime.now(timezone.utc).isoformat()
            try:
                rows = self.client.prices(chunk)
            except Exception:
                self.failed_quote_batches += 1
                continue
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
