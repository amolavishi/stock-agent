"""External adapter contracts and deterministic recorded implementations.

Adapters only return raw observations.  They do not return GateDecision,
ExecutionAction, position size, or any other Python-authoritative conclusion.
This module deliberately avoids inventing Toss/SEC endpoint capabilities.
"""
from __future__ import annotations

import json
import gzip
import math
import re
import statistics
import threading
import time
import csv
import io
import ipaddress
import email.utils
from html import unescape
from html.parser import HTMLParser
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .models import Evidence, RawArtifact, canonical_hash, utc_now
from .vault import (SecureVault, VaultBoundaryError, VaultConflictError,
                    VaultIntegrityError, content_digest)


class ProviderError(RuntimeError):
    """Normalized provider failure; no gate may treat this as a PASS."""


class ProjectionError(ProviderError):
    """Explicit terminal state for a failed/partial projection attempt."""

    def __init__(self, message: str, *, status: str, retryable: bool) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class ProjectionConflictError(ProjectionError):
    """A user-edited or non-canonical note cannot be overwritten."""


class MarketDataProvider(Protocol):
    provider_name: str

    def fetch_market_context(self, query: dict[str, Any]) -> RawArtifact: ...
    def fetch_execution_snapshot(self, security_id: str, query: dict[str, Any]) -> RawArtifact: ...
    def fetch_universe(self, query: dict[str, Any]) -> RawArtifact: ...


class SECProvider(Protocol):
    provider_name: str

    def fetch_submissions(self, identity: dict[str, Any]) -> RawArtifact: ...
    def fetch_facts(self, identity: dict[str, Any]) -> RawArtifact: ...
    def fetch_filings(self, identity: dict[str, Any], query: dict[str, Any] | None = None) -> RawArtifact: ...

    def fetch_cheap_facts(self, identity: dict[str, Any], submissions: RawArtifact | None = None, facts: RawArtifact | None = None) -> RawArtifact: ...


class PortfolioProvider(Protocol):
    provider_name: str

    def fetch_snapshot(self, query: dict[str, Any]) -> RawArtifact: ...


class ResearchEvidenceProvider(Protocol):
    """Non-SEC research evidence boundary. No LLM memory is evidence."""

    provider_name: str

    def fetch(self, subject_id: str, query: dict[str, Any]) -> RawArtifact: ...


def _validate_public_https_url(url: str, *, label: str) -> str:
    """Validate a production provider URL before any network request."""
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ProviderError(f"{label} must be an HTTPS URL without embedded credentials")
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host or host in {"localhost", "metadata.google.internal", "metadata.google"} or host.endswith(".local"):
        raise ProviderError(f"{label} host is private/local")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise ProviderError(f"{label} host is private/reserved")
    return host


def _same_or_subdomain(host: str, configured_host: str) -> bool:
    host = str(host).casefold().rstrip(".")
    configured_host = str(configured_host).casefold().rstrip(".")
    return host == configured_host or host.endswith("." + configured_host)


def _response_final_url(response: Any, fallback: str) -> str:
    """Read a redirect URL without trusting arbitrary test-double objects."""
    value = getattr(response, "url", None)
    if isinstance(value, str) and value:
        return value
    getter = getattr(response, "geturl", None)
    if callable(getter):
        try:
            value = getter()
        except Exception:
            value = None
        if isinstance(value, str) and value:
            return value
    return fallback


class ObsidianProjector(Protocol):
    """Projection-only sink; it cannot mutate authoritative SQLite state."""

    def project(self, run_id: str, note_name: str, document: dict[str, Any]) -> Path: ...


def _artifact(provider: str, artifact_type: str, payload: dict[str, Any], subject_id: str | None = None,
              *, source_observed_at: str | None = None, infer_source: bool = True) -> RawArtifact:
    inferred = payload.get("source_observed_at") or payload.get("observed_at") or payload.get("as_of") or payload.get("timestamp")
    observed_source = source_observed_at if source_observed_at is not None else (inferred if infer_source else None)
    # Recorded/configured adapters without a source timestamp are assigned the
    # observation time at ingestion. Live adapters pass infer_source=False so
    # retrieval time can never masquerade as source freshness.
    if infer_source and observed_source is None:
        observed_source = utc_now()
    observed = observed_source or utc_now()
    return RawArtifact(
        artifact_id=f"artifact-{uuid.uuid4().hex}",
        provider=provider,
        artifact_type=artifact_type,
        subject_id=subject_id,
        observed_at=str(observed),
        payload=payload,
        payload_hash=canonical_hash(payload),
        source_observed_at=str(observed_source) if observed_source else None,
        retrieved_at=utc_now(),
    )


def _nested_source_time(value: Any) -> str | None:
    """Find an explicitly provider-observed timestamp without using retrieval time."""
    keys = {"source_observed_at", "observed_at", "as_of", "timestamp", "published_at", "filing_date", "filingDate", "filed", "filedAt", "reportDate", "acceptanceDatetime", "end"}
    candidates: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, candidate in node.items():
                if key in keys:
                    values = candidate if isinstance(candidate, list) else [candidate]
                    candidates.extend(str(item) for item in values if item not in (None, ""))
                walk(candidate)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    if not candidates:
        return None
    parsed = [(item, _parse_observation_time(item)) for item in candidates]
    valid = [(item, timestamp) for item, timestamp in parsed if timestamp is not None]
    if valid:
        return max(valid, key=lambda pair: pair[1])[0]
    return candidates[0]


def _sec_source_time(payload: Any, artifact_type: str) -> str | None:
    """Return only SEC publication timestamps for a raw SEC artifact.

    SEC payloads contain many dates that describe the *facts* (for example
    XBRL ``start``/``end`` periods and contractual maturity/expiry dates).
    Those dates are not publication timestamps and must never become the
    RawArtifact freshness anchor.  For submissions we use the SEC filing
    metadata arrays; for companyfacts we use each fact's ``filed`` date,
    which is the only publication date carried by that endpoint.
    """
    values: list[str] = []

    def add(value: Any) -> None:
        if value in (None, ""):
            return
        if isinstance(value, list):
            for item in value:
                add(item)
        else:
            values.append(str(value))

    if not isinstance(payload, dict):
        return None
    if artifact_type in {"SEC_SUBMISSIONS", "SEC_FILINGS_INDEX"}:
        recent = ((payload.get("filings") or {}).get("recent") or {})
        # These are SEC publication fields.  reportDate is deliberately not
        # consulted: it is an issuer reporting period and may be future-dated.
        add(recent.get("filingDate"))
        add(recent.get("acceptanceDateTime"))
    elif artifact_type == "SEC_FACTS":
        # Companyfacts has no top-level publication timestamp.  Each XBRL
        # observation carries the filed date; period start/end are excluded.
        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, child in node.items():
                    if str(key).casefold() == "filed":
                        add(child)
                    else:
                        walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(payload.get("facts") or {})
    if not values:
        return None
    parsed = [(item, _parse_observation_time(item)) for item in values]
    valid = [(item, timestamp) for item, timestamp in parsed if timestamp is not None]
    return max(valid, key=lambda pair: pair[1])[0] if valid else values[0]


class RecordedMarketDataProvider:
    """Recorded raw observations for acceptance tests; never authoritative conclusions."""

    provider_name = "recorded-market"

    def __init__(self, recordings: dict[str, Any]) -> None:
        self.recordings = recordings
        self.recorded_at = recordings.get("_recorded_at")

    def fetch_market_context(self, query: dict[str, Any]) -> RawArtifact:
        payload = dict(self.recordings.get("market_context") or {})
        source = payload.get("source")
        if isinstance(source, list) and source and not payload.get("assets"):
            observations: list[dict[str, Any]] = []
            for item in source:
                if not isinstance(item, dict) or not item.get("symbol"):
                    continue
                raw = item.get("payload") if isinstance(item.get("payload"), (dict, list)) else item
                values = _close_series(raw)
                observed_at = item.get("observed_at") or _latest_observation_time(raw)
                if observed_at and len(values) >= 2:
                    observations.append(_market_asset_observation(
                        symbol=str(item["symbol"]).upper(), provider=self.provider_name,
                        source_identifier=str(item.get("source") or "recorded-market"),
                        payload=raw, values=values[-30:], observed_at=str(observed_at),
                        unit=str(item.get("unit") or "RECORDED"), currency=item.get("currency"),
                    ))
            if observations:
                normalized = deterministic_market_context_from_payload(observations)
                payload.update({key: value for key, value in normalized.items() if key != "complete"})
                payload["source"] = [{key: value for key, value in item.items() if key != "_raw_artifact"} for item in observations]
                payload["asset_raw_artifacts"] = [item["_raw_artifact"] for item in observations]
                payload["complete"] = False
        source_time = payload.get("source_observed_at") or payload.get("observed_at") or payload.get("as_of") or self.recorded_at
        return _artifact(self.provider_name, "MARKET_CONTEXT", payload, source_observed_at=source_time)

    def fetch_execution_snapshot(self, security_id: str, query: dict[str, Any]) -> RawArtifact:
        values = dict((self.recordings.get("execution") or {}).get(security_id) or self.recordings.get("market_execution") or {})
        source_time = values.get("source_observed_at") or values.get("observed_at") or values.get("as_of") or self.recorded_at
        return _artifact(self.provider_name, "MARKET_EXECUTION", values, security_id, source_observed_at=source_time)

    def fetch_universe(self, query: dict[str, Any]) -> RawArtifact:
        payload = {"candidates": list(self.recordings.get("candidates") or self.recordings.get("universe") or [])}
        return _artifact(self.provider_name, "UNIVERSE", payload, source_observed_at=self.recorded_at)


class ConfiguredJsonMarketDataProvider:
    """Generic HTTP boundary; endpoint paths/capabilities are configuration.

    No Toss endpoint is assumed.  The caller must provide the verified paths
    and any required authentication headers after confirming the provider's
    current API contract.
    """

    provider_name = "configured-market-http"

    def __init__(self, base_url: str, paths: dict[str, str], headers: dict[str, str] | None = None, timeout: float = 20.0, max_bytes: int = 2_000_000) -> None:
        required = {"market_context", "universe", "execution"}
        if not base_url or not required.issubset(paths):
            raise ValueError("market provider requires base_url and verified market_context/universe/execution paths")
        self.base_url = base_url.rstrip("/")
        self._base_host = _validate_public_https_url(self.base_url, label="market provider base_url")
        self.paths = dict(paths); self.headers = dict(headers or {}); self.timeout = timeout
        self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0:
            raise ValueError("market provider max_bytes must be positive")

    def _get(self, key: str, query: dict[str, Any], subject_id: str | None = None) -> RawArtifact:
        query_string = urllib.parse.urlencode(query, doseq=True) if query else ""
        url = f"{self.base_url}/{self.paths[key].lstrip('/')}" + (f"?{query_string}" if query_string else "")
        if _validate_public_https_url(url, label="market provider request") != self._base_host:
            raise ProviderError("market provider request crossed configured host boundary")
        request = urllib.request.Request(url, headers={"Accept": "application/json", **self.headers})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                try:
                    raw = response.read(self.max_bytes + 1)
                except TypeError:  # compatibility with minimal test doubles
                    raw = response.read()
                if len(raw) > self.max_bytes:
                    raise ProviderError("market provider response exceeds configured size limit")
                final_url = _response_final_url(response, url)
                if not _same_or_subdomain(_validate_public_https_url(str(final_url), label="market provider redirect"), self._base_host):
                    raise ProviderError("market provider redirect crossed configured host boundary")
                payload = json.loads(raw.decode("utf-8"))
        except ProviderError:
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"market provider request failed for {key}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProviderError(f"market provider response for {key} is not an object")
        return _artifact(self.provider_name, key.upper(), payload, subject_id)

    def fetch_market_context(self, query: dict[str, Any]) -> RawArtifact:
        return self._get("market_context", query)

    def fetch_execution_snapshot(self, security_id: str, query: dict[str, Any]) -> RawArtifact:
        return self._get("execution", {**query, "security_id": security_id}, security_id)

    def fetch_universe(self, query: dict[str, Any]) -> RawArtifact:
        return self._get("universe", query)


class _RateLimiter:
    def __init__(self, min_interval: float = 0.1) -> None:
        self.min_interval = max(0.0, float(min_interval)); self._last = 0.0; self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delay = self.min_interval - (time.monotonic() - self._last)
            if delay > 0: time.sleep(delay)
            self._last = time.monotonic()


def _close_series(payload: Any) -> list[float]:
    """Extract close observations from common candle JSON shapes."""
    if isinstance(payload, dict):
        for key in ("result", "candles", "data", "items", "prices", "observations"):
            value = payload.get(key)
            if isinstance(value, (dict, list)):
                values = _close_series(value)
                if values:
                    return values
        return []
    if not isinstance(payload, list):
        return []
    closes: list[float] = []
    for row in payload:
        if isinstance(row, dict):
            value = next((row.get(key) for key in ("close", "closePrice", "lastPrice", "adjClose", "c", "value") if row.get(key) is not None), None)
        elif isinstance(row, (list, tuple)) and row:
            value = row[-1]
        else:
            value = None
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            closes.append(number)
    return closes


def deterministic_market_context_from_payload(source: Any) -> dict[str, Any]:
    """Derive regime/breadth/volatility from raw index candle observations.

    Thresholds are versioned and deterministic.  If there are not enough
    independent series/observations, the result is UNKNOWN and incomplete;
    the MarketContextGate then fails closed.
    """
    payloads = source if isinstance(source, list) else [source]
    series: list[list[float]] = []
    assets: dict[str, dict[str, Any]] = {}
    for item in payloads:
        payload = item.get("payload") if isinstance(item, dict) and isinstance(item.get("payload"), (dict, list)) else item
        values = _close_series(payload)
        if len(values) >= 2:
            series.append(values)
        if isinstance(item, dict) and item.get("symbol"):
            symbol = str(item["symbol"]).upper()
            observed_at = item.get("observed_at") or item.get("source_observed_at") or _nested_source_time(payload)
            assets[symbol] = {
                "symbol": symbol,
                "observed_at": str(observed_at) if observed_at else None,
                "source": str(item.get("source") or "market-provider"),
                "observation_count": len(values),
            }
    base = {"regime": "UNKNOWN", "breadth": "UNKNOWN", "volatility": "UNKNOWN", "complete": False,
            "normalization_status": "INSUFFICIENT_OBSERVATIONS", "normalization_version": "market-context-v2",
            "assets": assets}
    if not series:
        return base
    returns = [(values[-1] / values[0]) - 1.0 for values in series]
    advancing = sum(1 for value in returns if value > 0)
    breadth_ratio = advancing / len(returns)
    if len(returns) >= 2:
        base["breadth"] = "BROAD" if breadth_ratio >= 0.60 else "NARROW" if breadth_ratio <= 0.40 else "MIXED"
    else:
        base["breadth"] = "UNKNOWN"
    daily_returns: list[float] = []
    for values in series:
        daily_returns.extend((values[index] / values[index - 1]) - 1.0 for index in range(1, len(values)))
    annualized_vol = statistics.pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) >= 2 else None
    if annualized_vol is not None:
        base["volatility"] = "HIGH" if annualized_vol >= 0.35 else "LOW" if annualized_vol <= 0.12 else "NORMAL"
        base["annualized_volatility"] = round(float(annualized_vol), 8)
    median_return = statistics.median(returns)
    base["regime"] = "RISK_ON" if median_return >= 0.02 else "RISK_OFF" if median_return <= -0.02 else "TRANSITION"
    base["breadth_ratio"] = round(float(breadth_ratio), 8)
    base["market_return"] = round(float(median_return), 8)
    base["complete"] = base["breadth"] != "UNKNOWN" and base["volatility"] != "UNKNOWN"
    base["normalization_status"] = "COMPLETE" if base["complete"] else "PARTIAL"
    return base


