from __future__ import annotations

"""Validated Security Master bootstrap and snapshot lifecycle.

The SEC ticker directory is deliberately treated as a listing baseline only.  It
does not establish that a symbol is a common stock, ETF, warrant, unit,
preferred security, or ADR.  This module composes that baseline with the
official Nasdaq Trader symbol directories and SEC submissions metadata while
preserving UNKNOWN whenever an authoritative classification is unavailable.
"""

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..capital_structure import sector_from_sic
from .schemas import SecurityMasterRecord
from .universe import InMemorySecurityMasterProvider, UniverseIntegrityEngine


IDENTITY_FLAGS = (
    "is_common_stock", "is_etf", "is_unit", "is_warrant", "is_preferred", "is_adr",
)
SECURITY_TYPES = {
    "COMMON_STOCK", "ETF", "UNIT", "WARRANT", "PREFERRED", "ADR", "UNKNOWN",
}
SUPPORTED_EXCHANGES = frozenset(UniverseIntegrityEngine.DEFAULT_EXCHANGES)


class SecurityMasterBootstrapError(RuntimeError):
    """A deterministic operational or snapshot validation failure."""

    def __init__(self, reason_code: str, message: str = ""):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


class SecurityMasterSnapshotValidationError(SecurityMasterBootstrapError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _checksum(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalise_exchange(value: Any) -> str:
    raw = str(value or "").strip().upper()
    aliases = {
        "NYSE MKT": "NYSE AMERICAN",
        "NYSE AMEX": "NYSE AMERICAN",
        "AMEX": "AMEX",
        "NYSE ARCA": "NYSE ARCA",
        "NASDAQ GLOBAL SELECT MARKET": "NASDAQ",
        "NASDAQ GLOBAL MARKET": "NASDAQ",
        "NASDAQ CAPITAL MARKET": "NASDAQ",
    }
    return aliases.get(raw, raw)


def _normalise_cik(value: Any) -> str:
    raw = re.sub(r"\D", "", str(value or ""))
    return raw.zfill(10) if raw else ""


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().upper()
    if text in {"Y", "YES", "TRUE", "1"}:
        return True
    if text in {"N", "NO", "FALSE", "0"}:
        return False
    return None


def _classification_from_name(name: str, etf_flag: bool | None) -> tuple[str, dict[str, bool | None]]:
    """Classify only explicit official security-name/type evidence.

    A missing type is intentionally not converted to common stock.  When a
    source explicitly identifies one mutually exclusive security type, the
    complementary flags are deterministic negatives from that same taxonomy;
    they are not inferred from ticker shape or issuer name.
    """
    text = re.sub(r"\s+", " ", str(name or "").strip().upper())
    if etf_flag is False and re.search(r"\bETF\b", text):
        # The same official row contains contradictory ETF evidence.  Keep the
        # entire identity unknown rather than selecting one field silently.
        return "UNKNOWN", {name: None for name in IDENTITY_FLAGS}
    category = ""
    if etf_flag is True or re.search(r"\bETF\b", text):
        category = "ETF"
    elif re.search(r"\bWARRANTS?\b", text):
        category = "WARRANT"
    elif re.search(r"\bUNITS?\b", text):
        category = "UNIT"
    elif re.search(r"\bPREFERRED\b|\bPREF(?:ERRED)?\.?\s+STOCK\b", text):
        category = "PREFERRED"
    elif re.search(r"\bADR\b|\bADRS\b|\bADS\b|AMERICAN DEPOSITARY", text):
        category = "ADR"
    elif re.search(r"\bCOMMON\s+(?:STOCK|SHARES?)\b", text):
        category = "COMMON_STOCK"

    flags: dict[str, bool | None] = {name: None for name in IDENTITY_FLAGS}
    if etf_flag is not None:
        flags["is_etf"] = etf_flag
    if category:
        flags = {name: False for name in IDENTITY_FLAGS}
        flags[f"is_{category.lower()}"] = True
        if category == "COMMON_STOCK":
            flags["is_common_stock"] = True
        elif category == "ETF":
            flags["is_etf"] = True
        elif category == "UNIT":
            flags["is_unit"] = True
        elif category == "WARRANT":
            flags["is_warrant"] = True
        elif category == "PREFERRED":
            flags["is_preferred"] = True
        elif category == "ADR":
            flags["is_adr"] = True
    return category or "UNKNOWN", flags


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Windows does not permit opening a directory with the same flags;
            # the atomic replace itself still protects the last-known-good file.
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict) and "payload" in payload and isinstance(payload["payload"], dict):
        return payload["payload"]
    return payload if isinstance(payload, dict) else None


def _write_json_cache(path: Path, payload: dict[str, Any], source: str, source_as_of: str) -> None:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    wrapper = {
        "cache_schema_version": "security_master_cache_v1",
        "source": source,
        "fetched_at": _now(),
        "source_as_of": source_as_of,
        "checksum": _checksum(raw),
        "payload": payload,
    }
    _atomic_write_bytes(path, json.dumps(wrapper, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))


class NasdaqTraderSecurityTypeProvider:
    """Parse official Nasdaq Trader Nasdaq/other-exchange symbol directories."""

    SOURCES = {
        "NASDAQ": "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
        "OTHER": "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt",
    }
    OTHER_EXCHANGES = {
        "A": "NYSE AMERICAN", "N": "NYSE", "P": "NYSE ARCA", "Z": "BATS", "V": "IEXG",
    }

    def __init__(self, cache_dir: str | Path, opener=None, user_agent: str = "StockAgent/0.6",
                 timeout: float = 20.0):
        self.cache_dir = Path(cache_dir)
        self.opener = opener or urllib.request.urlopen
        self.user_agent = user_agent or "StockAgent/0.6"
        self.timeout = timeout
        self.calls = 0

    def records(self, as_of: str, refresh: bool = False) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for source_name, url in self.SOURCES.items():
            raw_path = self.cache_dir / f"{source_name.lower()}_listed.txt"
            text, source_as_of = self._load_text(raw_path, url, as_of, refresh)
            output.extend(self._parse(text, source_name, url, source_as_of))
        return output

    def _load_text(self, path: Path, url: str, as_of: str, refresh: bool) -> tuple[str, str]:
        if path.is_file() and not refresh:
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig")
            metadata = _read_json_cache(path.with_suffix(path.suffix + ".meta.json")) or {}
            return text, str(metadata.get("source_as_of") or self._file_timestamp(text) or as_of)
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "text/plain"})
        self.calls += 1
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except (OSError, urllib.error.URLError) as exc:
            raise SecurityMasterBootstrapError("SECURITY_TYPE_SOURCE_UNAVAILABLE", str(exc)) from exc
        text = raw.decode("utf-8-sig")
        _atomic_write_bytes(path, raw)
        source_as_of = self._file_timestamp(text) or as_of
        _write_json_cache(path.with_suffix(path.suffix + ".meta.json"),
                          {"source_url": url, "source_as_of": source_as_of, "checksum": _checksum(raw)},
                          "NASDAQ_TRADER_SYMBOL_DIRECTORY", source_as_of)
        return text, source_as_of

    @staticmethod
    def _file_timestamp(text: str) -> str:
        match = re.search(r"File Creation Time:?\s*[:| ]\s*(\d{8}\d{2}:?\d{2})", text, re.IGNORECASE)
        return match.group(1) if match else ""

    def _parse(self, text: str, source_name: str, url: str, source_as_of: str) -> list[dict[str, Any]]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return []
        header = lines[0].split("|")
        output: list[dict[str, Any]] = []
        for raw in lines[1:]:
            columns = raw.split("|")
            if not columns or columns[0].lower().startswith("file creation time"):
                continue
            row = {name.strip(): columns[index].strip() if index < len(columns) else ""
                   for index, name in enumerate(header)}
            ticker = str(row.get("Symbol") or row.get("ACT Symbol") or "").strip().upper()
            if not ticker:
                continue
            name = str(row.get("Security Name") or "").strip()
            etf = _as_bool(row.get("ETF"))
            security_type, identity = _classification_from_name(name, etf)
            exchange = "NASDAQ" if source_name == "NASDAQ" else self.OTHER_EXCHANGES.get(
                str(row.get("Exchange") or "").strip().upper(), str(row.get("Exchange") or "").strip().upper())
            output.append({
                "ticker": ticker,
                "cik": _normalise_cik(row.get("CIK")),
                "company_name": name,
                "exchange": _normalise_exchange(exchange),
                "security_type": security_type,
                "identity": identity,
                "source": "NASDAQ_TRADER_SYMBOL_DIRECTORY",
                "source_url": url,
                "source_as_of": source_as_of,
                "source_name": source_name,
            })
        return output