def _timestamp_candidates(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key in ("timestamp", "observed_at", "source_observed_at", "as_of", "published_at", "date"):
            item = value.get(key)
            if item not in (None, ""):
                found.append(str(item))
        for child in value.values():
            found.extend(_timestamp_candidates(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_timestamp_candidates(child))
    elif isinstance(value, str):
        found.append(value)
    return found


def _parse_observation_time(value: str) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.isdigit():
            number = float(text)
            if number > 10_000_000_000:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _latest_observation_time(value: Any) -> str | None:
    parsed = [_parse_observation_time(item) for item in _timestamp_candidates(value)]
    parsed = [item for item in parsed if item is not None]
    if not parsed:
        return None
    return max(parsed).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _market_asset_observation(
    *, symbol: str, provider: str, source_identifier: str, payload: Any,
    values: list[float], observed_at: str, unit: str, currency: str | None,
    raw_artifact_id: str | None = None, fetched_at: str | None = None,
) -> dict[str, Any]:
    if not observed_at or _parse_observation_time(observed_at) is None:
        raise ProviderError(f"market asset {symbol} requires a valid observed_at")
    if len(values) < 2 or not all(math.isfinite(float(value)) for value in values):
        raise ProviderError(f"market asset {symbol} requires at least two finite observations")
    payload_hash = canonical_hash(payload)
    artifact_id = raw_artifact_id or f"artifact-market-{payload_hash[:32]}"
    fetched = fetched_at or utc_now()
    return {
        "symbol": symbol,
        "payload": payload,
        "observed_at": observed_at,
        "source_observed_at": observed_at,
        "fetched_at": fetched,
        "source": source_identifier,
        "source_identifier": source_identifier,
        "provider": provider,
        "value": float(values[-1]),
        "unit": unit,
        "currency": currency,
        "observation_count": len(values),
        "raw_artifact_id": artifact_id,
        "evidence_id": f"E-{artifact_id}",
        "payload_hash": payload_hash,
        "_raw_artifact": {
            "artifact_id": artifact_id,
            "provider": provider,
            "artifact_type": "MARKET_CONTEXT_ASSET",
            "subject_id": symbol,
            "observed_at": observed_at,
            "source_observed_at": observed_at,
            "retrieved_at": fetched,
            "payload": payload,
            "payload_hash": payload_hash,
        },
    }


class FREDMarketDataProvider:
    """Read-only FRED graph CSV adapter for official macro series."""

    provider_name = "fred"
    BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    SERIES = {"VIX": ("VIXCLS", "INDEX_POINTS", None), "US10Y": ("DGS10", "PERCENT", None), "WTI": ("DCOILWTICO", "USD_PER_BARREL", "USD")}

    def __init__(self, timeout: float = 20.0, max_bytes: int = 2_000_000) -> None:
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)

    def fetch_series(self, symbol: str) -> dict[str, Any]:
        key = str(symbol).upper()
        if key not in self.SERIES:
            raise ProviderError(f"unsupported FRED market asset: {key}")
        series_id, unit, currency = self.SERIES[key]
        url = f"{self.BASE_URL}?id={urllib.parse.quote(series_id)}"
        # FRED's graph endpoint is public and serves CSV without an API key.
        # Keep the request intentionally minimal; some edge proxies delay
        # responses when a non-browser Accept/User-Agent combination is sent.
        request = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                try:
                    raw = response.read(self.max_bytes + 1)
                except TypeError:
                    raw = response.read()
                final_url = _response_final_url(response, url)
                if not _same_or_subdomain(_validate_public_https_url(str(final_url), label="FRED redirect"), _validate_public_https_url(self.BASE_URL, label="FRED base_url")):
                    raise ProviderError("FRED redirect crossed configured host boundary")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"FRED request failed for {key}: {exc}") from exc
        if len(raw) > self.max_bytes:
            raise ProviderError(f"FRED response too large for {key}")
        text = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        values: list[float] = []
        dated: list[tuple[str, float]] = []
        for row in rows:
            date = str(row.get("observation_date") or "").strip()
            raw_value = str(row.get(series_id) or "").strip()
            if not date or raw_value in {"", ".", "NaN", "nan"}:
                continue
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if math.isfinite(value):
                values.append(value)
                dated.append((date, value))
        if len(values) < 2:
            raise ProviderError(f"FRED series {series_id} has fewer than two finite observations")
        latest_date = max(date for date, _ in dated)
        observed_at = f"{latest_date}T00:00:00Z"
        payload = {"series_id": series_id, "observations": [{"date": date, "value": value} for date, value in dated[-30:]], "source_url": url}
        return _market_asset_observation(symbol=key, provider=self.provider_name, source_identifier=url, payload=payload, values=values[-30:], observed_at=observed_at, unit=unit, currency=currency)


class YahooChartMarketDataProvider:
    """Read-only Yahoo chart adapter used only for the exact DXY proxy symbol."""

    provider_name = "yahoo-chart"
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
    FUNDAMENTALS_BASE_URL = "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
    SYMBOLS = {"DXY": ("DX-Y.NYB", "INDEX_POINTS", None)}

    def __init__(self, timeout: float = 20.0, max_bytes: int = 2_000_000) -> None:
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)

    def fetch_series(self, symbol: str) -> dict[str, Any]:
        key = str(symbol).upper()
        if key not in self.SYMBOLS:
            raise ProviderError(f"unsupported Yahoo market asset: {key}")
        source_symbol, unit, currency = self.SYMBOLS[key]
        url = f"{self.BASE_URL}{urllib.parse.quote(source_symbol, safe='')}?range=5d&interval=1d&events=history"
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "StockAgent/1.1 read-only market context"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                try:
                    raw = response.read(self.max_bytes + 1)
                except TypeError:
                    raw = response.read()
                final_url = _response_final_url(response, url)
                if not _same_or_subdomain(_validate_public_https_url(str(final_url), label="Yahoo redirect"), _validate_public_https_url(self.BASE_URL, label="Yahoo base_url")):
                    raise ProviderError("Yahoo redirect crossed configured host boundary")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"Yahoo chart request failed for {key}: {exc}") from exc
        if len(raw) > self.max_bytes:
            raise ProviderError(f"Yahoo chart response too large for {key}")
        try:
            document = json.loads(raw.decode("utf-8"))
            result = document["chart"]["result"][0]
            timestamps = result.get("timestamp") or []
            closes = (result.get("indicators") or {}).get("quote", [{}])[0].get("close") or []
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Yahoo chart payload malformed for {key}") from exc
        pairs = [(int(ts), float(close)) for ts, close in zip(timestamps, closes) if ts is not None and close is not None and math.isfinite(float(close))]
        if len(pairs) < 2:
            raise ProviderError(f"Yahoo chart {source_symbol} has fewer than two finite observations")
        pairs.sort(key=lambda item: item[0])
        values = [value for _, value in pairs]
        observed_at = datetime.fromtimestamp(pairs[-1][0], tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = {"source_symbol": source_symbol, "observations": [{"timestamp": ts, "value": value} for ts, value in pairs[-30:]], "source_url": url}
        return _market_asset_observation(symbol=key, provider=self.provider_name, source_identifier=url, payload=payload, values=values[-30:], observed_at=observed_at, unit=unit, currency=currency)

    def fetch_market_cap(self, symbol: str) -> dict[str, Any]:
        """Read the latest published Yahoo market-cap observation.

        This is used only as a discovery metric for explicitly requested live
        symbols.  The publication date from Yahoo is retained separately from
        the retrieval time; no retrieval timestamp is promoted to observation
        time.  A missing or malformed series fails closed.
        """
        key = str(symbol).upper().strip()
        if not re.fullmatch(r"[A-Z0-9.\-]{1,16}", key):
            raise ProviderError("invalid Yahoo fundamentals symbol")
        now = datetime.now(timezone.utc)
        period1 = int((now - timedelta(days=750)).timestamp())
        period2 = int((now + timedelta(days=1)).timestamp())
        query = urllib.parse.urlencode({
            "symbol": key,
            "type": "trailingMarketCap,quarterlyMarketCap",
            "merge": "false",
            "period1": period1,
            "period2": period2,
        })
        url = f"{self.FUNDAMENTALS_BASE_URL}{urllib.parse.quote(key, safe='')}?{query}"
        _validate_public_https_url(url, label="Yahoo fundamentals")
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "StockAgent/1.1 read-only discovery"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                try:
                    raw = response.read(self.max_bytes + 1)
                except TypeError:
                    raw = response.read()
                final_url = _response_final_url(response, url)
                if not _same_or_subdomain(_validate_public_https_url(str(final_url), label="Yahoo fundamentals redirect"), _validate_public_https_url(self.FUNDAMENTALS_BASE_URL, label="Yahoo fundamentals base_url")):
                    raise ProviderError("Yahoo fundamentals redirect crossed configured host boundary")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"Yahoo fundamentals request failed for {key}: {exc}") from exc
        if len(raw) > self.max_bytes:
            raise ProviderError(f"Yahoo fundamentals response too large for {key}")
        try:
            document = json.loads(raw.decode("utf-8"))
            results = document["timeseries"]["result"]
            if not isinstance(results, list):
                raise ValueError("timeseries result is not a list")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"Yahoo fundamentals payload malformed for {key}") from exc
        candidates: list[tuple[datetime, float, str]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            for field in ("trailingMarketCap", "quarterlyMarketCap"):
                values = item.get(field)
                if not isinstance(values, list):
                    continue
                for point in values:
                    if not isinstance(point, dict):
                        continue
                    raw_value = ((point.get("reportedValue") or {}).get("raw"))
                    as_of = point.get("asOfDate")
                    try:
                        value = float(raw_value)
                        observed = datetime.fromisoformat(str(as_of)).replace(tzinfo=timezone.utc)
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(value) or value <= 0 or observed > now:
                        continue
                    candidates.append((observed, value, field))
        if not candidates:
            raise ProviderError(f"Yahoo fundamentals has no usable market cap for {key}")
        observed, value, field = max(candidates, key=lambda item: item[0])
        return {
            "market_cap": value,
            "market_cap_observed_at": observed.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "market_cap_source": f"{url}#{field}",
            "market_cap_provider": self.provider_name,
        }


class CoinGeckoMarketDataProvider:
    """Keyless CoinGecko market-chart adapter for BTC and ETH."""

    provider_name = "coingecko"
    BASE_URL = "https://api.coingecko.com/api/v3/coins/"
    COINS = {"BTC": "bitcoin", "ETH": "ethereum"}

    def __init__(self, timeout: float = 20.0, max_bytes: int = 2_000_000) -> None:
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)

    def fetch_series(self, symbol: str) -> dict[str, Any]:
        key = str(symbol).upper()
        coin = self.COINS.get(key)
        if not coin:
            raise ProviderError(f"unsupported CoinGecko market asset: {key}")
        url = f"{self.BASE_URL}{coin}/market_chart?vs_currency=usd&days=2&interval=hourly"
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "StockAgent/1.1 read-only market context"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                try:
                    raw = response.read(self.max_bytes + 1)
                except TypeError:
                    raw = response.read()
                final_url = _response_final_url(response, url)
                if not _same_or_subdomain(_validate_public_https_url(str(final_url), label="CoinGecko redirect"), _validate_public_https_url(self.BASE_URL, label="CoinGecko base_url")):
                    raise ProviderError("CoinGecko redirect crossed configured host boundary")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"CoinGecko request failed for {key}: {exc}") from exc
        if len(raw) > self.max_bytes:
            raise ProviderError(f"CoinGecko response too large for {key}")
        try:
            document = json.loads(raw.decode("utf-8"))
            pairs = [(int(item[0]), float(item[1])) for item in document.get("prices", []) if isinstance(item, list) and len(item) >= 2 and math.isfinite(float(item[1]))]
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProviderError(f"CoinGecko payload malformed for {key}") from exc
        if len(pairs) < 2:
            raise ProviderError(f"CoinGecko {coin} has fewer than two finite observations")
        pairs.sort(key=lambda item: item[0])
        values = [value for _, value in pairs]
        observed_at = datetime.fromtimestamp(pairs[-1][0] / 1000.0, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = {"coin_id": coin, "observations": [{"timestamp_ms": ts, "value": value} for ts, value in pairs[-30:]], "source_url": url}
        return _market_asset_observation(symbol=key, provider=self.provider_name, source_identifier=url, payload=payload, values=values[-30:], observed_at=observed_at, unit="USD_PER_COIN", currency="USD")


class NasdaqScreenerMarketDataProvider:
    """Read-only broad U.S. equity universe from Nasdaq's public screener.

    The endpoint is used only to reconstruct a broad identifier/price/market
    capitalisation universe.  It does not provide a Gate decision and its
    values remain subject to the Python discovery prefilter.  Intraday price
    timestamps are deliberately taken from the payload's explicit ``asof``
    date; retrieval time is never promoted to an observation timestamp.
    """

    provider_name = "nasdaq-screener"
    BASE_URL = "https://api.nasdaq.com/api/screener/stocks"
    EXCHANGES = {"NASDAQ": "nasdaq", "NYSE": "nyse", "AMEX": "amex", "NYSE AMERICAN": "amex"}

    def __init__(self, timeout: float = 30.0, max_bytes: int = 8_000_000) -> None:
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0:
            raise ValueError("Nasdaq screener max_bytes must be positive")
        self._base_host = _validate_public_https_url(self.BASE_URL, label="Nasdaq screener base_url")

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, "", "N/A", "NA", "null"):
            return None
        try:
            number = float(str(value).replace("$", "").replace(",", "").replace("%", "").strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    @staticmethod
    def _asof_date(value: Any) -> str | None:
        text = str(value or "").strip()
        match = re.search(r"(?:as of\s+)?([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", text, re.IGNORECASE)
        if not match:
            return None
        try:
            parsed = datetime.strptime(match.group(1), "%b %d, %Y")
        except ValueError:
            try:
                parsed = datetime.strptime(match.group(1), "%B %d, %Y")
            except ValueError:
                return None
        observed = parsed.replace(tzinfo=timezone.utc)
        if observed > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ProviderError("Nasdaq screener asof is in the future")
        return observed.date().isoformat() + "T00:00:00Z"

    def fetch_universe(self, query: dict[str, Any]) -> RawArtifact:
        query = dict(query or {})
        raw_markets = query.get("markets") or [query.get("market") or "NASDAQ"]
        markets = [str(item).upper().strip() for item in raw_markets if str(item).strip()]
        if not markets or any(item not in self.EXCHANGES for item in markets):
            raise ProviderError("Nasdaq screener markets must be NASDAQ, NYSE, or AMEX")
        limit = int(query.get("limit", 5000))
        if not 1 <= limit <= 5000:
            raise ProviderError("Nasdaq screener limit must be 1..5000")
        rows: list[dict[str, Any]] = []
        source_rows: list[dict[str, Any]] = []
        source_times: list[str] = []
        for market in markets:
            exchange = self.EXCHANGES[market]
            url = f"{self.BASE_URL}?tableonly=true&limit={limit}&exchange={urllib.parse.quote(exchange)}"
            if _validate_public_https_url(url, label="Nasdaq screener request") != self._base_host:
                raise ProviderError("Nasdaq screener request crossed host boundary")
            request = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "StockAgent/1.1 broad discovery read-only",
            })
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    try:
                        raw = response.read(self.max_bytes + 1)
                    except TypeError:
                        raw = response.read()
                    if len(raw) > self.max_bytes:
                        raise ProviderError("Nasdaq screener response exceeds configured size limit")
                    final_url = _response_final_url(response, url)
                    if _validate_public_https_url(str(final_url), label="Nasdaq screener redirect") != self._base_host:
                        raise ProviderError("Nasdaq screener redirect crossed host boundary")
            except ProviderError:
                raise
            except (urllib.error.URLError, TimeoutError) as exc:
                raise ProviderError(f"Nasdaq screener request failed for {market}: {exc}") from exc
            try:
                document = json.loads(raw.decode("utf-8"))
                data = document.get("data") if isinstance(document, dict) else None
                table = data.get("table") if isinstance(data, dict) else None
                source = list(table.get("rows") or []) if isinstance(table, dict) else []
                asof = self._asof_date(data.get("asof") if isinstance(data, dict) else None)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ProviderError(f"Nasdaq screener payload malformed for {market}") from exc
            if not source:
                raise ProviderError(f"Nasdaq screener returned no rows for {market}")
            if asof:
                source_times.append(asof)
            source_rows.append({"exchange": market, "source_url": url, "asof": asof, "row_count": len(source)})
            for item in source:
                if not isinstance(item, dict):
                    continue
                ticker = str(item.get("symbol") or "").upper().strip()
                if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", ticker):
                    continue
                price = self._number(item.get("lastsale"))
                market_cap = self._number(item.get("marketCap"))
                rows.append({
                    "security_id": ticker,
                    "ticker": ticker,
                    "issuer_name": str(item.get("name") or ticker),
                    "venue": market,
                    "market": market,
                    "security_type": "COMMON_STOCK",
                    "currency": "USD",
                    "price": price,
                    "market_cap": market_cap,
                    "market_cap_source": url,
                    "market_cap_observed_at": asof,
                    "source_observed_at": asof,
                    "source_url": url,
                })
        # A security can appear on more than one exchange feed.  Keep the
        # first deterministic record and expose the source count for audit.
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            deduped.setdefault(str(row["security_id"]), row)
        payload = {
            "securities": list(deduped.values()),
            "markets": markets,
            "source": source_rows,
            "source_observed_at": max(source_times) if source_times else None,
            "normalization_status": "BROAD_UNIVERSE_IDENTIFIERS_AND_QUOTES",
            "provider": self.provider_name,
        }
        return _artifact(
            self.provider_name, "NASDAQ_BROAD_UNIVERSE", payload,
            source_observed_at=max(source_times) if source_times else None,
            infer_source=False,
        )


class CompositeLiveMarketContextProvider:
    """Composes verified Toss, FRED, Yahoo-chart, and CoinGecko read-only inputs."""

    provider_name = "composite-live-market"

    def __init__(self, toss: Any, fred: FREDMarketDataProvider | None = None, yahoo: YahooChartMarketDataProvider | None = None, coingecko: CoinGeckoMarketDataProvider | None = None, screener: NasdaqScreenerMarketDataProvider | None = None) -> None:
        self.toss = toss
        self.fred = fred or FREDMarketDataProvider()
        self.yahoo = yahoo or YahooChartMarketDataProvider()
        self.coingecko = coingecko or CoinGeckoMarketDataProvider()
        self.screener = screener or NasdaqScreenerMarketDataProvider()

    def fetch_market_context(self, query: dict[str, Any]) -> RawArtifact:
        symbols = [str(item).upper() for item in (query.get("symbols") or ["SPY", "QQQ", "IWM", "SOXX", "VIX", "US10Y", "DXY", "WTI", "BTC", "ETH"])]
        toss_symbols = [item for item in symbols if item in {"SPY", "QQQ", "IWM", "SOXX", "SMH"}]
        observations: list[dict[str, Any]] = []
        asset_raw_artifacts: list[dict[str, Any]] = []
        if toss_symbols:
            toss_context = self.toss.fetch_market_context({"symbols": toss_symbols, "interval": query.get("interval", "1d"), "count": int(query.get("count", 30))})
            observations.extend(toss_context.payload.get("source") or [])
            asset_raw_artifacts.extend(item for item in (toss_context.payload.get("asset_raw_artifacts") or []) if isinstance(item, dict))
            asset_raw_artifacts.extend(item["_raw_artifact"] for item in observations if isinstance(item, dict) and item.get("_raw_artifact"))
        for symbol in symbols:
            if symbol in {"VIX", "US10Y", "WTI"}:
                observations.append(self.fred.fetch_series(symbol))
            elif symbol == "DXY":
                observations.append(self.yahoo.fetch_series(symbol))
            elif symbol in {"BTC", "ETH"}:
                observations.append(self.coingecko.fetch_series(symbol))
        normalized = deterministic_market_context_from_payload(observations)
        detailed_assets: dict[str, dict[str, Any]] = {}
        for item in observations:
            if not isinstance(item, dict) or not item.get("symbol"):
                continue
            symbol = str(item["symbol"]).upper()
            detailed_assets[symbol] = {key: value for key, value in item.items() if key != "payload" and not key.startswith("_")}
            if item.get("_raw_artifact"):
                asset_raw_artifacts.append(item["_raw_artifact"])
        for symbol, details in detailed_assets.items():
            # Group is assigned by the deterministic Python adapter, never by
            # a provider completeness claim.  The gate uses it only to apply
            # the immutable EffectiveRuleSet clock policy.
            if symbol in {"SPY", "QQQ", "IWM", "SOXX", "SMH"}:
                details.setdefault("sync_group", "exchange")
            elif symbol in {"VIX", "US10Y", "WTI"}:
                details.setdefault("sync_group", "daily")
            elif symbol == "DXY":
                details.setdefault("sync_group", "fx")
            elif symbol in {"BTC", "ETH"}:
                details.setdefault("sync_group", "crypto")
            normalized.setdefault("assets", {}).setdefault(symbol, {}).update(details)
        normalized["assets"] = {symbol: normalized["assets"][symbol] for symbol in symbols if symbol in normalized.get("assets", {})}
        normalized["source"] = [{key: value for key, value in item.items() if key != "_raw_artifact"} for item in observations]
        normalized["asset_raw_artifacts"] = asset_raw_artifacts
        normalized["provider_sources"] = {symbol: normalized["assets"][symbol].get("provider") for symbol in normalized.get("assets", {})}
        source_time = _latest_observation_time([item.get("observed_at") for item in observations if isinstance(item, dict)]) or _nested_source_time(observations)
        return _artifact(self.provider_name, "MARKET_CONTEXT", normalized, source_observed_at=source_time, infer_source=False)

    def fetch_universe(self, query: dict[str, Any]) -> RawArtifact:
        query = dict(query or {})
        requested_raw = query.get("symbols") or query.get("tickers")
        if not requested_raw:
            # Toss's all-stocks response is an identifier directory, not a
            # broad U.S. quote universe.  Use the public Nasdaq screener for
            # breadth, then scan quotes for every price/cap eligible row before
            # spending the more expensive candle requests on the most liquid
            # estimates.  The old market-cap-descending top-N probe silently
            # excluded mid-cap securities from liquidity evaluation.
            if not query.get("broad", True):
                base = self.toss.fetch_universe(query)
                return base
            screen = self.screener.fetch_universe({
                "markets": query.get("markets") or ["NASDAQ", "NYSE", "AMEX"],
                "limit": int(query.get("screener_limit", 5000)),
            })
            screened = list(screen.payload.get("securities") or [])
            min_price = float(query.get("min_price", 0) or 0)
            min_cap = float(query.get("min_market_cap", 0) or 0)
            # ``liquidity_full_probe_limit`` bounds candle retrieval only.  A
            # caller may provide the legacy name explicitly, but its default is
            # deliberately no longer 200 and it never controls quote breadth.
            # Keep the full-candle sample bounded for an operator-invoked
            # daily run.  Quote coverage still spans every eligible security;
            # this limit controls only the expensive historical candle probe,
            # which is selected across cap strata rather than a top-cap slice.
            probe_limit = int(query.get("liquidity_full_probe_limit", query.get("liquidity_probe_limit", 30)))
            if not 1 <= probe_limit <= 5000:
                raise ProviderError("liquidity_full_probe_limit must be 1..5000")
            min_liquidity = float(query.get("min_average_dollar_volume", 10_000_000) or 10_000_000)
            eligible_rows = [row for row in screened if (row.get("price") or 0) >= min_price and (row.get("market_cap") or 0) >= min_cap]
            symbols = list(dict.fromkeys(str(row["security_id"]).upper() for row in eligible_rows if row.get("security_id")))
            quote_by_symbol: dict[str, dict[str, Any]] = {}
            for start in range(0, len(symbols), 200):
                price_artifact = self.toss.fetch_prices(symbols[start:start + 200])
                result = price_artifact.payload.get("result") if isinstance(price_artifact.payload, dict) else []
                for item in result if isinstance(result, list) else []:
                    if isinstance(item, dict) and item.get("symbol"):
                        quote_by_symbol[str(item["symbol"]).upper()] = {**item, "_artifact": price_artifact}
            probe_errors: list[str] = []
            average_volume_keys = ("averageVolume", "avgVolume", "average_volume", "volumeAverage", "volumeAvg")
            daily_volume_keys = ("volume", "accumulatedVolume", "accVolume", "volumeToday", "tradeVolume")
            liquidity_scanned_rows: set[str] = set()
            adv_observed_rows: set[str] = set()
            for row in eligible_rows:
                try:
                    existing_adv = float(row.get("average_dollar_volume"))
                except (TypeError, ValueError):
                    existing_adv = 0.0
                if math.isfinite(existing_adv) and existing_adv > 0:
                    liquidity_scanned_rows.add(str(row.get("security_id") or "").upper())
                    adv_observed_rows.add(str(row.get("security_id") or "").upper())
            for row in screened:
                ticker = str(row.get("security_id") or "").upper()
                quote = quote_by_symbol.get(ticker)
                if quote:
                    try:
                        price = float(quote.get("lastPrice"))
                        if not math.isfinite(price) or price <= 0:
                            raise ValueError
                    except (TypeError, ValueError):
                        price = None
                    if price is not None:
                        row["price"] = price
                        row["currency"] = str(quote.get("currency") or "USD")
                        row["source_observed_at"] = quote.get("timestamp") or row.get("source_observed_at")
                        row["price_source"] = "toss"
                        for key in average_volume_keys:
                            try:
                                average_volume = float(quote.get(key))
                            except (TypeError, ValueError):
                                average_volume = None
                            if average_volume is not None and math.isfinite(average_volume) and average_volume > 0:
                                row["average_volume"] = average_volume
                                row["average_dollar_volume"] = average_volume * price
                                row["average_dollar_volume_source"] = f"toss_quote:{key}*price"
                                row["liquidity_status"] = "QUOTE_AVERAGE_VOLUME"
                                row["liquidity_source"] = f"{self.toss.base_url}/api/v1/quotes"
                                row["liquidity_observed_at"] = quote.get("timestamp") or row.get("source_observed_at")
                                liquidity_scanned_rows.add(ticker)
                                adv_observed_rows.add(ticker)
                                break
                        else:
                            # A single-day quote volume is useful only as a
                            # bounded selection hint; it is intentionally not
                            # promoted to ADV until candles are verified.
                            for key in daily_volume_keys:
                                try:
                                    daily_volume = float(quote.get(key))
                                except (TypeError, ValueError):
                                    daily_volume = None
                                if daily_volume is not None and math.isfinite(daily_volume) and daily_volume > 0:
                                    row["approximate_dollar_volume"] = daily_volume * price
                                    row["approximate_dollar_volume_source"] = f"toss_quote:{key}*price"
                                    row["liquidity_status"] = "QUOTE_SINGLE_DAY_ESTIMATE"
                                    row["liquidity_observed_at"] = quote.get("timestamp") or row.get("source_observed_at")
                                    liquidity_scanned_rows.add(ticker)
                                    break
            def _liquidity_hint(row: dict[str, Any]) -> float:
                for key in ("average_dollar_volume", "approximate_dollar_volume"):
                    try:
                        value = float(row.get(key))
                    except (TypeError, ValueError):
                        value = 0.0
                    if math.isfinite(value) and value > 0:
                        return value
                return 0.0

            # Full candles are expensive, so keep a bounded daily budget but do
            # not spend the entire budget on the same high-volume names every
            # day.  One third of the budget prioritizes current quote-volume
            # hints; the remainder is a deterministic daily rotation through
            # the still-unobserved strategy universe.  Re-running the same
            # official day is reproducible, while consecutive days advance the
            # coverage window instead of permanently starving mid/small caps.
            estimated_rows = [
                row for row in eligible_rows
                if row.get("approximate_dollar_volume") is not None and _liquidity_hint(row) >= min_liquidity
            ]
            estimated_rows.sort(key=lambda row: (_liquidity_hint(row), str(row.get("security_id"))), reverse=True)
            priority_slots = min(len(estimated_rows), max(1, probe_limit // 3))
            probe_rows = list(estimated_rows[:priority_slots])
            selected_ids = {str(row.get("security_id") or "").upper() for row in probe_rows}
            remaining = [
                row for row in eligible_rows
                if str(row.get("security_id") or "").upper() not in selected_ids
                and row.get("average_dollar_volume") is None
            ]
            rotation_slots = max(0, probe_limit - len(probe_rows))
            rotation_key = str(query.get("liquidity_rotation_key") or query.get("as_of") or utc_now())[:10]
            rotation_offset = 0
            rotation_rows: list[dict[str, Any]] = []
            if rotation_slots and remaining:
                stable_pool = sorted(remaining, key=lambda row: str(row.get("security_id") or "").upper())
                try:
                    rotation_ordinal = datetime.fromisoformat(rotation_key).date().toordinal()
                except (TypeError, ValueError):
                    rotation_ordinal = int(canonical_hash(rotation_key)[:12], 16)
                rotation_offset = (rotation_ordinal * rotation_slots) % len(stable_pool)
                for step in range(min(rotation_slots, len(stable_pool))):
                    rotation_rows.append(stable_pool[(rotation_offset + step) % len(stable_pool)])
                probe_rows.extend(rotation_rows)
            probe_ids = {str(row.get("security_id") or "").upper() for row in probe_rows}
            for row in screened:
                ticker = str(row.get("security_id") or "").upper()
                if ticker not in probe_ids:
                    continue
                try:
                    candles_artifact = self.toss.fetch_candles(ticker, "1d", int(query.get("technical_count", 30)))
                    candle_payload = candles_artifact.payload.get("result") if isinstance(candles_artifact.payload, dict) else []
                    candles = candle_payload.get("candles") if isinstance(candle_payload, dict) else candle_payload
                    closes: list[float] = []
                    volumes: list[float] = []
                    for candle in candles if isinstance(candles, list) else []:
                        if not isinstance(candle, dict):
                            continue
                        try:
                            close, volume = float(candle.get("closePrice")), float(candle.get("volume"))
                        except (TypeError, ValueError):
                            continue
                        if math.isfinite(close) and close > 0:
                            closes.append(close)
                        if math.isfinite(volume) and volume > 0:
                            volumes.append(volume)
                    if len(closes) >= 2 and volumes:
                        row["prices"] = closes
                        row["volumes"] = volumes
                        row["average_volume"] = sum(volumes[-20:]) / len(volumes[-20:])
                        try:
                            candle_price = float(row.get("price") or closes[-1])
                        except (TypeError, ValueError):
                            candle_price = closes[-1]
                        row["average_dollar_volume"] = row["average_volume"] * candle_price
                        row["average_dollar_volume_source"] = "mean(volumes[-20:])*price"
                        row["liquidity_status"] = "FULL_CANDLE"
                        row["liquidity_source"] = f"{self.toss.base_url}/api/v1/candles?symbol={urllib.parse.quote(ticker)}&interval=1d"
                        row["liquidity_observed_at"] = candles_artifact.source_observed_at
                        liquidity_scanned_rows.add(ticker)
                        adv_observed_rows.add(ticker)
                except (ProviderError, ValueError) as exc:
                    probe_errors.append(f"{ticker}:{type(exc).__name__}")
            payload = {
                "securities": screened,
                "source": screen.payload.get("source") or [],
                "source_artifact_id": screen.artifact_id,
                "source_artifact_hash": screen.payload_hash,
                "enrichment_provider": self.provider_name,
                "broad_discovery": True,
                "probe_limit": probe_limit,
                "probe_count": len(probe_rows),
                "probe_strategy": "BROAD_QUOTE_PRIORITY_PLUS_DAILY_ROTATION",
                "quote_scan_count": len(symbols),
                "liquidity_rotation_key": rotation_key,
                "liquidity_rotation_offset": rotation_offset,
                "liquidity_priority_probe_count": len(probe_rows) - len(rotation_rows),
                "liquidity_rotation_probe_count": len(rotation_rows),
                "liquidity_scanned_count": len(liquidity_scanned_rows),
                "probe_not_evaluated_count": sum(1 for row in eligible_rows if str(row.get("security_id") or "").upper() not in adv_observed_rows),
                "probe_not_evaluated_ids": [str(row.get("security_id") or "").upper() for row in eligible_rows if str(row.get("security_id") or "").upper() not in adv_observed_rows][:200],
                "probe_errors": probe_errors[:200],
            }
            source_times = [str(row.get("source_observed_at")) for row in screened if row.get("source_observed_at")]
            return _artifact(self.provider_name, "UNIVERSE", payload, None, source_observed_at=_latest_observation_time(source_times) or screen.source_observed_at, infer_source=False)
        base = self.toss.fetch_universe(query)
        payload = dict(base.payload)
        rows = payload.get("securities") if isinstance(payload.get("securities"), list) else []
        requested = {str(item).upper().strip() for item in requested_raw if str(item).strip()}
        if not requested or len(requested) > 50:
            raise ProviderError("live universe symbols must contain 1..50 tickers")
        selected = [row for row in rows if str(row.get("ticker") or row.get("security_id") or "").upper() in requested]
        if not selected:
            raise ProviderError("Toss live universe returned none of the requested symbols")
        enriched: list[dict[str, Any]] = []
        source_times: list[str] = []
        for row in selected:
            ticker = str(row.get("ticker") or row.get("security_id") or "").upper()
            prices_artifact = self.toss.fetch_prices([ticker])
            price_rows = prices_artifact.payload.get("result") if isinstance(prices_artifact.payload, dict) else []
            price_row = price_rows[0] if isinstance(price_rows, list) and price_rows and isinstance(price_rows[0], dict) else {}
            try:
                last_price = float(price_row.get("lastPrice"))
            except (TypeError, ValueError):
                raise ProviderError(f"Toss live price missing or malformed for {ticker}")
            if not math.isfinite(last_price) or last_price <= 0:
                raise ProviderError(f"Toss live price invalid for {ticker}")
            candles_artifact = self.toss.fetch_candles(ticker, "1d", int(query.get("technical_count", 30)))
            candle_payload = candles_artifact.payload.get("result") if isinstance(candles_artifact.payload, dict) else []
            candles = candle_payload.get("candles") if isinstance(candle_payload, dict) else candle_payload
            if not isinstance(candles, list):
                raise ProviderError(f"Toss live candles missing for {ticker}")
            closes: list[float] = []
            volumes: list[float] = []
            for candle in candles:
                if not isinstance(candle, dict):
                    continue
                try:
                    close = float(candle.get("closePrice"))
                    volume = float(candle.get("volume"))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(close) and close > 0:
                    closes.append(close)
                if math.isfinite(volume) and volume > 0:
                    volumes.append(volume)
                if candle.get("timestamp"):
                    source_times.append(str(candle["timestamp"]))
            if len(closes) < 2 or not volumes:
                raise ProviderError(f"Toss live candles lack usable observations for {ticker}")
            cap = self.yahoo.fetch_market_cap(ticker)
            updated = dict(row)
            updated.update({
                "ticker": ticker,
                "security_id": ticker,
                "price": last_price,
                "prices": closes,
                "volumes": volumes,
                "average_volume": sum(volumes[-20:]) / len(volumes[-20:]),
                "market_cap": cap["market_cap"],
                "market_cap_source": cap["market_cap_source"],
                "market_cap_observed_at": cap["market_cap_observed_at"],
                "market_cap_provider": cap["market_cap_provider"],
                "market": str(query.get("market") or updated.get("venue") or "UNKNOWN"),
                "currency": str(price_row.get("currency") or updated.get("currency") or "USD"),
                "live_metric_sources": {
                    "price": f"{self.toss.base_url}/api/v1/prices?symbols={urllib.parse.quote(ticker)}",
                    "candles": f"{self.toss.base_url}/api/v1/candles?symbol={urllib.parse.quote(ticker)}&interval=1d",
                },
            })
            enriched.append(updated)
            if prices_artifact.source_observed_at:
                source_times.append(prices_artifact.source_observed_at)
        payload["securities"] = enriched
        payload["requested_symbols"] = sorted(requested)
        payload["enrichment_provider"] = self.provider_name
        source_observed = _latest_observation_time(source_times) or base.source_observed_at
        return _artifact(self.provider_name, "UNIVERSE", payload, None, source_observed_at=source_observed, infer_source=False)

    def fetch_execution_snapshot(self, security_id: str, query: dict[str, Any]) -> RawArtifact:
        return self.toss.fetch_execution_snapshot(security_id, query)


def _sanitize_toss_diagnostic_text(value: str, *secrets: str | None) -> str:
    """Bounded diagnostic text with credential/token material redacted."""
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED]")
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|api[_-]?key|authorization)\s*[:=]\s*[^,;\s}]+", "[REDACTED]", text)
    return text[:240]