class SECSubmissionsMetadataProvider:
    """Cached official SEC issuer metadata used for SIC/sector enrichment."""

    URL = "https://data.sec.gov/submissions/CIK{cik}.json"

    def __init__(self, user_agent: str, cache_dir: str | Path, opener=None,
                 max_requests: int = 10_000, max_rps: float = 4.0, timeout: float = 20.0,
                 max_attempts: int = 3):
        if not user_agent:
            raise SecurityMasterBootstrapError("SEC_USER_AGENT_REQUIRED")
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.opener = opener or urllib.request.urlopen
        self.max_requests = max(0, int(max_requests))
        self.max_rps = max(0.5, min(float(max_rps), 5.0))
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self.calls = 0
        self.failed = 0
        self.unmatched = 0
        self._last_request_at = 0.0

    def profiles(self, records: Iterable[SecurityMasterRecord], refresh: bool = False) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        for record in records:
            if record.cik in seen or not record.cik:
                continue
            seen.add(record.cik)
            payload = self.profile(record.cik, refresh=refresh)
            if payload is None:
                self.unmatched += 1
                continue
            result[record.cik] = {
                "cik": record.cik,
                "sic": str(payload.get("sic") or ""),
                "sic_description": str(payload.get("sicDescription") or ""),
                "company_name": str(payload.get("name") or ""),
                "source": "SEC_SUBMISSIONS",
                "source_url": self.URL.format(cik=record.cik),
                "source_as_of": str(payload.get("lastUpdate") or ""),
            }
        return result

    def profile(self, cik: str, refresh: bool = False) -> dict[str, Any] | None:
        normalized = _normalise_cik(cik)
        if not normalized:
            return None
        cache_path = self.cache_dir / f"CIK{normalized}.json"
        if cache_path.is_file() and not refresh:
            return _read_json_cache(cache_path)
        if self.max_requests and self.calls >= self.max_requests:
            return None
        self._throttle()
        request = urllib.request.Request(
            self.URL.format(cik=normalized),
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        for attempt in range(1, self.max_attempts + 1):
            self.calls += 1
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    if getattr(response, "status", 200) != 200:
                        raise SecurityMasterBootstrapError("SEC_SUBMISSIONS_HTTP_ERROR")
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise SecurityMasterBootstrapError("SEC_SUBMISSIONS_INVALID_JSON")
                source_as_of = str(payload.get("lastUpdate") or "")
                _write_json_cache(cache_path, payload, "SEC_SUBMISSIONS", source_as_of)
                return payload
            except SecurityMasterBootstrapError:
                self.failed += 1
                if attempt == self.max_attempts:
                    return None
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                self.failed += 1
                if attempt == self.max_attempts:
                    return None
            time.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))
        return None

    def _throttle(self) -> None:
        interval = 1.0 / self.max_rps
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_at = time.monotonic()