class TossMarketDataProvider:
    """Verified Toss Securities Open API adapter.

    Only read endpoints are exposed.  No GateDecision, action, price stop, or
    position size is created here.  The API contract is pinned to the official
    OAuth2 and ``/api/v1`` paths; callers may override only the base URL for a
    documented test server.
    """

    provider_name = "toss"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 base_url: str = "https://openapi.tossinvest.com", timeout: float = 20.0,
                 max_retries: int = 2, min_interval: float = 0.1, max_bytes: int = 4_000_000) -> None:
        self.client_id = client_id; self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self._base_host = _validate_public_https_url(self.base_url, label="Toss base_url")
        self.timeout = timeout; self.max_retries = max(0, int(max_retries)); self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0:
            raise ValueError("Toss max_bytes must be positive")
        self._token_value: str | None = None; self._token_expiry = 0.0; self._token_lock = threading.Lock()
        self._limiter = _RateLimiter(min_interval)
        # Safe, in-memory diagnostics for provider failures.  This never
        # contains bearer/API credential material and is exposed only by the
        # read-only smoke commands for operator diagnosis.
        self.last_error_diagnostic: dict[str, Any] | None = None

    def _request_json(self, method: str, path: str, query: dict[str, Any] | None = None,
                      form: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                      auth: bool = True) -> dict[str, Any]:
        if auth:
            token = self._access_token()
            headers = {"Authorization": f"Bearer {token}", **(headers or {})}
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            headers = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query: url += "?" + urllib.parse.urlencode(query, doseq=True)
        if _validate_public_https_url(url, label="Toss request") != self._base_host:
            raise ProviderError("Toss request crossed configured host boundary")
        request = urllib.request.Request(url, data=body, method=method, headers={"Accept": "application/json", **(headers or {})})
        header_names = {str(key).casefold() for key in (headers or {})}
        request_diagnostic = {
            "endpoint": f"{urllib.parse.urlparse(url).scheme}://{urllib.parse.urlparse(url).netloc}{urllib.parse.urlparse(url).path}",
            "method": str(method).upper(),
            "authorization_attached": "authorization" in header_names,
            "account_header_attached": "x-tossinvest-account" in header_names,
            "client_credentials_configured": bool(self.client_id and self.client_secret),
            "token_cached": bool(self._token_value),
            "token_seconds_remaining": max(0, int(self._token_expiry - time.time())) if self._token_value else 0,
        }
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._limiter.wait()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    try:
                        raw = response.read(self.max_bytes + 1)
                    except TypeError:  # compatibility with minimal test doubles
                        raw = response.read()
                    if len(raw) > self.max_bytes:
                        raise ProviderError("Toss response exceeds configured size limit")
                    final_url = _response_final_url(response, url)
                    final_host = _validate_public_https_url(str(final_url), label="Toss redirect")
                    if not _same_or_subdomain(final_host, self._base_host):
                        raise ProviderError("Toss redirect crossed configured host boundary")
                    payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict): raise ProviderError("Toss response is not an object")
                self.last_error_diagnostic = None
                return payload
            except ProviderError:
                raise
            except urllib.error.HTTPError as exc:
                error_code = None
                detail = ""
                try:
                    raw_bytes = exc.read()
                    if str(exc.headers.get("Content-Encoding", "")).casefold() == "gzip":
                        raw_bytes = gzip.decompress(raw_bytes)
                    raw = raw_bytes.decode("utf-8", errors="replace")
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        nested = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
                        error_code = nested.get("code") or nested.get("errorCode") or parsed.get("code") or parsed.get("errorCode") or (parsed.get("error") if isinstance(parsed.get("error"), str) else None)
                        detail = nested.get("message") or nested.get("error_description") or parsed.get("message") or parsed.get("error_description") or parsed.get("msg") or ""
                except Exception:
                    # Do not persist arbitrary HTTP bodies; diagnostics only
                    # contain the status and a bounded structured message.
                    detail = ""
                safe_detail = _sanitize_toss_diagnostic_text(str(detail), self.client_id, self.client_secret)
                diagnostic = {**request_diagnostic, "status": int(exc.code), "error_code": str(error_code)[:120] if error_code else None, "message": safe_detail[:240]}
                self.last_error_diagnostic = diagnostic
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= self.max_retries:
                    suffix = f": {safe_detail}" if safe_detail else (f" (code={diagnostic['error_code']})" if diagnostic["error_code"] else "")
                    raise ProviderError(f"Toss HTTP {exc.code}{suffix}") from exc
                last = exc; time.sleep(min(2.0, 0.25 * (2 ** attempt)))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt >= self.max_retries: raise ProviderError(f"Toss request failed: {exc}") from exc
                last = exc; time.sleep(min(2.0, 0.25 * (2 ** attempt)))
        raise ProviderError(f"Toss request failed: {last}")

    def _access_token(self) -> str:
        if not self.client_id or not self.client_secret: raise ProviderError("TOSS_CLIENT_ID and TOSS_CLIENT_SECRET are required")
        with self._token_lock:
            if self._token_value and time.time() < self._token_expiry - 30: return self._token_value
            payload = self._request_json("POST", "/oauth2/token", form={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret}, auth=False)
            token = payload.get("access_token"); expires = payload.get("expires_in", 0)
            if not token: raise ProviderError("Toss token response missing access_token")
            self._token_value = str(token); self._token_expiry = time.time() + max(0, int(expires or 0)); return self._token_value

    def fetch_prices(self, symbols: list[str]) -> RawArtifact:
        if not symbols or len(symbols) > 200: raise ProviderError("Toss prices requires 1..200 symbols")
        payload = self._request_json("GET", "/api/v1/prices", {"symbols": ",".join(str(s) for s in symbols)})
        # A live provider response without an observed timestamp is not made
        # fresh by ingestion time.  The execution freshness fence must reject
        # it instead of allowing retrieval time to masquerade as observation.
        return _artifact(self.provider_name, "TOSS_PRICES", payload, source_observed_at=_nested_source_time(payload), infer_source=False)

    def fetch_accounts(self) -> RawArtifact:
        """Read the account list and expose the documented ``accountSeq`` values.

        The Open API account endpoint is the authoritative source for the
        ``X-Tossinvest-Account`` header.  No account number or secret is
        logged, and this endpoint cannot create or modify orders.
        """
        payload = self._request_json("GET", "/api/v1/accounts")
        rows = payload.get("result") if isinstance(payload.get("result"), list) else []
        accounts = [
            {
                "account_seq": int(row["accountSeq"]),
                "account_type": str(row.get("accountType") or "UNKNOWN"),
            }
            for row in rows
            if isinstance(row, dict) and row.get("accountSeq") is not None
        ]
        # Persist only the minimum routing metadata required by the read-only
        # portfolio adapter.  The raw /accounts response may contain accountNo
        # or future credential-like fields and must never enter RawArtifact or
        # SQLite persistence.
        return _artifact(
            self.provider_name,
            "TOSS_ACCOUNTS",
            {"accounts": accounts, "source_endpoint": "/api/v1/accounts", "result_count": len(rows)},
        )

    def fetch_candles(self, symbol: str, interval: str = "1d", count: int = 100, before: str | None = None) -> RawArtifact:
        if not symbol or interval not in {"1m", "1d"} or not 1 <= int(count) <= 200: raise ProviderError("invalid Toss candles request")
        query: dict[str, Any] = {"symbol": symbol, "interval": interval, "count": int(count)}
        if before: query["before"] = before
        payload = self._request_json("GET", "/api/v1/candles", query)
        return _artifact(self.provider_name, "TOSS_CANDLES", payload, symbol, source_observed_at=_nested_source_time(payload), infer_source=False)

    def fetch_universe(self, query: dict[str, Any]) -> RawArtifact:
        market = str(query.get("market") or "KOSPI")
        if market not in {"KOSPI", "KOSDAQ", "NYSE", "NASDAQ", "AMEX", "KR_ETC", "US_ETC"}: raise ProviderError("invalid Toss market")
        params = {"market": market, "status": query.get("status", "ACTIVE")}
        for key in ("securityType", "commonShare"):
            if key in query: params[key] = query[key]
        payload = self._request_json("GET", "/api/v1/stocks/all", params)
        rows = payload.get("result") if isinstance(payload.get("result"), list) else []
        securities = [{"security_id": str(row.get("symbol")), "ticker": str(row.get("symbol")), "issuer_name": str(row.get("name") or row.get("symbol")), "venue": str(row.get("market") or market), "currency": row.get("currency"), "shares_outstanding": row.get("sharesOutstanding")} for row in rows if row.get("symbol")]
        return _artifact(self.provider_name, "UNIVERSE", {"source": payload, "securities": securities}, None, source_observed_at=_nested_source_time(payload), infer_source=False)

    def fetch_market_context(self, query: dict[str, Any]) -> RawArtifact:
        # Verified Toss candle endpoint; market-indicators is unavailable for US ETF symbols.
        symbols = query.get("symbols") or ["SPY", "QQQ", "IWM", "SOXX"]
        observations: list[dict[str, Any]] = []
        for symbol_value in symbols:
            symbol = str(symbol_value).upper()
            artifact = self.fetch_candles(symbol, str(query.get("interval", "1d")), int(query.get("count", 30)))
            payload = artifact.payload
            values = _close_series(payload)
            observed_at = _latest_observation_time(payload) or artifact.source_observed_at
            if not observed_at:
                raise ProviderError(f"Toss candle payload has no observed_at for {symbol}")
            observations.append(_market_asset_observation(
                symbol=symbol,
                provider=self.provider_name,
                source_identifier=f"{self.base_url}/api/v1/candles?symbol={urllib.parse.quote(symbol)}",
                payload=payload,
                values=values[-30:],
                observed_at=observed_at,
                unit="USD_PER_SHARE",
                currency="USD",
                raw_artifact_id=artifact.artifact_id,
                fetched_at=artifact.retrieved_at,
            ))
        normalized = deterministic_market_context_from_payload(observations)
        normalized["source"] = [{key: value for key, value in item.items() if key != "_raw_artifact"} for item in observations]
        normalized["asset_raw_artifacts"] = [item["_raw_artifact"] for item in observations]
        for item in observations:
            normalized.setdefault("assets", {}).setdefault(item["symbol"], {}).update({
                key: value for key, value in item.items() if key not in {"payload", "_raw_artifact"}
            })
        normalized["provider_sources"] = {item["symbol"]: self.provider_name for item in observations}
        return _artifact(self.provider_name, "MARKET_CONTEXT", normalized, source_observed_at=_latest_observation_time(observations), infer_source=False)

    def fetch_execution_snapshot(self, security_id: str, query: dict[str, Any]) -> RawArtifact:
        prices = self.fetch_prices([security_id])
        candles = self.fetch_candles(security_id, str(query.get("interval", "1d")), int(query.get("count", 30)))
        payload = {
            "security_id": security_id,
            "prices": prices.payload,
            "candles": candles.payload,
            "normalization_status": "RISK_INPUTS_REQUIRED",
            "risk_inputs_required": ["execution_stop", "account_equity", "gap_risk", "event_risk_pct"],
        }
        # Current price is provider-derived only.  Stop, equity, and event/gap
        # risk are not Toss market observations and must never be copied from
        # caller query fields into the authoritative execution snapshot.
        results = (prices.payload.get("result") or [])
        if results and isinstance(results[0], dict) and results[0].get("lastPrice") is not None:
            payload["current_price"] = float(results[0]["lastPrice"])
        payload["core_input_complete"] = False
        return _artifact(self.provider_name, "MARKET_EXECUTION", payload, security_id, source_observed_at=_nested_source_time([prices.payload, candles.payload]), infer_source=False)


class TossPortfolioProvider:
    """Read-only Toss account/holdings/buying-power adapter."""

    provider_name = "toss-portfolio"

    def __init__(self, market_provider: TossMarketDataProvider, account_seq: int | None = None) -> None:
        self.market_provider = market_provider; self.account_seq = account_seq

    def discover_accounts(self) -> list[dict[str, Any]]:
        """Return sanitized account metadata from the documented account endpoint."""
        artifact = self.market_provider.fetch_accounts()
        return list(artifact.payload.get("accounts") or [])

    def _resolve_account_seq(self, query: dict[str, Any]) -> int:
        explicit = self.account_seq or query.get("account_seq")
        if explicit is not None:
            try:
                return int(explicit)
            except (TypeError, ValueError) as exc:
                raise ProviderError("TOSS account_seq must be an integer") from exc
        accounts = self.discover_accounts()
        if not accounts:
            raise ProviderError("Toss account list is empty; no accountSeq available")
        if len(accounts) > 1:
            raise ProviderError("multiple Toss accounts found; set TOSS_ACCOUNT_SEQ explicitly")
        return int(accounts[0]["account_seq"])

    def fetch_snapshot(self, query: dict[str, Any]) -> RawArtifact:
        query = query or {}
        account = self._resolve_account_seq(query)
        holdings = self.market_provider._request_json("GET", "/api/v1/holdings", {"symbol": query["symbol"]} if query.get("symbol") else None, headers={"X-Tossinvest-Account": str(account)})
        buying = self.market_provider._request_json("GET", "/api/v1/buying-power", {"currency": query.get("currency", "KRW")}, headers={"X-Tossinvest-Account": str(account)})
        result = holdings.get("result") or {}; items = result.get("items") or []
        cash = float((buying.get("result") or {}).get("cashBuyingPower", 0))
        market_value = float(((result.get("marketValue") or {}).get("amount") or {}).get("krw", 0))
        positions = [{"subject_id": str(row.get("symbol")), "shares": int(float(row.get("quantity", 0))), "average_cost": float(row.get("averagePurchasePrice", 0)), "as_of": str(row.get("asOf") or utc_now())} for row in items]
        source_time = _nested_source_time([holdings, buying])
        # Persist only normalized portfolio economics and coarse source
        # metadata.  Raw holdings/buying-power payloads can contain account
        # identifiers or provider-added sensitive fields and therefore stay
        # transport-local.
        persisted_payload = {
            "as_of": source_time or utc_now(),
            "account_seq": account,
            "cash": cash,
            "total_equity": cash + market_value,
            "positions": positions,
            "source_endpoints": ["/api/v1/holdings", "/api/v1/buying-power"],
            "holding_count": len(items),
        }
        return _artifact(
            self.provider_name,
            "PORTFOLIO_SNAPSHOT",
            persisted_payload,
            None,
            source_observed_at=source_time,
            infer_source=False,
        )


class RecordedSECProvider:
    provider_name = "recorded-sec"

    def __init__(self, recordings: dict[str, Any] | None = None) -> None:
        self.recordings = recordings or {}
        self.recorded_at = self.recordings.get("_recorded_at")

    def _get(self, name: str, identity: dict[str, Any]) -> RawArtifact:
        sid = identity.get("security_id") or identity.get("cik") or "UNKNOWN"
        payload = dict((self.recordings.get(sid) or {}).get(name) or self.recordings.get(name) or {})
        source_time = payload.get("source_observed_at") or payload.get("observed_at") or payload.get("as_of") or self.recorded_at
        return _artifact(self.provider_name, f"SEC_{name.upper()}", payload, sid, source_observed_at=source_time)

    def fetch_submissions(self, identity: dict[str, Any]) -> RawArtifact:
        return self._get("submissions", identity)

    def fetch_facts(self, identity: dict[str, Any]) -> RawArtifact:
        return self._get("facts", identity)

    def fetch_filings(self, identity: dict[str, Any], query: dict[str, Any] | None = None) -> RawArtifact:
        return self._get("filings", identity)

    def fetch_cheap_facts(self, identity: dict[str, Any], submissions: RawArtifact | None = None, facts: RawArtifact | None = None) -> RawArtifact:
        sid = identity.get("security_id") or identity.get("cik") or "UNKNOWN"
        recorded = self.recordings.get(sid) or self.recordings
        payload = dict(recorded.get("cheap_facts") or {}) if isinstance(recorded, dict) else {}
        if not payload:
            payload = {"extraction_status": "INCOMPLETE", "identity_status": "CONFIRMED", "unknowns": ["cheap_capital_facts_missing"]}
        evidence_id = "E-SEC-CHEAP-1"
        for key in ("active_atm", "large_shelf_and_financing_need", "toxic_convertible", "material_warrant", "imminent_financing", "cash_runway_critical"):
            value = payload.get(key)
            if not isinstance(value, dict):
                value = {"state": "UNKNOWN"}
            value.setdefault("state", "UNKNOWN")
            value.setdefault("details", {"summary": "SEC cheap-facts extraction", "evidence_ids": [evidence_id], "unknowns": []})
            value["evidence_ids"] = [evidence_id]
            payload[key] = value
        source_time = payload.get("source_observed_at") or payload.get("observed_at") or payload.get("as_of") or self.recorded_at
        return _artifact(self.provider_name, "SEC_CHEAP_FACTS", payload, sid, source_observed_at=source_time)


class RecordedPortfolioProvider:
    provider_name = "recorded-portfolio"

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.recorded_at = snapshot.get("_recorded_at")

    def fetch_snapshot(self, query: dict[str, Any]) -> RawArtifact:
        payload = dict(self.snapshot)
        source_time = payload.get("source_observed_at") or payload.get("observed_at") or payload.get("as_of") or self.recorded_at
        return _artifact(self.provider_name, "PORTFOLIO_SNAPSHOT", payload, source_observed_at=source_time)


class RecordedResearchEvidenceProvider:
    """Recorded raw research evidence for tests; never an LLM-memory source."""

    provider_name = "recorded-research"

    def __init__(self, recordings: dict[str, Any] | None = None) -> None:
        self.recordings = recordings or {}
        self.recorded_at = self.recordings.get("_recorded_at")

    def fetch(self, subject_id: str, query: dict[str, Any]) -> RawArtifact:
        payload = self.recordings.get(subject_id) or self.recordings.get("default")
        if not isinstance(payload, dict): raise ProviderError(f"recorded research evidence missing for {subject_id}")
        observed_at = payload.get("observed_at") or utc_now()
        return _artifact(self.provider_name, "RESEARCH_EVIDENCE", {"subject_id": subject_id, "query": query, "source": payload, "observed_at": observed_at}, subject_id)