def _record_from_row(row: dict[str, Any]) -> SecurityMasterRecord:
    return SecurityMasterRecord(
        security_id=str(row.get("security_id") or f"SEC-{row.get('cik', '')}-{row.get('ticker', '')}"),
        ticker=str(row.get("ticker") or "").upper(),
        company_name=str(row.get("company_name") or ""),
        cik=_normalise_cik(row.get("cik")),
        exchange=_normalise_exchange(row.get("exchange")),
        security_type=str(row.get("security_type") or "UNKNOWN"),
        country=str(row.get("country") or "US").upper(),
        is_common_stock=row.get("is_common_stock"), is_etf=row.get("is_etf"),
        is_unit=row.get("is_unit"), is_warrant=row.get("is_warrant"),
        is_preferred=row.get("is_preferred"), is_adr=row.get("is_adr"),
        sector_canonical=str(row.get("sector_canonical") or "UNKNOWN"),
        industry_canonical=str(row.get("industry_canonical") or "UNKNOWN"),
        sic=str(row.get("sic") or ""), sic_description=str(row.get("sic_description") or ""),
        active_status=str(row.get("active_status") or "ACTIVE").upper(),
        source=str(row.get("source") or "SEC_DIRECTORY+VALIDATED_ENRICHMENT"),
        source_as_of=str(row.get("source_as_of") or ""),
        ingested_at=str(row.get("ingested_at") or ""),
        themes=tuple(row.get("themes") or ()),
    )