class ConfiguredResearchEvidenceProvider:
    """Configured non-SEC evidence transport.

    The runtime does not assume a vendor, endpoint shape, or search
    capability.  The operator supplies a verified JSON endpoint and the
    adapter requires the response to carry a source URL, source observation
    time, and non-empty content/evidence payload.  This keeps company IR,
    earnings, transcript, consensus, or search providers behind one strict
    boundary without treating LLM memory as evidence.
    """

    provider_name = "configured-research-http"

    _SENSITIVE_KEYS = {
        "authorization", "cookie", "set-cookie", "x-api-key", "api-key",
        "access-token", "access_token", "refresh-token", "refresh_token",
        "client-secret", "client_secret", "secret", "password", "token",
    }

    def __init__(self, base_url: str, path: str = "/", headers: dict[str, str] | None = None,
                 timeout: float = 20.0, max_retries: int = 2,
                 max_bytes: int = 2_000_000, allow_http_local_fixture: bool = False) -> None:
        if not base_url or not path:
            raise ValueError("configured research provider requires base_url and path")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("configured research base_url must be an http(s) URL")
        self._validate_url(base_url, allow_http_local_fixture=allow_http_local_fixture)
        self.base_url = base_url.rstrip("/")
        self._base_host = (urllib.parse.urlparse(self.base_url).hostname or "").casefold().rstrip(".")
        self.path = path
        self.headers = {key: value for key, value in dict(headers or {}).items() if str(key).casefold() not in self._SENSITIVE_KEYS}
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0:
            raise ValueError("configured research max_bytes must be positive")
        self.allow_http_local_fixture = bool(allow_http_local_fixture)

    @classmethod
    def _validate_url(cls, url: str, *, allow_http_local_fixture: bool = False) -> None:
        parsed = urllib.parse.urlparse(str(url))
        if parsed.scheme != "https":
            local_ok = allow_http_local_fixture and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if not local_ok:
                raise ProviderError("non-SEC research requires HTTPS outside an explicitly trusted local fixture")
        host = (parsed.hostname or "").casefold().rstrip(".")
        if not host or host in {"localhost", "metadata.google.internal", "metadata.google"} or host.endswith(".local"):
            raise ProviderError("private/local research endpoint is not allowed in production")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
            raise ProviderError("private research endpoint is not allowed in production")

    @classmethod
    def _sanitize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): "[REDACTED]" if str(key).casefold() in cls._SENSITIVE_KEYS or any(token in str(key).casefold() for token in ("authorization", "cookie", "api_key", "apikey")) else cls._sanitize(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [cls._sanitize(child) for child in value]
        return value

    def fetch(self, subject_id: str, query: dict[str, Any]) -> RawArtifact:
        params = {**(query or {}), "subject_id": subject_id}
        query_string = urllib.parse.urlencode(params, doseq=True)
        url = f"{self.base_url}/{self.path.lstrip('/')}" + (f"?{query_string}" if query_string else "")
        self._validate_url(url, allow_http_local_fixture=self.allow_http_local_fixture)
        request = urllib.request.Request(url, headers={"Accept": "application/json", **self.headers})
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    try:
                        raw = response.read(self.max_bytes + 1)
                    except TypeError:  # compatibility with minimal test doubles
                        raw = response.read()
                    if len(raw) > self.max_bytes:
                        raise ProviderError("research provider response exceeds configured size limit")
                    final_url = _response_final_url(response, url)
                    self._validate_url(str(final_url), allow_http_local_fixture=self.allow_http_local_fixture)
                    final_host = (urllib.parse.urlparse(str(final_url)).hostname or "").casefold().rstrip(".")
                    if not _same_or_subdomain(final_host, self._base_host):
                        raise ProviderError("research provider redirect crossed configured host boundary")
                    payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ProviderError("research provider response is not an object")
                source = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else payload
                content = source.get("content") or source.get("text") or source.get("summary") or source.get("items")
                source_url = source.get("source_url") or source.get("url") or source.get("canonical_url")
                source_observed_at = source.get("source_observed_at") or source.get("published_at") or source.get("observed_at") or source.get("as_of")
                if content in (None, "", [], {}):
                    raise ProviderError("research provider returned empty evidence content")
                if not source_url:
                    raise ProviderError("research evidence must include source_url")
                self._validate_url(str(source_url), allow_http_local_fixture=self.allow_http_local_fixture)
                if not source_observed_at:
                    raise ProviderError("research evidence must include source_observed_at/published_at")
                normalized = {
                    "subject_id": subject_id,
                    "provider": self.provider_name,
                    "evidence_type": str(source.get("evidence_type") or "NON_SEC_RESEARCH"),
                    "source_class": str(source.get("source_class") or "NON_SEC_RESEARCH"),
                    "source_url": str(source_url),
                    "source_observed_at": str(source_observed_at),
                    "content": content,
                    "title": source.get("title"),
                    "source_type": source.get("source_type") or "NON_SEC_RESEARCH",
                    "provider_payload": self._sanitize(payload),
                }
                return _artifact(self.provider_name, "RESEARCH_EVIDENCE", normalized, subject_id, source_observed_at=str(source_observed_at), infer_source=False)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= self.max_retries:
                    raise ProviderError(f"research provider HTTP {exc.code}") from exc
                last = exc
                time.sleep(min(2.0, 0.25 * (2 ** attempt)))
            except ProviderError as exc:
                # Schema/provenance failures are deterministic and must not
                # be retried as if they were transient transport failures.
                raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt >= self.max_retries:
                    raise ProviderError(f"research provider request failed: {exc}") from exc
                last = exc
                time.sleep(min(2.0, 0.25 * (2 ** attempt)))
        raise ProviderError(f"research provider request exhausted retries: {last}")


class _IssuerIRHTMLParser(HTMLParser):
    """Small, dependency-free parser for public issuer pages.

    The parser deliberately extracts only publication metadata and visible
    text.  It does not execute scripts, follow links, or interpret provider
    sentiment/verification labels as facts.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.visible_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.times: list[str] = []
        self.json_ld: list[str] = []
        self._title = False
        self._script: str | None = None
        self._time = False
        self._time_value: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {str(k).casefold(): v for k, v in attrs}
        lower = tag.casefold()
        if lower == "title":
            self._title = True
        elif lower == "script":
            # Never treat executable JavaScript (which may contain public
            # API/configuration strings) as source evidence.  JSON-LD is
            # retained only for publication metadata.
            self._script = "" if str(attrs_dict.get("type") or "").casefold() == "application/ld+json" else "__NON_JSON_SCRIPT__"
        elif lower == "style":
            self._script = "__NON_JSON_STYLE__"
        elif lower == "time":
            self._time = True
            self._time_value = attrs_dict.get("datetime")
        elif lower == "meta":
            key = str(attrs_dict.get("property") or attrs_dict.get("name") or "").casefold()
            value = attrs_dict.get("content")
            if key and value:
                self.meta[key] = str(value).strip()

    def handle_endtag(self, tag: str) -> None:
        lower = tag.casefold()
        if lower == "title":
            self._title = False
        elif lower == "script" and self._script is not None:
            if self._script != "__NON_JSON_SCRIPT__":
                self.json_ld.append(self._script)
            self._script = None
        elif lower == "style":
            self._script = None
        elif lower == "time":
            if self._time_value:
                self.times.append(self._time_value)
            self._time = False
            self._time_value = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            if self._script not in {"__NON_JSON_SCRIPT__", "__NON_JSON_STYLE__"}:
                self._script += data
            return
        text = unescape(str(data)).strip()
        if not text:
            return
        if self._title:
            self.title_parts.append(text)
        if not self._time and len(text) <= 5000:
            self.visible_parts.append(text)


class IssuerIRWebEvidenceProvider:
    """Fetch and normalize explicitly configured public issuer web evidence.

    This is a direct, read-only adapter rather than a search engine.  Each
    subject must have an operator-supplied, issuer-owned HTTPS URL and an
    allowlisted host.  Consequently a caller cannot turn this adapter into an
    arbitrary URL fetcher or silently substitute a foreign issuer page.
    """

    provider_name = "issuer-ir-html"
    _DATE_RE = re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},\s+\d{4}\b", re.IGNORECASE,
    )
    _SECRET_RE = re.compile(
        r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}|(?:api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*[A-Za-z0-9._~+/=-]{8,})"
    )

    def __init__(self, sources: dict[str, dict[str, Any]], *, timeout: float = 30.0,
                 max_bytes: int = 4_000_000, user_agent: str = "StockAgent/1.1 research") -> None:
        if not isinstance(sources, dict) or not sources:
            raise ValueError("issuer IR provider requires explicit issuer source configuration")
        self.sources: dict[str, dict[str, Any]] = {}
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0:
            raise ValueError("issuer IR max_bytes must be positive")
        self.user_agent = str(user_agent)
        for subject_id, raw in sources.items():
            if not isinstance(raw, dict) or not raw.get("source_url"):
                raise ValueError(f"issuer IR source for {subject_id} requires source_url")
            source = dict(raw)
            url = str(source["source_url"])
            self._validate_url(url)
            hosts = source.get("allowed_hosts") or ([urllib.parse.urlparse(url).hostname] if urllib.parse.urlparse(url).hostname else [])
            normalized_hosts = {str(host).casefold().rstrip(".") for host in hosts if host}
            if not normalized_hosts:
                raise ValueError(f"issuer IR source for {subject_id} requires allowed host")
            source["allowed_hosts"] = sorted(normalized_hosts)
            markers = source.get("issuer_markers") or [str(subject_id).casefold()]
            source["issuer_markers"] = [str(marker).casefold() for marker in markers if str(marker).strip()]
            self.sources[str(subject_id).upper()] = source

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urllib.parse.urlparse(str(url))
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ProviderError("issuer IR source must be an HTTPS URL without embedded credentials")
        host = (parsed.hostname or "").casefold().rstrip(".")
        if not host or host in {"localhost", "metadata.google.internal", "metadata.google"} or host.endswith(".local"):
            raise ProviderError("issuer IR source host is private/local")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
            raise ProviderError("issuer IR source host is private/reserved")

    @staticmethod
    def _host_allowed(url: str, allowed_hosts: list[str]) -> bool:
        host = (urllib.parse.urlparse(str(url)).hostname or "").casefold().rstrip(".")
        return any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts)

    @classmethod
    def _json_ld_values(cls, value: Any, key: str) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for name, child in value.items():
                if str(name).casefold() == key.casefold() and child not in (None, ""):
                    found.append(str(child))
                found.extend(cls._json_ld_values(child, key))
        elif isinstance(value, list):
            for child in value:
                found.extend(cls._json_ld_values(child, key))
        return found

    @classmethod
    def _parse_source_time(cls, parser: _IssuerIRHTMLParser) -> datetime | None:
        candidates: list[str] = []
        for key in ("article:published_time", "datepublished", "publishdate", "date"):
            if parser.meta.get(key):
                candidates.append(parser.meta[key])
        candidates.extend(parser.times)
        for raw in parser.json_ld:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            candidates.extend(cls._json_ld_values(payload, "datePublished"))
        # Investor-relations templates often place the date deep in page
        # chrome (well after the title), while company blogs put it in the
        # article lead.  The parser already bounds visible text, so searching
        # the complete normalized text is deterministic and size-safe.
        candidates.extend(cls._DATE_RE.findall(" ".join(parser.visible_parts)))
        for candidate in candidates:
            parsed = _parse_observation_time(candidate)
            if parsed is None:
                try:
                    parsed = datetime.strptime(str(candidate).strip(), "%B %d, %Y").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise ProviderError("issuer IR source_observed_at is in the future")
            return parsed.replace(microsecond=0)
        return None

    @classmethod
    def _title_and_body(cls, parser: _IssuerIRHTMLParser) -> tuple[str, str]:
        title = " ".join(parser.title_parts).strip()
        body = " ".join(parser.visible_parts)
        # Collapse page chrome while retaining source text and punctuation.
        body = re.sub(r"\s+", " ", body).strip()
        body = cls._SECRET_RE.sub("[REDACTED]", body)
        return title[:1000], body[:120_000]

    def fetch(self, subject_id: str, query: dict[str, Any] | None = None) -> RawArtifact:
        sid = str(subject_id).upper()
        source = self.sources.get(sid)
        if source is None:
            raise ProviderError(f"issuer IR source is not configured for {sid}")
        source_url = str(source["source_url"])
        allowed_hosts = list(source["allowed_hosts"])
        self._validate_url(source_url)
        if not self._host_allowed(source_url, allowed_hosts):
            raise ProviderError("issuer IR source URL is outside its configured host allowlist")
        request = urllib.request.Request(source_url, headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise ProviderError("issuer IR response exceeds configured size limit")
                final_url = _response_final_url(response, source_url)
        except ProviderError:
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"issuer IR request failed for {sid}: {exc}") from exc
        self._validate_url(str(final_url))
        if not self._host_allowed(str(final_url), allowed_hosts):
            raise ProviderError("issuer IR redirect crossed issuer host boundary")
        try:
            parser = _IssuerIRHTMLParser()
            parser.feed(raw.decode("utf-8", errors="replace"))
            parser.close()
        except Exception as exc:
            raise ProviderError(f"issuer IR HTML parse failed for {sid}: {exc}") from exc
        observed = self._parse_source_time(parser)
        if observed is None:
            raise ProviderError("issuer IR page lacks a publication/observation timestamp")
        title, content = self._title_and_body(parser)
        markers = source["issuer_markers"]
        identity_text = f"{title} {content}".casefold()
        if not markers or not any(marker in identity_text for marker in markers):
            raise ProviderError("issuer IR page does not identify the configured issuer")
        if not content:
            raise ProviderError("issuer IR page returned empty visible content")
        observed_text = observed.isoformat().replace("+00:00", "Z")
        payload = {
            "security_id": sid,
            "evidence_type": str(source.get("evidence_type") or "COMPANY_IR_NEWS"),
            "source_class": str(source.get("source_class") or "COMPANY_IR"),
            "source_url": source_url,
            "source_observed_at": observed_text,
            "provider": self.provider_name,
            "title": title or str(source.get("title") or sid),
            "content": content,
        }
        content_hash = canonical_hash({"source_url": source_url, "content": content})
        payload["content_hash"] = content_hash
        payload["fetched_at"] = utc_now()
        payload["raw_artifact_id"] = f"artifact-ir-{canonical_hash({'source_url': source_url, 'content_hash': content_hash})[:32]}"
        payload_hash = canonical_hash(payload)
        return RawArtifact(
            artifact_id=payload["raw_artifact_id"], provider=self.provider_name,
            artifact_type="RESEARCH_EVIDENCE", subject_id=sid,
            observed_at=observed_text, payload=payload, payload_hash=payload_hash,
            source_observed_at=observed_text, retrieved_at=payload["fetched_at"],
        )

    @staticmethod
    def evidence_from_artifact(artifact: RawArtifact, *, epoch: int = 0) -> Evidence:
        payload = artifact.payload
        if canonical_hash(payload) != artifact.payload_hash:
            raise ProviderError("issuer IR RawArtifact payload hash mismatch")
        required = ("security_id", "evidence_type", "source_class", "source_url", "source_observed_at", "provider", "title", "content", "raw_artifact_id", "content_hash")
        if any(key not in payload or payload[key] in (None, "") for key in required):
            raise ProviderError("issuer IR artifact is missing normalized evidence fields")
        if payload["raw_artifact_id"] != artifact.artifact_id or payload["content_hash"] != canonical_hash({"source_url": payload["source_url"], "content": payload["content"]}):
            raise ProviderError("issuer IR artifact hash/receipt lineage mismatch")
        IssuerIRWebEvidenceProvider._validate_url(str(payload["source_url"]))
        return Evidence(
            evidence_id=f"E-{artifact.artifact_id}", subject_id=str(payload["security_id"]),
            source_class=str(payload["source_class"]), observed_at=str(payload["source_observed_at"]),
            epoch=int(epoch), payload_hash=artifact.payload_hash, grade="RAW_SOURCE", status="ACTIVE",
            raw_artifact_id=artifact.artifact_id,
        )


class YahooFinanceNewsEvidenceProvider:
    """Read-only, secondary news discovery for otherwise unconfigured issuers.

    This adapter is intentionally classified as ``MAJOR_MEDIA``.  It never
    upgrades a news item to issuer/official evidence; it only prevents a broad
    live universe from silently losing every newly discovered ticker when the
    operator has not yet configured an issuer IR URL.  The feed is queried for
    one ticker, and the selected article must identify that ticker in its own
    title/description before it is accepted.
    """

    provider_name = "yahoo-finance-news"
    BASE_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"

    def __init__(self, *, timeout: float = 20.0, max_bytes: int = 1_000_000,
                 user_agent: str = "StockAgent/1.1 research") -> None:
        self.timeout = max(1.0, float(timeout))
        self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0:
            raise ValueError("Yahoo news max_bytes must be positive")
        self.user_agent = str(user_agent)
        IssuerIRWebEvidenceProvider._validate_url(self.BASE_URL)

    @staticmethod
    def _parse_date(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        for parser in (
            lambda: email.utils.parsedate_to_datetime(text),
            lambda: datetime.fromisoformat(text.replace("Z", "+00:00")),
        ):
            try:
                result = parser()
            except (TypeError, ValueError, OverflowError):
                continue
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
            return result.astimezone(timezone.utc).replace(microsecond=0)
        return None

    def fetch(self, subject_id: str, query: dict[str, Any] | None = None) -> RawArtifact:
        sid = str(subject_id).upper().strip()
        if not sid or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sid):
            raise ProviderError("Yahoo news ticker is malformed")
        params = urllib.parse.urlencode({"s": sid, "region": "US", "lang": "en-US"})
        feed_url = f"{self.BASE_URL}?{params}"
        request = urllib.request.Request(feed_url, headers={"Accept": "application/rss+xml,application/xml", "User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise ProviderError("Yahoo news response exceeds configured size limit")
                final_url = _response_final_url(response, feed_url)
        except ProviderError:
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"Yahoo news request failed for {sid}: {exc}") from exc
        IssuerIRWebEvidenceProvider._validate_url(str(final_url))
        if not IssuerIRWebEvidenceProvider._host_allowed(str(final_url), ["feeds.finance.yahoo.com"]):
            raise ProviderError("Yahoo news redirect crossed configured host boundary")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ProviderError("Yahoo news RSS payload is malformed") from exc
        chosen: tuple[str, str, str, datetime] | None = None
        for item in root.findall(".//item"):
            title = str(item.findtext("title") or "").strip()
            link = str(item.findtext("link") or "").strip()
            description = str(item.findtext("description") or "").strip()
            published = self._parse_date(str(item.findtext("pubDate") or item.findtext("published") or ""))
            if not title or not link or published is None:
                continue
            if published > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise ProviderError("Yahoo news publication timestamp is in the future")
            IssuerIRWebEvidenceProvider._validate_url(link)
            if not IssuerIRWebEvidenceProvider._host_allowed(link, ["finance.yahoo.com", "finance.yahoo.com"]):
                continue
            identity_text = f"{title} {description}".casefold()
            if sid.casefold() not in identity_text:
                continue
            chosen = (title, link, description, published)
            break
        if chosen is None:
            raise ProviderError(f"Yahoo news feed has no issuer-identifiable article for {sid}")
        title, source_url, description, published = chosen
        content = re.sub(r"\s+", " ", f"{title}. {description}").strip()[:120_000]
        published_text = published.isoformat().replace("+00:00", "Z")
        content_hash = canonical_hash({"source_url": source_url, "content": content})
        fetched_at = utc_now()
        payload = {
            "security_id": sid,
            "evidence_type": "NEWS_ARTICLE",
            "source_class": "MAJOR_MEDIA",
            "source_url": source_url,
            "source_observed_at": published_text,
            "provider": self.provider_name,
            "title": title[:1000],
            "content": content,
            "content_hash": content_hash,
            "feed_url": feed_url,
            "fetched_at": fetched_at,
            "raw_artifact_id": f"artifact-yahoo-news-{canonical_hash({'source_url': source_url, 'content_hash': content_hash})[:32]}",
        }
        return RawArtifact(
            payload["raw_artifact_id"], self.provider_name, "RESEARCH_EVIDENCE", sid,
            published_text, payload, canonical_hash(payload), published_text, fetched_at,
        )


class CompositeResearchEvidenceProvider:
    """Prefer issuer IR, then discover a real secondary article, fail closed."""

    provider_name = "composite-research"

    def __init__(self, issuer_provider: IssuerIRWebEvidenceProvider,
                 secondary_provider: YahooFinanceNewsEvidenceProvider | None = None) -> None:
        self.issuer_provider = issuer_provider
        self.secondary_provider = secondary_provider or YahooFinanceNewsEvidenceProvider()

    def fetch(self, subject_id: str, query: dict[str, Any] | None = None) -> RawArtifact:
        try:
            return self.issuer_provider.fetch(subject_id, query or {})
        except ProviderError as primary_error:
            try:
                return self.secondary_provider.fetch(subject_id, query or {})
            except ProviderError as secondary_error:
                raise ProviderError(
                    f"research sources unavailable for {str(subject_id).upper()}: "
                    f"issuer_ir={str(primary_error)[:100]}; secondary={str(secondary_error)[:100]}"
                ) from secondary_error


class UnavailableResearchEvidenceProvider:
    """Explicit fail-closed placeholder for non-SEC research/search sources."""

    provider_name = "unavailable-research"

    def fetch(self, subject_id: str, query: dict[str, Any]) -> RawArtifact:
        raise ProviderError("non-SEC research/search provider is not configured")


class FilesystemObsidianProjector:
    """Atomic Markdown projection for local Obsidian vault integration."""

    FORMAT_VERSION = 2

    def __init__(self, vault_root: str | Path) -> None:
        try:
            self._vault = SecureVault(vault_root)
        except VaultBoundaryError as exc:
            raise ProviderError(str(exc)) from exc
        self.vault_root = self._vault.root

    @staticmethod
    def _validate_run_id(run_id: str) -> str:
        value = str(run_id)
        if not value or "/" in value or "\\" in value or ".." in value:
            raise ProviderError("unsafe Obsidian run_id path component")
        return value

    @staticmethod
    def _safe_name(note_name: str) -> str:
        return "".join(char if char.isalnum() or char in "-_" else "_" for char in note_name).strip("_") or "run"

    def _relative_path(self, run_id: str, note_name: str) -> Path:
        return Path(f"{self._safe_name(note_name)}_{self._validate_run_id(run_id)}.md")

    @classmethod
    def _render(cls, run_id: str, document: dict[str, Any]) -> str:
        payload = json.dumps(document, ensure_ascii=False, indent=2)
        return (
            "---\n"
            f"run_id: {run_id}\n"
            "projection_only: true\n"
            f"projection_format_version: {cls.FORMAT_VERSION}\n"
            f"document_hash: {canonical_hash(document)}\n"
            "---\n\n"
            f"{payload}"
        )

    @classmethod
    def _canonical_existing(cls, value: str, run_id: str) -> bool:
        try:
            prefix, body = value.split("---\n\n", 1)
            metadata: dict[str, str] = {}
            for line in prefix.removeprefix("---\n").splitlines():
                key, separator, item = line.partition(":")
                if not separator:
                    return False
                metadata[key.strip()] = item.strip()
            document = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return False
        if not isinstance(document, dict):
            return False
        if metadata != {
            "run_id": str(run_id),
            "projection_only": "true",
            "projection_format_version": str(cls.FORMAT_VERSION),
            "document_hash": canonical_hash(document),
        }:
            return False
        return value == cls._render(str(run_id), document)

    def project(self, run_id: str, note_name: str, document: dict[str, Any]) -> Path:
        relative = self._relative_path(run_id, note_name)
        body = self._render(str(run_id), document)
        expected_hash: str | None = None
        try:
            target = self._vault.path(relative)
            if target.exists():
                existing = self._vault.read_text(relative)
                if existing == body:
                    return target
                if not self._canonical_existing(existing, str(run_id)):
                    raise ProjectionConflictError(
                        "Obsidian note was edited or is not canonical; status=CONFLICT",
                        status="CONFLICT",
                        retryable=False,
                    )
                expected_hash = content_digest(existing)
            result = self._vault.write_text(relative, body, expected_existing_hash=expected_hash)
            if self._vault.read_text(relative) != body:
                raise ProjectionError("projection verification failed; status=PARTIAL", status="PARTIAL", retryable=True)
            return result
        except ProjectionError:
            raise
        except VaultConflictError as exc:
            raise ProjectionConflictError(str(exc), status="CONFLICT", retryable=False) from exc
        except VaultIntegrityError as exc:
            status = "FAILED" if "status=FAILED" in str(exc) else "PARTIAL"
            raise ProjectionError(str(exc), status=status, retryable=True) from exc
        except VaultBoundaryError as exc:
            raise ProjectionError(str(exc), status="FAILED", retryable=False) from exc

    def read(self, run_id: str, note_name: str) -> str:
        try:
            return self._vault.read_text(self._relative_path(run_id, note_name))
        except VaultBoundaryError as exc:
            raise ProjectionError(str(exc), status="FAILED", retryable=False) from exc

    def verify(self, run_id: str, note_name: str, document: dict[str, Any]) -> bool:
        expected = self._render(self._validate_run_id(run_id), document)
        return self.read(run_id, note_name) == expected


class HttpJsonSECProvider:
    """Small SEC/EDGAR adapter boundary using only documented JSON resources.

    The endpoint is configurable and no response is interpreted as a gate.  A
    User-Agent is mandatory because SEC requests require an identifiable client.
    """

    provider_name = "sec-edgar-http"

    def __init__(self, base_url: str = "https://data.sec.gov", user_agent: str | None = None, timeout: float = 20.0, max_retries: int = 2, min_interval: float = 0.11, filing_base_url: str = "https://www.sec.gov", max_bytes: int = 12_000_000) -> None:
        if not user_agent or "@" not in user_agent or "example." in user_agent.lower():
            raise ValueError("SEC User-Agent must include a real contact address")
        self.base_url = base_url.rstrip("/")
        self._base_host = _validate_public_https_url(self.base_url, label="SEC data base_url")
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries)); self._limiter = _RateLimiter(min_interval); self.filing_base_url = filing_base_url.rstrip("/")
        self._filing_host = _validate_public_https_url(self.filing_base_url, label="SEC filing base_url")
        self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0:
            raise ValueError("SEC max_bytes must be positive")

    def _get(self, path: str, artifact_type: str, subject_id: str, base_url: str | None = None) -> RawArtifact:
        request_url = f"{(base_url or self.base_url).rstrip('/')}/{path.lstrip('/')}"
        configured_host = self._filing_host if base_url else self._base_host
        if _validate_public_https_url(request_url, label="SEC request") != configured_host:
            raise ProviderError("SEC request crossed configured host boundary")
        request = urllib.request.Request(request_url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        for attempt in range(self.max_retries + 1):
            self._limiter.wait()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    try:
                        raw = response.read(self.max_bytes + 1)
                    except TypeError:
                        raw = response.read()
                    if len(raw) > self.max_bytes:
                        raise ProviderError("SEC response exceeds configured size limit")
                    final_url = _response_final_url(response, request_url)
                    if not _same_or_subdomain(_validate_public_https_url(str(final_url), label="SEC redirect"), configured_host):
                        raise ProviderError("SEC redirect crossed configured host boundary")
                    payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict): raise ProviderError("SEC response is not an object")
                payload = dict(payload)
                payload.setdefault("source_url", request_url)
                source_time = _sec_source_time(payload, artifact_type)
                return _artifact(self.provider_name, artifact_type, payload, subject_id, source_observed_at=source_time, infer_source=False)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= self.max_retries:
                    raise ProviderError(f"SEC HTTP {exc.code}") from exc
                time.sleep(min(2.0, 0.25 * (2 ** attempt)))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt >= self.max_retries: raise ProviderError(f"SEC request failed: {exc}") from exc
                time.sleep(min(2.0, 0.25 * (2 ** attempt)))
        raise ProviderError("SEC request exhausted retries")

    def resolve_cik(self, ticker: str) -> str:
        """Resolve ticker through SEC's published ticker mapping, not memory."""
        artifact = self._get("files/company_tickers_exchange.json", "SEC_TICKER_MAP", "ticker-map", base_url="https://www.sec.gov")
        rows = artifact.payload.get("data") or []
        target = str(ticker).upper()
        for row in rows:
            if isinstance(row, list) and len(row) >= 3 and str(row[2]).upper() == target:
                return str(row[0]).zfill(10)
        raise ProviderError(f"SEC ticker not found: {ticker}")

    @staticmethod
    def _normalized_cik(value: Any) -> str:
        digits = re.sub(r"\D", "", str(value or ""))
        return digits.zfill(10) if digits else ""

    def _assert_issuer_identity(self, payload: dict[str, Any], identity: dict[str, Any], artifact_type: str) -> None:
        """Reject an EDGAR response whose explicit issuer identity disagrees."""
        expected = self._normalized_cik(identity.get("cik"))
        if not expected:
            raise ProviderError("CIK is required for SEC identity validation")
        for key in ("cik", "cik_str", "cikNumber"):
            if payload.get(key) not in (None, "") and self._normalized_cik(payload.get(key)) != expected:
                raise ProviderError(f"SEC {artifact_type} CIK mismatch")
        if artifact_type == "SEC_SUBMISSIONS" and not (payload.get("name") or payload.get("filings")):
            raise ProviderError("SEC submissions issuer payload is empty")
        if artifact_type == "SEC_FACTS" and not payload.get("entityName") and not payload.get("facts"):
            raise ProviderError("SEC companyfacts issuer payload is empty")

    def fetch_submissions(self, identity: dict[str, Any]) -> RawArtifact:
        cik = str(identity.get("cik") or "").zfill(10)
        if not cik.strip("0"):
            raise ProviderError("CIK is required for submissions")
        artifact = self._get(f"submissions/CIK{cik}.json", "SEC_SUBMISSIONS", identity.get("security_id") or cik)
        self._assert_issuer_identity(artifact.payload, identity, artifact.artifact_type)
        payload = {**artifact.payload, "cik": cik}
        return RawArtifact(artifact.artifact_id, artifact.provider, artifact.artifact_type, artifact.subject_id, artifact.observed_at, payload, canonical_hash(payload), artifact.source_observed_at, artifact.retrieved_at)

    def fetch_facts(self, identity: dict[str, Any]) -> RawArtifact:
        cik = str(identity.get("cik") or "").zfill(10)
        if not cik.strip("0"):
            raise ProviderError("CIK is required for facts")
        artifact = self._get(f"api/xbrl/companyfacts/CIK{cik}.json", "SEC_FACTS", identity.get("security_id") or cik)
        self._assert_issuer_identity(artifact.payload, identity, artifact.artifact_type)
        payload = {**artifact.payload, "cik": cik}
        return RawArtifact(artifact.artifact_id, artifact.provider, artifact.artifact_type, artifact.subject_id, artifact.observed_at, payload, canonical_hash(payload), artifact.source_observed_at, artifact.retrieved_at)

    def fetch_filings(self, identity: dict[str, Any], query: dict[str, Any] | None = None) -> RawArtifact:
        submissions = self.fetch_submissions(identity)
        query = query or {}; recent = (submissions.payload.get("filings") or {}).get("recent") or {}
        accession = str(query.get("accession_number") or "")
        form = str(query.get("form") or "")
        if not accession:
            forms = [str(candidate) for candidate in (recent.get("form") or [])]
            accessions = recent.get("accessionNumber") or []
            primary_documents = recent.get("primaryDocument") or []
            # A submission's first recent row is often a Schedule 13G/13D
            # without a primary filing document.  Full-forensic callers need
            # a document-bearing issuer filing by default, not an arbitrary
            # ownership notice.  Prefer substantive forms while retaining a
            # deterministic fallback for issuers with unusual filing history.
            preferred_forms = {
                "10-Q": 0, "10-Q/A": 0, "10-K": 1, "10-K/A": 1,
                "20-F": 1, "20-F/A": 1, "40-F": 1, "40-F/A": 1,
                "8-K": 2, "8-K/A": 2, "6-K": 2, "6-K/A": 2,
                "S-1": 3, "S-1/A": 3, "S-3": 3, "S-3/A": 3,
                "424B3": 4, "424B4": 4, "424B5": 4,
                "F-1": 3, "F-1/A": 3, "F-3": 3, "F-3/A": 3,
            }
            indices = [idx for idx, candidate in enumerate(forms) if not form or candidate == form]
            indices.sort(key=lambda idx: (preferred_forms.get(forms[idx].upper(), 5), idx))
            for idx in indices:
                candidate_accession = str(accessions[idx]) if idx < len(accessions) else ""
                candidate_primary = str(primary_documents[idx]) if idx < len(primary_documents) else ""
                if candidate_accession and candidate_primary:
                    accession = candidate_accession
                    form = forms[idx]
                    break
            if not accession and indices:
                idx = indices[0]
                accession = str(accessions[idx]) if idx < len(accessions) else ""
                form = forms[idx]
        if not accession or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            return _artifact(self.provider_name, "SEC_FILINGS_INDEX", {"submissions": submissions.payload, "query": query, "document_status": "MISSING_ACCESSION"}, submissions.subject_id, source_observed_at=_sec_source_time(submissions.payload, "SEC_FILINGS_INDEX"), infer_source=False)
        accession_no_dash = accession.replace("-", "")
        cik = str(identity.get("cik") or "").zfill(10)
        primary = ""
        filing_date = ""
        report_date = ""
        for idx, candidate in enumerate(recent.get("accessionNumber") or []):
            if candidate == accession:
                primary = str((recent.get("primaryDocument") or [])[idx])
                filing_date = str((recent.get("filingDate") or [])[idx]) if idx < len(recent.get("filingDate") or []) else ""
                report_date = str((recent.get("reportDate") or [])[idx]) if idx < len(recent.get("reportDate") or []) else ""
                break
        if not primary or not re.fullmatch(r"[A-Za-z0-9_.-]+", primary):
            return _artifact(self.provider_name, "SEC_FILINGS_INDEX", {"submissions": submissions.payload, "query": query, "document_status": "MISSING_PRIMARY_DOCUMENT", "accession_number": accession, "form": form}, submissions.subject_id, source_observed_at=_sec_source_time(submissions.payload, "SEC_FILINGS_INDEX"), infer_source=False)
        url_path = f"Archives/edgar/data/{int(cik)}/{accession_no_dash}/{primary}"
        request = urllib.request.Request(f"{self.filing_base_url}/{url_path}", headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"})
        for attempt in range(self.max_retries + 1):
            self._limiter.wait()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    try:
                        raw_document = response.read(self.max_bytes + 1)
                    except TypeError:
                        raw_document = response.read()
                    if len(raw_document) > self.max_bytes:
                        raise ProviderError("SEC filing document exceeds configured size limit")
                    final_url = _response_final_url(response, request.full_url)
                    if not _same_or_subdomain(_validate_public_https_url(str(final_url), label="SEC filing redirect"), self._filing_host):
                        raise ProviderError("SEC filing redirect crossed configured host boundary")
                    document = raw_document.decode("utf-8", errors="replace")
                source_url = f"{self.filing_base_url}/{url_path}"
                if not document.strip():
                    raise ProviderError("SEC filing document is empty")
                identity_markers = re.findall(r"(?:central\s+index\s+key|cik)\s*[:#]?\s*(\d{7,10})", document[:200000], flags=re.I)
                if identity_markers and any(self._normalized_cik(marker) != self._normalized_cik(cik) for marker in identity_markers):
                    raise ProviderError("SEC filing document CIK mismatch")
                normalized = {
                    "accession_number": accession,
                    "form": form,
                    "filing_date": filing_date,
                    "report_date": report_date,
                    "primary_document": primary,
                    "url": source_url,
                    "source_url": source_url,
                    "document": document,
                    "document_hash": canonical_hash(document),
                    "cik": cik,
                    "source_submissions_artifact": submissions.artifact_id,
                }
                return _artifact(self.provider_name, "SEC_FILING_DOCUMENT", normalized, submissions.subject_id, source_observed_at=filing_date or _sec_source_time(submissions.payload, "SEC_FILINGS_INDEX"), infer_source=False)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                code = getattr(exc, "code", None)
                if (code not in (429, 500, 502, 503, 504)) or attempt >= self.max_retries: raise ProviderError(f"SEC filing request failed: {exc}") from exc
                time.sleep(min(2.0, 0.25 * (2 ** attempt)))
        raise ProviderError("SEC filing request exhausted retries")

    def fetch_required_filings(self, identity: dict[str, Any], forms: tuple[str, ...] | None = None) -> list[RawArtifact]:
        """Fetch a deterministic, document-backed representative filing set.

        The returned list preserves SEC's submission ordering and includes
        index artifacts for requested forms that are unavailable.  Callers
        must keep those index artifacts fail-closed; they are not full
        forensic documents.
        """
        requested = tuple(forms or ("10-K", "10-Q", "8-K", "S-3", "424B5", "424B3", "424B4", "S-8", "4", "144", "13D", "13G"))
        submissions = self.fetch_submissions(identity)
        recent = ((submissions.payload.get("filings") or {}).get("recent") or {})
        available = [str(item).upper() for item in (recent.get("form") or [])]
        result: list[RawArtifact] = []
        for form in requested:
            upper = form.upper()
            if upper not in available:
                result.append(_artifact(self.provider_name, "SEC_FILINGS_INDEX", {"source_url": submissions.payload.get("source_url"), "query": {"form": form}, "document_status": "FORM_NOT_AVAILABLE", "form": form}, submissions.subject_id, source_observed_at=_sec_source_time(submissions.payload, "SEC_FILINGS_INDEX"), infer_source=False))
                continue
            result.append(self.fetch_filings(identity, {"form": form}))
        return result

    def fetch_cheap_facts(self, identity: dict[str, Any], submissions: RawArtifact | None = None, facts: RawArtifact | None = None) -> RawArtifact:
        """Extract the canonical cheap-facts packet from SEC evidence.

        This is deliberately evidence-backed and conservative: a positive
        filing/XBRL signal can produce TRUE, a quantified runway can produce
        FALSE/TRUE, and all ambiguous absence remains UNKNOWN.  The gate then
        fails closed instead of treating missing disclosure as a clean
        capital structure.
        """
        submissions = submissions or self.fetch_submissions(identity)
        facts = facts or self.fetch_facts(identity)
        raw = facts.payload.get("facts") if isinstance(facts.payload, dict) else None
        evidence_ids = [submissions.artifact_id, facts.artifact_id]
        filing: RawArtifact | None = None
        try:
            filing = self.fetch_filings(identity)
            evidence_ids.append(filing.artifact_id)
        except ProviderError:
            filing = None
        text = str((filing.payload if filing else {}).get("document") or "").lower()
        recent = ((submissions.payload.get("filings") or {}).get("recent") or {}) if isinstance(submissions.payload, dict) else {}
        forms = [str(item).upper() for item in (recent.get("form") or [])]
        selected_form = str((filing.payload if filing else {}).get("form") or (forms[0] if forms else "")).upper()

        def fact_rows_for(tags: tuple[str, ...]) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            if not isinstance(raw, dict):
                return rows
            for namespace_name, namespace in raw.items():
                if not isinstance(namespace, dict):
                    continue
                for tag, node in namespace.items():
                    if not any(token.casefold() in str(tag).casefold() for token in tags) or not isinstance(node, dict):
                        continue
                    units = node.get("units") or {}
                    for unit_values in units.values() if isinstance(units, dict) else []:
                        for item in unit_values or []:
                            try:
                                value = float(item.get("val"))
                            except (TypeError, ValueError, AttributeError):
                                continue
                            if not math.isfinite(value):
                                continue
                            rows.append({
                                "value": value,
                                "unit": str(next((unit for unit, values_for_unit in units.items() if item in (values_for_unit or [])), "UNKNOWN")),
                                "start": item.get("start"),
                                "end": item.get("end"),
                                "filed": item.get("filed"),
                                "form": item.get("form"),
                                "frame": item.get("frame"),
                                "fp": item.get("fp"),
                                "accn": item.get("accn"),
                                "tag": str(tag),
                                "namespace": str(namespace_name),
                            })
            return rows

        def values_for(tags: tuple[str, ...]) -> list[float]:
            return [float(row["value"]) for row in fact_rows_for(tags)]

        def context_windows(pattern: str, radius: int = 260) -> list[str]:
            windows: list[str] = []
            for match in re.finditer(pattern, text, flags=re.I | re.S):
                start = max(0, match.start() - radius)
                end = min(len(text), match.end() + radius)
                windows.append(text[start:end])
            return windows

        historical_terms = r"(?:repaid|redeemed|extinguished|converted and settled|fully converted|no longer outstanding|terminated|expired|cancelled|exhausted)"

        def clean_current_window(windows: list[str], economic_pattern: str, current_pattern: str | None = None, historical_pattern: str = historical_terms) -> str | None:
            """Return one field-local current/economic text window.

            Historical language elsewhere in the filing must not contaminate a
            currently outstanding instrument.  Conversely, a keyword or XBRL
            tag without current economic terms never produces TRUE.
            """
            for window in windows:
                if historical_pattern and re.search(historical_pattern, window, flags=re.I | re.S):
                    continue
                if current_pattern and not re.search(current_pattern, window, flags=re.I | re.S):
                    continue
                if re.search(economic_pattern, window, flags=re.I | re.S):
                    return window
            return None

        matched_windows: dict[str, str] = {}

        def fact_signal(field: str, tags: tuple[str, ...], positive_regex: str) -> str:
            """Conservative field-local tri-state extraction."""
            nums = values_for(tags)
            if field == "toxic_convertible":
                windows = context_windows(r"(?:convertible|conversion price|debenture|toxic convertible)")
                economic_pattern = r"(?:toxic convertible|floating conversion|variable[- ](?:price )?conversion|reset (?:conversion|price)|down[- ]round|discount.{0,120}conversion|conversion price.{0,120}(?:discount|reset|floor))"
                current_pattern = r"(?:currently|presently|as of|remains?|still).{0,120}(?:outstanding|convertible|note|debenture)|(?:convertible|note|debenture).{0,120}outstanding"
                convertible_history = r"(?:convertible|note|debenture).{0,120}(?:repaid|redeemed|extinguished|fully converted|no longer outstanding|cancelled)|(?:repaid|redeemed|extinguished|fully converted|no longer outstanding|cancelled).{0,120}(?:convertible|note|debenture)"
                matched = clean_current_window(windows, economic_pattern, current_pattern, convertible_history)
                if matched:
                    matched_windows[field] = matched
                    return "TRUE"
                return "UNKNOWN"
            if field == "material_warrant":
                windows = context_windows(r"warrant(?:s)?")
                economic_pattern = r"(?:exercise price|warrant shares|shares issuable|potential dilution|to purchase common stock)"
                current_pattern = r"(?:currently|presently|as of|remain(?:s|ing)?|still).{0,120}(?:outstanding )?warrant|warrant.{0,120}outstanding"
                warrant_history = r"warrant(?:s)?.{0,120}(?:redeemed|expired|cancelled|exercised|no longer outstanding)|(?:redeemed|expired|cancelled|exercised|no longer outstanding).{0,120}warrant(?:s)?"
                matched = clean_current_window(windows, economic_pattern, current_pattern, warrant_history)
                if matched:
                    matched_windows[field] = matched
                    return "TRUE"
                return "UNKNOWN"
            # Non-instrument fallback remains conservative: structured numeric
            # evidence can establish FALSE, but bare filing keywords do not
            # establish a risky TRUE without field-specific economics.
            if nums and all(value <= 0 for value in nums):
                return "FALSE"
            return "UNKNOWN"

        def item(state: str, summary: str) -> dict[str, Any]:
            return {"state": state, "details": {"summary": summary, "evidence_ids": evidence_ids, "unknowns": [] if state != "UNKNOWN" else [summary]}, "evidence_ids": evidence_ids}

        # Restricted cash is not interchangeable with unrestricted cash for a
        # runway calculation.  Prefer canonical unrestricted tags and fail
        # closed when an issuer discloses only restricted cash.
        cash_rows = [
            row for row in fact_rows_for(("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents", "CashAndDueFromBanks"))
            if "restricted" not in str(row.get("tag") or "").casefold()
            and str(row.get("unit") or "").upper() == "USD"
        ]
        operating_cash_rows = [
            row for row in fact_rows_for(("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"))
            if str(row.get("unit") or "").upper() == "USD"
        ]
        cash_values = [float(row["value"]) for row in cash_rows]
        operating_cash_values = [float(row["value"]) for row in operating_cash_rows]
        filing_payload = filing.payload if filing else {}
        accession = str(filing_payload.get("accession_number") or "")
        selected_filing_date = str(filing_payload.get("filing_date") or "")
        selected_report_date = str(filing_payload.get("report_date") or "")
        if not selected_report_date:
            for idx, candidate_accession in enumerate(recent.get("accessionNumber") or []):
                if str(candidate_accession) == accession:
                    selected_report_date = str((recent.get("reportDate") or [])[idx]) if idx < len(recent.get("reportDate") or []) else ""
                    if not selected_filing_date:
                        selected_filing_date = str((recent.get("filingDate") or [])[idx]) if idx < len(recent.get("filingDate") or []) else ""
                    break
        selected_period_cash = [row for row in cash_rows if not selected_report_date or str(row.get("end") or "") == selected_report_date]
        selected_period_ocf = [row for row in operating_cash_rows if not selected_report_date or str(row.get("end") or "") == selected_report_date]
        if selected_filing_date:
            selected_period_cash = [row for row in selected_period_cash if not row.get("filed") or str(row.get("filed")) <= selected_filing_date]
            selected_period_ocf = [row for row in selected_period_ocf if not row.get("filed") or str(row.get("filed")) <= selected_filing_date]
        runway_coverage: dict[str, Any] = {
            "selected_report_date": selected_report_date or None,
            "selected_filing_date": selected_filing_date or None,
            "cash_rows": selected_period_cash[-5:] if selected_report_date else cash_rows[-5:],
            "operating_cash_rows": selected_period_ocf[-5:] if selected_report_date else operating_cash_rows[-5:],
        }
        comparable = []
        # Non-periodic filings (for example an S-3) do not establish a
        # reporting-period anchor.  Never pair the latest unrelated XBRL
        # cash and OCF rows in that case.
        if selected_report_date:
            for cash_row in selected_period_cash:
                for ocf_row in selected_period_ocf:
                    if cash_row.get("end") and ocf_row.get("end") and cash_row.get("end") == ocf_row.get("end"):
                        comparable.append((cash_row, ocf_row))
        if comparable:
            cash_row, ocf_row = sorted(comparable, key=lambda pair: (str(pair[0].get("filed") or ""), str(pair[0].get("end") or "")))[-1]
            cash = float(cash_row["value"])
            operating_cash = float(ocf_row["value"])
            if cash <= 0:
                runway_state, runway_summary = "UNKNOWN", "cash balance is non-positive"
            elif operating_cash >= 0:
                runway_state, runway_summary = "FALSE", "latest comparable operating cash flow is positive; no cash burn inferred"
            else:
                start = _parse_observation_time(str(ocf_row.get("start") or ""))
                end = _parse_observation_time(str(ocf_row.get("end") or ""))
                duration_days = (end - start).total_seconds() / 86400.0 if start and end and end > start else 365.0
                annual_burn = abs(operating_cash) * (365.0 / max(duration_days, 1.0))
                runway_months = cash / annual_burn * 12.0 if annual_burn > 0 else float("inf")
                runway_state = "TRUE" if runway_months <= 6 else "FALSE" if runway_months >= 12 else "UNKNOWN"
                runway_summary = f"quantified comparable-period cash runway months={runway_months:.2f}"
        elif cash_values and operating_cash_values:
            runway_state, runway_summary = "UNKNOWN", "cash and operating cash flow periods do not match"
        else:
            runway_state, runway_summary = "UNKNOWN", "cash runway facts are not jointly disclosed"

        atm_windows = context_windows(r"(?:at[- ]?the[- ]?market|\batm\b)", radius=320)
        active_atm_window = clean_current_window(
            atm_windows,
            # A generic future "at-the-market sales" mention is not an
            # active ATM program.  Require a program/agreement/offering/
            # facility noun plus an explicit remaining/available capacity
            # statement before classifying the field TRUE.
            r"(?:at[- ]the[- ]market(?:\s+sales)?\s+(?:agreement|program|offering|facility)|\batm\b\s+(?:sales\s+)?(?:agreement|program|offering|facility)).{0,220}(?:remaining|available|unused).{0,120}(?:capacity|proceeds|shares|amount)|(?:remaining|available|unused).{0,120}(?:capacity|proceeds|shares|amount).{0,220}(?:at[- ]the[- ]market(?:\s+sales)?\s+(?:agreement|program|offering|facility)|\batm\b\s+(?:sales\s+)?(?:agreement|program|offering|facility))",
            r"(?:currently|presently|as of|remain(?:s|ing)?|still|active|available|remaining).{0,120}(?:offering|program|sales agreement|sales agent|capacity|proceeds|shares|amount)",
        )
        if active_atm_window:
            matched_windows["active_atm"] = active_atm_window

        financing_windows = context_windows(r"(?:registered direct|private placement|public offering|securities purchase agreement|underwriting agreement|definitive agreement|committed financing)", radius=300)
        imminent_window = None
        for window in financing_windows:
            if re.search(historical_terms, window, flags=re.I | re.S):
                continue
            if re.search(r"(?:announced|entered into|executed|agreed to|will issue|will sell|priced).{0,180}(?:offering|financ|securities|shares|notes)|(?:registered direct|private placement|public offering).{0,180}(?:priced|shares|gross proceeds|closing)", window, flags=re.I | re.S):
                imminent_window = window
                break
        if imminent_window:
            matched_windows["imminent_financing"] = imminent_window

        shelf_window = None
        for window in context_windows(r"(?:shelf registration|universal shelf|base prospectus|registration statement.{0,80}(?:shelf|capacity))", radius=260):
            if re.search(r"(?:working capital need|liquidity need|fund operations|finance operations|raise capital)", window, flags=re.I | re.S):
                shelf_window = window
                break
        if shelf_window:
            matched_windows["large_shelf_and_financing_need"] = shelf_window

        states = {
            "active_atm": "TRUE" if active_atm_window else "UNKNOWN",
            "large_shelf_and_financing_need": "TRUE" if shelf_window else "UNKNOWN",
            "toxic_convertible": fact_signal("toxic_convertible", ("ConvertibleDebt", "ConvertibleNote", "ConvertiblePreferred", "ConvertibleInstrument"), r"toxic convertible|variable conversion|reset conversion|convertible note|convertible debenture"),
            "material_warrant": fact_signal("material_warrant", ("Warrant", "WarrantsOutstanding", "WarrantIssued"), r"warrant(s)? (to purchase|exercise price|outstanding)|pre[- ]?funded warrant"),
            "imminent_financing": "TRUE" if selected_form in {"S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR", "424B5", "424B3", "424B4", "8-K"} and imminent_window else "UNKNOWN",
            "cash_runway_critical": runway_state,
        }
        summaries = {"active_atm": "ATM language in filing", "large_shelf_and_financing_need": "shelf plus financing language", "toxic_convertible": "convertible debt/terms evidence", "material_warrant": "warrant facts/terms evidence", "imminent_financing": "recent offering/registration evidence", "cash_runway_critical": runway_summary}
        payload = {"extraction_status": "COMPLETE" if all(state != "UNKNOWN" for state in states.values()) else "PARTIAL" if isinstance(raw, dict) and raw else "INCOMPLETE", "identity_status": "CONFIRMED"}
        payload.update({key: item(state, summaries[key]) for key, state in states.items()})
        payload["evidence_ids"] = evidence_ids
        primary_document = str((filing.payload if filing else {}).get("primary_document") or "")
        coverage: dict[str, Any] = {}
        for key, state in states.items():
            matched = matched_windows.get(key, "")
            window_hash = canonical_hash({"field": key, "text_window": matched, "accession": accession}) if matched else None
            all_windows = context_windows({
                "active_atm": r"(?:at[- ]?the[- ]?market|\batm\b)",
                "large_shelf_and_financing_need": r"(?:shelf registration|universal shelf|base prospectus|registration statement.{0,80}(?:shelf|capacity))",
                "toxic_convertible": r"(?:convertible|conversion price|debenture|toxic convertible)",
                "material_warrant": r"warrant(?:s)?",
                "imminent_financing": r"(?:registered direct|private placement|public offering|securities purchase agreement|underwriting agreement|definitive agreement|committed financing)",
                "cash_runway_critical": r"(?:cash|operating activities|runway)",
            }[key])
            temporal_status = "CURRENT" if state == "TRUE" else "HISTORICAL" if any(re.search(historical_terms, window, flags=re.I | re.S) for window in all_windows) else "UNKNOWN"
            coverage[key] = {
                "state": state,
                "temporal_status": temporal_status,
                "sources": evidence_ids,
                "accession_number": accession,
                "form": selected_form,
                "primary_document": primary_document,
                "matched_section": "filing_text" if matched else "xbrl_facts_or_no_current_match",
                "matched_text_window_hash": window_hash,
                "matched_text_window": matched[:1200] if matched else None,
                "matched_text_window_length": len(matched),
                "extraction_rule_id": f"SEC_CHEAP_FACTS_{key.upper()}_V3",
                "extraction_version": "3",
                "forms_considered": forms[:20],
                "method": "FIELD_LOCAL_ECONOMIC_CONDITION_AND_FILING_TEXT",
                "capacity_observed": bool(active_atm_window) if key == "active_atm" else None,
            }
        payload["coverage"] = coverage
        payload["xbrl_period_coverage"] = runway_coverage
        payload["source_artifact_ids"] = evidence_ids
        payload["source_urls"] = [str(item.payload.get("source_url")) for item in (submissions, facts, filing) if item is not None and item.payload.get("source_url")]
        payload["filing_provenance"] = {
            "cik": str(identity.get("cik") or "").zfill(10),
            "accession_number": accession,
            "form": str((filing.payload if filing else {}).get("form") or ""),
            "filing_date": str((filing.payload if filing else {}).get("filing_date") or ""),
            "primary_document": primary_document,
            "document_hash": str((filing.payload if filing else {}).get("document_hash") or ""),
            "source_artifact_id": filing.artifact_id if filing else None,
        }
        payload["unknowns"] = [key for key, state in states.items() if state == "UNKNOWN"]
        payload["extraction_method"] = "SEC_XBRL_AND_FIELD_LOCAL_FILING_TEXT_V2"
        # This is a derived packet, not a newly observed source.  Preserve the
        # newest publication timestamp from the SEC inputs and keep retrieval
        # time separate; otherwise ``_artifact`` would incorrectly stamp the
        # packet with ``utc_now()`` and make fetched-at look like source time.
        source_candidates = [
            _parse_observation_time(item.source_observed_at)
            for item in (submissions, facts, filing)
            if item is not None and item.source_observed_at
        ]
        source_candidates = [item for item in source_candidates if item is not None]
        derived_source_time = None
        if source_candidates:
            derived_source_time = max(source_candidates).isoformat().replace("+00:00", "Z")
        return _artifact(
            self.provider_name,
            "SEC_CHEAP_FACTS",
            payload,
            identity.get("security_id"),
            source_observed_at=derived_source_time,
            infer_source=False,
        )