def validate_snapshot(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != "security_master_enrichment_v1":
        raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_SCHEMA_INVALID")
    rows = payload.get("records")
    if not isinstance(rows, list):
        raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_RECORDS_INVALID")
    seen_tickers: set[str] = set()
    seen_identity: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_RECORD_INVALID")
        ticker = str(row.get("ticker") or "").upper()
        raw_cik = str(row.get("cik") or "").strip()
        if raw_cik and (not raw_cik.isdigit() or len(raw_cik) > 10):
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_MALFORMED_CIK")
        cik = _normalise_cik(raw_cik)
        exchange = _normalise_exchange(row.get("exchange"))
        if not ticker:
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_EMPTY_TICKER")
        if ticker in seen_tickers:
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_DUPLICATE_TICKER")
        seen_tickers.add(ticker)
        if not exchange:
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_INVALID_EXCHANGE")
        if cik and not re.fullmatch(r"\d{10}", cik):
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_MALFORMED_CIK")
        identity_key = (cik, ticker)
        if identity_key in seen_identity:
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_DUPLICATE_IDENTITY")
        seen_identity.add(identity_key)
        true_flags = []
        states = row.get("identity_states") or {}
        provenance = row.get("provenance") or {}
        for flag in IDENTITY_FLAGS:
            value = row.get(flag)
            if value is not None and not isinstance(value, bool):
                raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_BOOLEAN_INVALID")
            if value is not None:
                if value is True:
                    true_flags.append(flag)
                state = states.get(flag, {}) if isinstance(states, dict) else {}
                source_rows = provenance.get(flag, []) if isinstance(provenance, dict) else []
                if state.get("state") != "KNOWN" or not source_rows:
                    raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_PROVENANCE_MISSING")
        if len(true_flags) > 1:
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_CONTRADICTORY_FLAGS")
        if not str(row.get("sector_canonical") or "").strip():
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_SECTOR_INVALID")
        security_type = str(row.get("security_type") or "UNKNOWN").upper()
        if security_type not in SECURITY_TYPES:
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_SECURITY_TYPE_INVALID")


class SecurityMasterBootstrapBuilder:
    """Build and atomically publish a validated, provenance-bearing snapshot."""

    def __init__(self, listing_provider, security_type_provider, sector_provider=None,
                 snapshot_path: str | Path = "data/discovery/security_master_enrichment.json",
                 raw_cache_dir: str | Path = "data/cache/discovery/security_master/raw",
                 normalized_cache_dir: str | Path = "data/cache/discovery/security_master/normalized",
                 supported_exchanges: Iterable[str] | None = None,
                 min_accepted: int = 1, min_identity_coverage_pct: float = 95.0,
                 min_sector_coverage_pct: float = 90.0):
        self.listing_provider = listing_provider
        self.security_type_provider = security_type_provider
        self.sector_provider = sector_provider
        self.snapshot_path = Path(snapshot_path)
        self.raw_cache_dir = Path(raw_cache_dir)
        self.normalized_cache_dir = Path(normalized_cache_dir)
        self.supported_exchanges = {str(item).upper() for item in (supported_exchanges or SUPPORTED_EXCHANGES)}
        self.min_accepted = int(min_accepted)
        self.min_identity_coverage_pct = float(min_identity_coverage_pct)
        self.min_sector_coverage_pct = float(min_sector_coverage_pct)

    def build(self, as_of: str | None = None, refresh: bool = False) -> dict[str, Any]:
        as_of = as_of or _now()
        baseline = self._records(self.listing_provider, as_of, refresh)
        if not baseline:
            raise SecurityMasterBootstrapError("SEC_DIRECTORY_EMPTY")
        type_rows = self._records(self.security_type_provider, as_of, refresh)
        type_groups = self._join_type_rows(baseline, type_rows)

        prelim_records = []
        for base in baseline:
            group = type_groups.get(base.ticker, [])
            merged = self._merge_type_group(group)
            prelim_records.append(self._row_from_base(base, merged, as_of))

        sector_records = [
            _record_from_row(row) for row in prelim_records
            if row["exchange"] in self.supported_exchanges
            and row.get("is_common_stock") is True
            and all(row.get(flag) is not None for flag in IDENTITY_FLAGS)
        ]
        issuer_profiles: dict[str, dict[str, Any]] = {}
        if self.sector_provider is not None and sector_records:
            issuer_profiles = self.sector_provider.profiles(sector_records, refresh=refresh)

        rows: list[dict[str, Any]] = []
        for row in prelim_records:
            profile = issuer_profiles.get(_normalise_cik(row.get("cik")), {})
            sic = str(profile.get("sic") or row.get("sic") or "")
            sic_description = str(profile.get("sic_description") or row.get("sic_description") or "")
            sector = sector_from_sic(sic) if sic else "UNKNOWN"
            if sector == "UNKNOWN" and row.get("sector_canonical") not in {None, "UNKNOWN"}:
                sector = str(row["sector_canonical"])
            row["sic"] = sic
            row["sic_description"] = sic_description
            row["sector_canonical"] = sector
            row["industry_canonical"] = sector if sector != "UNKNOWN" else "UNKNOWN"
            row.setdefault("provenance", {})["sector_canonical"] = ([{
                "source": profile.get("source", "SEC_SUBMISSIONS"),
                "source_url": profile.get("source_url", ""),
                "source_as_of": profile.get("source_as_of", as_of),
                "sic": sic,
            }] if sic else [])
            rows.append(row)

        payload = {
            "schema_version": "security_master_enrichment_v1",
            "generated_at": _now(),
            "source_as_of": as_of,
            "records": rows,
            "sources": self._source_metadata(as_of),
        }
        metrics = self._metrics(payload)
        payload["metrics"] = metrics
        validate_snapshot(payload)
        return payload

    def build_and_write(self, as_of: str | None = None, refresh: bool = False) -> dict[str, Any]:
        payload = self.build(as_of=as_of, refresh=refresh)
        # Re-validate at the publication boundary as well.  This protects the
        # last-known-good snapshot even if a custom/injected builder returns a
        # malformed payload.
        validate_snapshot(payload)
        normalized = self.normalized_cache_dir / "security_master_normalized.json"
        normalized_payload = {
            "schema_version": "security_master_normalized_v1",
            "source": "SEC_DIRECTORY+NASDAQ_TRADER+SEC_SUBMISSIONS",
            "fetched_at": payload["generated_at"],
            "generated_at": payload["generated_at"],
            "source_as_of": payload["source_as_of"],
            "checksum": _checksum(json.dumps(payload["records"], sort_keys=True, ensure_ascii=False).encode("utf-8")),
            "records": payload["records"],
            "metrics": payload["metrics"],
        }
        _atomic_write_bytes(
            normalized,
            json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        )
        _atomic_write_bytes(
            self.snapshot_path,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        )
        payload["snapshot_path"] = str(self.snapshot_path)
        payload["normalized_cache_path"] = str(normalized)
        payload["status"] = self._readiness_status(payload["metrics"])
        payload["reason_codes"] = self._readiness_reasons(payload["metrics"])
        return payload

    def refresh(self, as_of: str | None = None) -> dict[str, Any]:
        return self.build_and_write(as_of=as_of, refresh=True)

    def _records(self, provider, as_of: str, refresh: bool) -> list[Any]:
        try:
            return list(provider.records(as_of, refresh=refresh))
        except TypeError:
            return list(provider.records(as_of))

    @staticmethod
    def _join_type_rows(baseline: list[SecurityMasterRecord], rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        by_cik_ticker: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        by_cik: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            ticker = str(row.get("ticker") or "").upper()
            cik = _normalise_cik(row.get("cik"))
            if not ticker:
                continue
            row = dict(row)
            row["ticker"] = ticker
            row["cik"] = cik
            by_ticker[ticker].append(row)
            if cik:
                by_cik_ticker[(cik, ticker)].append(row)
                by_cik[cik].append(row)
        joined: dict[str, list[dict[str, Any]]] = {}
        for base in baseline:
            candidates = by_cik_ticker.get((_normalise_cik(base.cik), base.ticker), []) if base.cik else []
            if not candidates and base.cik and len(by_cik.get(_normalise_cik(base.cik), [])) == 1:
                candidate = by_cik[_normalise_cik(base.cik)][0]
                if str(candidate.get("ticker") or "").upper() == base.ticker:
                    candidates = [candidate]
            if not candidates:
                ticker_candidates = by_ticker.get(base.ticker, [])
                # A source-provided CIK mismatch is an identity blocker, not
                # permission to fall back to a ticker-only join.  A ticker-only
                # join remains valid only when the source itself has no CIK.
                if base.cik and any(row.get("cik") and row.get("cik") != _normalise_cik(base.cik)
                                    for row in ticker_candidates):
                    candidates = []
                else:
                    candidates = ticker_candidates
            # Multiple official listings for the same symbol are allowed only
            # when their type classifications agree; _merge_type_group handles
            # conflicts as UNKNOWN rather than silently selecting one.
            joined[base.ticker] = candidates
        return joined

    @staticmethod
    def _merge_type_group(group: list[dict[str, Any]]) -> dict[str, Any]:
        if not group:
            return {"security_type": "UNKNOWN", "identity": {flag: None for flag in IDENTITY_FLAGS},
                    "provenance": {flag: [] for flag in IDENTITY_FLAGS}, "conflicted": False,
                    "sources": []}
        merged: dict[str, Any] = {
            "security_type": "UNKNOWN", "identity": {}, "provenance": {}, "conflicted": False,
            "sources": group,
        }
        for flag in IDENTITY_FLAGS:
            values = {row.get("identity", {}).get(flag) for row in group
                      if row.get("identity", {}).get(flag) is not None}
            if len(values) > 1:
                merged["identity"][flag] = None
                merged["conflicted"] = True
            elif values:
                merged["identity"][flag] = next(iter(values))
            else:
                merged["identity"][flag] = None
            merged["provenance"][flag] = [{
                "source": row.get("source", ""), "source_url": row.get("source_url", ""),
                "source_as_of": row.get("source_as_of", ""),
                "value": row.get("identity", {}).get(flag),
            } for row in group if row.get("identity", {}).get(flag) is not None]
        categories = {str(row.get("security_type") or "UNKNOWN") for row in group}
        known_categories = categories - {"UNKNOWN"}
        if len(known_categories) == 1:
            merged["security_type"] = next(iter(known_categories))
        elif len(known_categories) > 1:
            merged["security_type"] = "UNKNOWN"
            merged["conflicted"] = True
        return merged

    @staticmethod
    def _row_from_base(base: SecurityMasterRecord, merged: dict[str, Any], as_of: str) -> dict[str, Any]:
        identity = merged["identity"]
        conflict = bool(merged.get("conflicted"))
        states = {
            flag: {"state": "UNKNOWN_CONFLICTED" if conflict and identity.get(flag) is None
                   else "KNOWN" if identity.get(flag) is not None else "UNKNOWN_NOT_AVAILABLE"}
            for flag in IDENTITY_FLAGS
        }
        return {
            "security_id": base.security_id,
            "ticker": base.ticker,
            "company_name": base.company_name,
            "cik": _normalise_cik(base.cik),
            "exchange": _normalise_exchange(base.exchange),
            "country": base.country,
            "active_status": base.active_status,
            "security_type": merged.get("security_type", "UNKNOWN"),
            **identity,
            "sector_canonical": "UNKNOWN",
            "industry_canonical": "UNKNOWN",
            "sic": "",
            "sic_description": "",
            "source": "SEC_DIRECTORY+NASDAQ_TRADER",
            "source_as_of": as_of,
            "ingested_at": _now(),
            "identity_states": states,
            "provenance": merged.get("provenance", {}),
            "identity_conflicted": conflict,
            "identity_sources": [
                {"source": row.get("source", ""), "source_url": row.get("source_url", ""),
                 "source_as_of": row.get("source_as_of", "")}
                for row in merged.get("sources", [])
            ],
        }

    def _metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = payload["records"]
        records = [_record_from_row(row) for row in rows]
        supported = [row for row in records if row.exchange.upper() in self.supported_exchanges]
        identity_known_global = sum(self._identity_known(row) for row in records)
        identity_known_supported = sum(self._identity_known(row) for row in supported)
        conflict_count = sum(bool(row.get("identity_conflicted")) for row in rows)
        integrity = UniverseIntegrityEngine(exchanges=set(self.supported_exchanges)).build(
            InMemorySecurityMasterProvider(records), payload["source_as_of"])
        health = integrity["health"]
        accepted = integrity["records"]
        sector_known = sum(row.sector_canonical.upper() != "UNKNOWN" for row in accepted)
        raw_count = len(records)
        supported_count = len(supported)
        source_matches = sum(bool(row.get("identity_sources")) for row in rows)
        return {
            "raw_count": raw_count,
            "supported_exchange_scope_count": supported_count,
            "identity_known_global_count": identity_known_global,
            "identity_coverage_global_pct": round(identity_known_global / raw_count * 100, 4) if raw_count else 0.0,
            "identity_known_supported_count": identity_known_supported,
            "identity_coverage_supported_scope_pct": round(
                identity_known_supported / supported_count * 100, 4) if supported_count else 0.0,
            # Backward-compatible alias; readiness uses the explicit supported metric.
            "identity_coverage_pct": round(identity_known_global / raw_count * 100, 4) if raw_count else 0.0,
            "accepted_common_stock_count": len(accepted),
            "sector_known_count": sector_known,
            "sector_coverage_pct": round(sector_known / len(accepted) * 100, 4) if accepted else 0.0,
            "unknown_identity_count": sum(not self._identity_known(row) for row in records),
            "identity_conflict_count": conflict_count,
            "duplicate_count": health.get("duplicate_count", 0),
            "rejection_counts": dict(integrity.get("rejected", {})),
            "source_matches": source_matches,
            "source_unmatched": raw_count - source_matches,
            "source_conflicted": conflict_count,
            "security_type_source_calls": int(getattr(self.security_type_provider, "calls", 0) or 0),
            "sector_source_calls": int(getattr(self.sector_provider, "calls", 0) or 0),
            "sector_source_failed": int(getattr(self.sector_provider, "failed", 0) or 0),
            "sector_source_unmatched": int(getattr(self.sector_provider, "unmatched", 0) or 0),
            "security_master_ready": self._readiness_status({
                "accepted_common_stock_count": len(accepted),
                "identity_coverage_supported_scope_pct": round(
                    identity_known_supported / supported_count * 100, 4) if supported_count else 0.0,
                "sector_coverage_pct": round(sector_known / len(accepted) * 100, 4) if accepted else 0.0,
            }) == "SECURITY_MASTER_READY",
        }

    @staticmethod
    def _identity_known(record: SecurityMasterRecord) -> bool:
        return all(getattr(record, flag) is not None for flag in IDENTITY_FLAGS)

    def _readiness_status(self, metrics: dict[str, Any]) -> str:
        if (metrics.get("accepted_common_stock_count", 0) >= self.min_accepted
                and metrics.get("identity_coverage_supported_scope_pct", 0.0) >= self.min_identity_coverage_pct
                and metrics.get("sector_coverage_pct", 0.0) >= self.min_sector_coverage_pct):
            return "SECURITY_MASTER_READY"
        return "SECURITY_MASTER_COVERAGE_INSUFFICIENT"

    def _readiness_reasons(self, metrics: dict[str, Any]) -> list[str]:
        reasons = []
        if metrics.get("accepted_common_stock_count", 0) < self.min_accepted:
            reasons.append("SECURITY_MASTER_ACCEPTED_COUNT_INSUFFICIENT")
        if metrics.get("identity_coverage_supported_scope_pct", 0.0) < self.min_identity_coverage_pct:
            reasons.append("SECURITY_MASTER_COVERAGE_INSUFFICIENT")
        if metrics.get("sector_coverage_pct", 0.0) < self.min_sector_coverage_pct:
            reasons.append("SECURITY_MASTER_SECTOR_COVERAGE_INSUFFICIENT")
        return reasons

    def _source_metadata(self, as_of: str) -> dict[str, Any]:
        return {
            "baseline": {
                "source": "SEC_DIRECTORY",
                "source_url": getattr(self.listing_provider, "URL", ""),
                "source_as_of": as_of,
            },
            "security_type": {
                "source": "NASDAQ_TRADER_SYMBOL_DIRECTORY",
                "source_urls": list(getattr(self.security_type_provider, "SOURCES", {}).values()),
                "source_as_of": as_of,
            },
            "sector": {
                "source": "SEC_SUBMISSIONS",
                "source_url": getattr(self.sector_provider, "URL", "") if self.sector_provider else "",
                "source_as_of": as_of,
            },
        }


def snapshot_records(payload: dict[str, Any]) -> list[SecurityMasterRecord]:
    validate_snapshot(payload)
    return [_record_from_row(row) for row in payload.get("records", [])]


def read_snapshot(path: str | Path) -> dict[str, Any] | None:
    target = Path(path)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecurityMasterBootstrapError("SECURITY_MASTER_SNAPSHOT_UNREADABLE", str(exc)) from exc
    validate_snapshot(payload)
    payload["snapshot_path"] = str(target)
    payload["snapshot_file_mtime"] = datetime.fromtimestamp(target.stat().st_mtime, timezone.utc).isoformat()
    return payload


class SecurityMasterBootstrapService:
    """Configuration-bound service used by bootstrap/refresh/health CLI commands."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        discovery = config.get("discovery", {})
        bootstrap = discovery.get("bootstrap", {})
        credentials = config.get("credentials", {})
        self.user_agent = str(credentials.get("sec_user_agent", ""))
        self.snapshot_path = Path(bootstrap.get("security_master_enrichment_path", ""))
        self.raw_cache_dir = Path(bootstrap.get("raw_cache_dir", "data/cache/discovery/security_master/raw"))
        self.normalized_cache_dir = Path(bootstrap.get("normalized_cache_dir", "data/cache/discovery/security_master/normalized"))
        self.sector_cache_dir = Path(bootstrap.get("sector_cache_dir", str(self.raw_cache_dir / "sec_submissions")))
        self._builder: SecurityMasterBootstrapBuilder | None = None

    def builder(self) -> SecurityMasterBootstrapBuilder:
        if self._builder is not None:
            return self._builder
        if not self.user_agent:
            raise SecurityMasterBootstrapError("SEC_USER_AGENT_REQUIRED")
        from .providers_live import SECCompanyTickerSecurityMasterProvider
        listing = SECCompanyTickerSecurityMasterProvider(
            self.user_agent, self.raw_cache_dir / "company_tickers_exchange.json")
        security_types = NasdaqTraderSecurityTypeProvider(self.raw_cache_dir / "nasdaq_trader")
        sector = SECSubmissionsMetadataProvider(
            self.user_agent, self.sector_cache_dir,
            max_requests=int(self.config.get("discovery", {}).get("bootstrap", {}).get(
                "max_issuer_metadata_requests", 10_000)),
            max_rps=float(self.config.get("sec_max_rps", 4)))
        bootstrap = self.config.get("discovery", {}).get("bootstrap", {})
        self._builder = SecurityMasterBootstrapBuilder(
            listing, security_types, sector, self.snapshot_path, self.raw_cache_dir,
            self.normalized_cache_dir,
            supported_exchanges=UniverseIntegrityEngine.DEFAULT_EXCHANGES,
            min_accepted=int(bootstrap.get("min_accepted", 1)),
            min_identity_coverage_pct=float(bootstrap.get("min_identity_coverage_pct", 95)),
            min_sector_coverage_pct=float(bootstrap.get("min_sector_coverage_pct", 90)),
        )
        return self._builder

    def bootstrap(self, refresh: bool = False) -> dict[str, Any]:
        return self.builder().build_and_write(refresh=refresh)

    def health(self, database=None) -> dict[str, Any]:
        """Read-only health: it never creates or replaces a snapshot."""
        from .health import bootstrap_health

        payload = read_snapshot(self.snapshot_path) if self.snapshot_path.is_file() else None
        reasons: list[str] = []
        if payload is None:
            reasons.append("SECURITY_MASTER_SNAPSHOT_MISSING")
            records = []
        else:
            records = snapshot_records(payload)
        from .universe import InMemorySecurityMasterProvider
        security_master = InMemorySecurityMasterProvider(records)

        credentials = self.config.get("credentials", {})
        market_data = None
        benchmark_provider = None
        if not credentials.get("toss_app_key") or not credentials.get("toss_app_secret"):
            reasons.append("TOSS_CREDENTIALS_REQUIRED")
        elif self.config.get("market_data_provider", self.config.get("provider", "toss")) == "toss":
            from ..toss import TossClient
            from .providers_live import TossDiscoveryBenchmarkProvider, TossDiscoveryMarketDataProvider
            market_data = TossDiscoveryMarketDataProvider(
                TossClient(credentials.get("toss_app_key", ""), credentials.get("toss_app_secret", "")))
            benchmark_provider = TossDiscoveryBenchmarkProvider(market_data)
        fundamental = None
        capital = None
        if not self.user_agent:
            reasons.append("SEC_USER_AGENT_REQUIRED")
        elif records:
            from .providers_live import SECDiscoveryCapitalPreflightProvider, SECDiscoveryFundamentalProvider
            cache_dir = self.config.get("discovery", {}).get("bootstrap", {}).get("fundamental_cache_dir", "")
            fundamental = SECDiscoveryFundamentalProvider(self.user_agent, cache_dir)
            capital = SECDiscoveryCapitalPreflightProvider(self.user_agent, self.sector_cache_dir)

        if database is None:
            from ..database import Database
            database = Database(self.config["database_path"])
        result = bootstrap_health(
            database, security_master, market_data, benchmark_provider,
            min_accepted=int(self.config.get("discovery", {}).get("bootstrap", {}).get("min_accepted", 1)),
            min_identity_coverage_pct=float(self.config.get("discovery", {}).get("bootstrap", {}).get(
                "min_identity_coverage_pct", 95)),
            min_sector_coverage_pct=float(self.config.get("discovery", {}).get("bootstrap", {}).get(
                "min_sector_coverage_pct", 90)),
            fundamental_provider=fundamental, capital_preflight_provider=capital,
            max_actual_llm_calls=int(self.config.get("discovery", {}).get("cost", {}).get(
                "max_actual_llm_calls", 0) or 0), initialize_database=False,
        )
        result["snapshot"] = {
            "exists": payload is not None,
            "path": str(self.snapshot_path),
            "generated_at": payload.get("generated_at", "") if payload else "",
            "source_as_of": payload.get("source_as_of", "") if payload else "",
            "metrics": payload.get("metrics", {}) if payload else {},
        }
        result["credentials"] = {
            "sec_user_agent": "READY" if self.user_agent else "BLOCKED",
            "toss": "READY" if credentials.get("toss_app_key") and credentials.get("toss_app_secret") else "BLOCKED",
        }
        result["reason_codes"] = sorted(set(result.get("reason_codes", []) + reasons))
        if result.get("status") == "DEEP_HANDOFF_READY" and reasons:
            result["status"] = "BOOTSTRAP_REQUIRED"
        result["command"] = "discovery-health"
        return result
