from __future__ import annotations

"""Validated Security Master bootstrap and snapshot lifecycle.

The SEC ticker directory is deliberately treated as a listing baseline only.  It
does not establish that a symbol is a common stock, ETF, warrant, unit,
preferred security, or ADR.  This module composes that baseline with the
official Nasdaq Trader symbol directories and SEC submissions metadata while
preserving UNKNOWN whenever an authoritative classification is unavailable.
"""

import hashlib
import html
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from contextlib import contextmanager
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
        "NEW YORK STOCK EXCHANGE": "NYSE",
        "THE NEW YORK STOCK EXCHANGE": "NYSE",
        "NASDAQ STOCK MARKET": "NASDAQ",
        "THE NASDAQ STOCK MARKET": "NASDAQ",
        "NASDAQ GLOBAL SELECT MARKET": "NASDAQ",
        "NASDAQ GLOBAL MARKET": "NASDAQ",
        "NASDAQ CAPITAL MARKET": "NASDAQ",
        "THE NASDAQ GLOBAL SELECT MARKET": "NASDAQ",
        "THE NASDAQ GLOBAL MARKET": "NASDAQ",
        "THE NASDAQ CAPITAL MARKET": "NASDAQ",
        "NYSE AMERICAN LLC": "NYSE AMERICAN",
        "NYSE ARCA INC": "NYSE ARCA",
        "NYSE ARCA, INC.": "NYSE ARCA",
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
    elif re.search(r"\bADR\b|\bADRS\b|\bADS\b|AMERICAN DEPOSITARY|DEPOSITARY SHARES?", text):
        category = "ADR"
    elif re.search(
            r"\bCOMMON\s+(?:STOCK|SHARES?)\b"
            r"|\bORDINARY\s+SHARES?\b"
            r"|\b(?:CLASS\s+[A-Z0-9]+\s+)?SUBORDINATE\s+VOTING\s+SHARES?\b"
            r"|\b(?:CLASS\s+[A-Z0-9]+\s+)?LIMITED\s+VOTING\s+SHARES?\b"
            r"|\bVOTING\s+COMMON\s+SHARES?\b", text) and not re.search(
                r"\bRIGHTS?\b|\bDEPOSITARY\b|\bADS?\b|AMERICAN\s+DEPOSITARY", text):
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


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_bytes(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))


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
        self.source_as_of = "UNKNOWN"
        self.fetched_at = ""

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
            self.source_as_of = str(metadata.get("source_as_of") or self._file_timestamp(text) or "UNKNOWN")
            self.fetched_at = str(metadata.get("fetched_at") or "")
            return text, self.source_as_of
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
        self.source_as_of = source_as_of or "UNKNOWN"
        self.fetched_at = _now()
        _write_json_cache(path.with_suffix(path.suffix + ".meta.json"),
                          {"source_url": url, "source_as_of": source_as_of,
                           "fetched_at": self.fetched_at, "checksum": _checksum(raw)},
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
            test_issue = _as_bool(row.get("Test Issue"))
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
                "is_test_issue": test_issue,
            })
        return output


class SECSubmissionsBulkMetadataProvider:
    """Read official SEC submissions bulk JSON files without per-CIK requests."""

    URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
    MAX_ENTRY_BYTES = 25 * 1024 * 1024

    def __init__(self, user_agent: str, raw_cache_dir: str | Path, opener=None,
                 timeout: float = 120.0):
        if not user_agent:
            raise SecurityMasterBootstrapError("SEC_USER_AGENT_REQUIRED")
        raw_dir = Path(raw_cache_dir)
        self.user_agent = user_agent
        self.archive_path = raw_dir / "sec_submissions.zip"
        self.metadata_path = raw_dir / "sec_submissions.zip.meta.json"
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.calls = 0
        self.bulk_downloads = 0
        self.cache_hits = 0
        self.failed = 0
        self.failure_reason_counts: Counter[str] = Counter()
        self.unmatched = 0
        self.progress_callback = None
        self.source_as_of = "UNKNOWN"
        self.fetched_at = ""

    def set_progress_callback(self, callback) -> None:
        self.progress_callback = callback

    def _validate_archive(self, path: Path) -> list[str]:
        if not zipfile.is_zipfile(path):
            raise SecurityMasterBootstrapError("SEC_BULK_INVALID")
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if not names:
                    raise SecurityMasterBootstrapError("SEC_BULK_INVALID")
                seen: set[str] = set()
                json_names = []
                for info in archive.infolist():
                    name = str(info.filename).replace("\\", "/")
                    normalized = name.lstrip("/")
                    if name.startswith("/") or ".." in Path(normalized).parts:
                        raise SecurityMasterBootstrapError("SEC_BULK_PATH_TRAVERSAL")
                    if name in seen:
                        raise SecurityMasterBootstrapError("SEC_BULK_DUPLICATE_ENTRY")
                    seen.add(name)
                    if info.file_size > self.MAX_ENTRY_BYTES:
                        raise SecurityMasterBootstrapError("SEC_BULK_ENTRY_TOO_LARGE")
                    if re.fullmatch(r"CIK\d{10}\.json", Path(name).name, re.IGNORECASE):
                        json_names.append(name)
                if not json_names:
                    raise SecurityMasterBootstrapError("SEC_BULK_SCHEMA_INVALID")
                with archive.open(json_names[0]) as handle:
                    sample = json.loads(handle.read(self.MAX_ENTRY_BYTES + 1).decode("utf-8"))
                if not isinstance(sample, dict) or not any(
                        key in sample for key in ("sic", "sicDescription", "name", "tickers", "exchanges")):
                    raise SecurityMasterBootstrapError("SEC_BULK_SCHEMA_INVALID")
                return json_names
        except SecurityMasterBootstrapError:
            raise
        except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecurityMasterBootstrapError("SEC_BULK_INVALID", str(exc)) from exc

    def _load(self, refresh: bool = False) -> Path:
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        if self.archive_path.is_file() and not refresh:
            self._validate_archive(self.archive_path)
            metadata = _read_json_cache(self.metadata_path) or {}
            self.source_as_of = str(metadata.get("source_as_of") or "UNKNOWN")
            self.fetched_at = str(metadata.get("fetched_at") or "")
            self.cache_hits += 1
            return self.archive_path
        request = urllib.request.Request(self.URL, headers={"User-Agent": self.user_agent, "Accept": "application/zip"})
        temporary = self.archive_path.with_name(f".{self.archive_path.name}.{os.getpid()}.tmp")
        self.calls += 1
        self.bulk_downloads += 1
        try:
            with self.opener(request, timeout=self.timeout) as response, temporary.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_archive(temporary)
            fetched_at = _now()
            headers = getattr(response, "headers", {})
            get_header = getattr(headers, "get", lambda name, default=None: default)
            self.source_as_of = str(get_header("Last-Modified") or get_header("ETag") or "UNKNOWN")
            self.fetched_at = fetched_at
            raw = temporary.read_bytes()
            _write_json_atomic(self.metadata_path, {
                "source": "SEC_SUBMISSIONS_BULK",
                "source_url": self.URL,
                "source_as_of": self.source_as_of,
                "fetched_at": fetched_at,
                "checksum": _checksum(raw),
                "size": len(raw),
                "schema_version": "security_master_bulk_v1",
            })
            os.replace(temporary, self.archive_path)
            return self.archive_path
        except SecurityMasterBootstrapError:
            self.failed += 1
            raise
        except (OSError, urllib.error.URLError) as exc:
            self.failed += 1
            raise SecurityMasterBootstrapError("SEC_BULK_DOWNLOAD_FAILED", str(exc)) from exc
        finally:
            if temporary.exists():
                temporary.unlink()

    def profiles(self, records: Iterable[SecurityMasterRecord], refresh: bool = False) -> dict[str, dict[str, Any]]:
        archive_path = self._load(refresh=refresh)
        by_cik = {_normalise_cik(record.cik): record for record in records if _normalise_cik(record.cik)}
        result: dict[str, dict[str, Any]] = {}
        processed = 0
        with zipfile.ZipFile(archive_path) as archive:
            names = {Path(name).name.upper(): name for name in archive.namelist()}
            for cik, record in by_cik.items():
                member = names.get(f"CIK{cik}.JSON")
                if not member:
                    self.unmatched += 1
                    continue
                try:
                    with archive.open(member) as handle:
                        payload = json.loads(handle.read(self.MAX_ENTRY_BYTES + 1).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("SEC bulk issuer payload is not an object")
                    result[cik] = {
                        "cik": cik,
                        "sic": str(payload.get("sic") or ""),
                        "sic_description": str(payload.get("sicDescription") or ""),
                        "company_name": str(payload.get("name") or record.company_name),
                        "source": "SEC_SUBMISSIONS_BULK",
                        "source_url": self.URL,
                        "source_as_of": self.source_as_of,
                    }
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self.failed += 1
                processed += 1
                if callable(self.progress_callback) and (processed % 100 == 0 or processed == len(by_cik)):
                    self.progress_callback("SEC_METADATA", processed, len(by_cik), self.cache_hits,
                                           self.bulk_downloads, self.failed, self.unmatched)
        return result

    @staticmethod
    def _filing_rows(payload: dict[str, Any], allowed_forms: set[str]) -> list[dict[str, Any]]:
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filed_dates = recent.get("filingDate", [])
        acceptance = recent.get("acceptanceDateTime", [])
        documents = recent.get("primaryDocument", [])
        rows: list[dict[str, Any]] = []
        for index, form in enumerate(forms):
            if str(form or "").upper() not in allowed_forms:
                continue
            accession = str(accessions[index] if index < len(accessions) else "")
            filed_at = str(filed_dates[index] if index < len(filed_dates) else "")
            accepted_at = str(acceptance[index] if index < len(acceptance) else "")
            document = str(documents[index] if index < len(documents) else "")
            if accession and filed_at and document:
                rows.append({"form": str(form).upper(), "accession": accession,
                             "filed_at": filed_at, "acceptance_datetime": accepted_at,
                             "primary_document": document})
        return rows

    @classmethod
    def _latest_periodic_from_payload(cls, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Select a periodic filing by filed/acceptance time, never accession order."""
        rows = cls._filing_rows(payload, {"10-Q", "10-K", "20-F", "40-F"})
        return max(rows, key=lambda row: (row["filed_at"], row["acceptance_datetime"], row["accession"])) if rows else None

    @classmethod
    def _latest_cover_filing_from_payload(cls, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Prefer periodic cover pages, then a usable 8-K cover page."""
        periodic = cls._filing_rows(payload, {"10-Q", "10-K", "20-F", "40-F"})
        if periodic:
            row = max(periodic, key=lambda item: (item["filed_at"], item["acceptance_datetime"], item["accession"]))
            row["selection"] = "PERIODIC"
            return row
        current = cls._filing_rows(payload, {"8-K"})
        if current:
            row = max(current, key=lambda item: (item["filed_at"], item["acceptance_datetime"], item["accession"]))
            row["selection"] = "8-K_FALLBACK"
            return row
        return None

    def latest_periodic(self, records: Iterable[SecurityMasterRecord], refresh: bool = False) -> dict[str, dict[str, Any]]:
        archive_path = self._load(refresh=refresh)
        by_cik = {_normalise_cik(record.cik): record for record in records if _normalise_cik(record.cik)}
        result: dict[str, dict[str, Any]] = {}
        with zipfile.ZipFile(archive_path) as archive:
            names = {Path(name).name.upper(): name for name in archive.namelist()}
            for cik in by_cik:
                member = names.get(f"CIK{cik}.JSON")
                if not member:
                    self.unmatched += 1
                    continue
                try:
                    with archive.open(member) as handle:
                        payload = json.loads(handle.read(self.MAX_ENTRY_BYTES + 1).decode("utf-8"))
                    if isinstance(payload, dict):
                        filing = self._latest_periodic_from_payload(payload)
                        if filing:
                            result[cik] = {"cik": cik, **filing, "source": "SEC_SUBMISSIONS_BULK",
                                           "source_url": self.URL, "source_as_of": self.source_as_of}
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self.failed += 1
        return result

    def latest_cover_filing(self, records: Iterable[SecurityMasterRecord], refresh: bool = False) -> dict[str, dict[str, Any]]:
        archive_path = self._load(refresh=refresh)
        by_cik = {_normalise_cik(record.cik): record for record in records if _normalise_cik(record.cik)}
        result: dict[str, dict[str, Any]] = {}
        with zipfile.ZipFile(archive_path) as archive:
            names = {Path(name).name.upper(): name for name in archive.namelist()}
            for cik in by_cik:
                member = names.get(f"CIK{cik}.JSON")
                if not member:
                    self.unmatched += 1
                    continue
                try:
                    with archive.open(member) as handle:
                        payload = json.loads(handle.read(self.MAX_ENTRY_BYTES + 1).decode("utf-8"))
                    if isinstance(payload, dict):
                        filing = self._latest_cover_filing_from_payload(payload)
                        if filing:
                            result[cik] = {"cik": cik, **filing, "source": "SEC_SUBMISSIONS_BULK",
                                           "source_url": self.URL, "source_as_of": self.source_as_of}
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self.failed += 1
        return result


class SECPeriodicCoverIdentityProvider:
    """Resolve unresolved listings from SEC inline-XBRL security-level facts.

    Only the structured tuple (Security12bTitle, TradingSymbol,
    SecurityExchangeName) is accepted.  CompanyFacts share counts and issuer
    names are deliberately not identity evidence for a ticker.
    """

    PERIODIC_FORMS = {"10-Q", "10-K", "20-F", "40-F"}
    FACT_NAMES = {
        "dei:security12btitle": "title",
        "dei:tradingsymbol": "symbol",
        "dei:securityexchangename": "exchange",
    }

    def __init__(self, user_agent: str, bulk_provider: SECSubmissionsBulkMetadataProvider,
                 cache_dir: str | Path, opener=None, max_requests: int = 2_500,
                 max_rps: float = 4.0, timeout: float = 30.0):
        if not user_agent:
            raise SecurityMasterBootstrapError("SEC_USER_AGENT_REQUIRED")
        self.user_agent = user_agent
        self.bulk_provider = bulk_provider
        self.cache_dir = Path(cache_dir)
        self.opener = opener or urllib.request.urlopen
        self.max_requests = max(0, int(max_requests))
        self.max_rps = max(0.5, min(float(max_rps), 5.0))
        self.timeout = timeout
        self.calls = 0
        self.cache_hits = 0
        self.failed = 0
        self.failure_reason_counts: Counter[str] = Counter()
        self.unmatched = 0
        self.resolved = 0
        self.conflicted = 0
        self.remaining_unknown = 0
        self.source_as_of = "UNKNOWN"
        self.fetched_at = ""
        self._last_request_at = 0.0

    @classmethod
    def _inline_facts(cls, text: str) -> dict[str, list[dict[str, str]]]:
        facts: dict[str, list[dict[str, str]]] = defaultdict(list)
        pattern = re.compile(
            r"<ix:nonnumeric\b(?P<attrs>[^>]*)>(?P<body>.*?)</ix:nonnumeric\s*>",
            re.IGNORECASE | re.DOTALL,
        )
        attr_pattern = re.compile(r"([A-Za-z_:][\w:.-]*)\s*=\s*([\"'])(.*?)\2", re.DOTALL)
        for match in pattern.finditer(text):
            attrs = {key.lower(): value for key, _, value in attr_pattern.findall(match.group("attrs"))}
            field = cls.FACT_NAMES.get(str(attrs.get("name") or "").lower())
            if not field:
                continue
            value = html.unescape(re.sub(r"<[^>]*>", " ", match.group("body")))
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                facts[field].append({"value": value, "context_ref": str(attrs.get("contextref") or "")})
        return facts

    @classmethod
    def parse_cover_page(cls, raw: bytes | str, filing: dict[str, Any],
                         expected_ticker: str, expected_cik: str,
                         expected_exchange: str) -> dict[str, Any] | None:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        facts = cls._inline_facts(text)
        grouped: dict[str, dict[str, str]] = defaultdict(dict)
        for field in cls.FACT_NAMES.values():
            for item in facts.get(field, []):
                grouped[item.get("context_ref", "")][field] = item["value"]
        tuples = [row for row in grouped.values() if all(row.get(field) for field in ("title", "symbol", "exchange"))]
        if not tuples:
            return None
        expected_ticker = str(expected_ticker or "").strip().upper()
        expected_exchange = _normalise_exchange(expected_exchange)
        matching = [row for row in tuples if row["symbol"].strip().upper() == expected_ticker]
        if not matching:
            return None
        def signature(row: dict[str, str]) -> tuple[str, str, str]:
            return (
                re.sub(r"\s+", " ", row["title"]).strip().upper(),
                row["symbol"].strip().upper(),
                _normalise_exchange(row["exchange"]),
            )
        unique = {signature(row): row for row in matching}
        ordered_signatures = sorted(unique)
        tuple_row = unique[ordered_signatures[0]]
        tuple_conflict = len(unique) > 1
        cover_exchange = _normalise_exchange(tuple_row["exchange"])
        category, identity = _classification_from_name(tuple_row["title"], None)
        if tuple_conflict:
            identity = {flag: None for flag in IDENTITY_FLAGS}
            category = "UNKNOWN"
        exchange_conflict = bool(cover_exchange and expected_exchange and cover_exchange != expected_exchange)
        return {
            "ticker": expected_ticker,
            "cik": _normalise_cik(expected_cik),
            "company_name": tuple_row["title"],
            "exchange": expected_exchange,
            "security_type": category,
            "identity": identity,
            "identity_conflicted": bool(exchange_conflict or tuple_conflict),
            "identity_conflict_reason": (
                "MULTIPLE_SECURITY_TUPLE_CONFLICT" if tuple_conflict else
                "EXCHANGE_MISMATCH" if exchange_conflict else ""),
            "cover_title": tuple_row["title"],
            "cover_symbol": tuple_row["symbol"].strip().upper(),
            "cover_exchange": cover_exchange,
            "source": "SEC_PERIODIC_COVER_PAGE",
            "source_url": str(filing.get("source_url") or ""),
            "source_as_of": str(filing.get("filed_at") or ""),
            "filing_form": str(filing.get("form") or ""),
            "filing_accession": str(filing.get("accession") or ""),
            "provenance": {
                "security_level_tuple": {
                    "title": tuple_row["title"], "symbol": tuple_row["symbol"],
                    "exchange": tuple_row["exchange"], "cik": _normalise_cik(expected_cik),
                    "form": filing.get("form", ""), "filed_at": filing.get("filed_at", ""),
                },
                "security_level_tuples": [
                    {"title": row["title"], "symbol": row["symbol"],
                     "exchange": row["exchange"], "normalized": list(item)}
                    for item, row in sorted(unique.items())
                ],
            },
        }

    def _cache_paths(self, filing: dict[str, Any]) -> tuple[Path, Path]:
        cik = _normalise_cik(filing.get("cik"))
        accession = re.sub(r"[^0-9A-Za-z]", "", str(filing.get("accession") or "")) or "unknown"
        base = self.cache_dir / f"CIK{cik}_{accession}"
        return base.with_suffix(".html"), base.with_suffix(".html.meta.json")

    def _load_document(self, filing: dict[str, Any], refresh: bool = False) -> bytes | None:
        html_path, meta_path = self._cache_paths(filing)
        if html_path.is_file() and not refresh:
            self.cache_hits += 1
            return html_path.read_bytes()
        if self.max_requests and self.calls >= self.max_requests:
            return None
        elapsed = time.monotonic() - self._last_request_at
        wait = (1.0 / self.max_rps) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()
        cik = _normalise_cik(filing.get("cik"))
        accession = str(filing.get("accession") or "").replace("-", "")
        document = str(filing.get("primary_document") or "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{document}"
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "text/html"})
        self.calls += 1
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read()
            _atomic_write_bytes(html_path, raw)
            _write_json_atomic(meta_path, {
                "source": "SEC_PERIODIC_COVER_PAGE", "source_url": url,
                "source_as_of": filing.get("filed_at", ""), "fetched_at": _now(),
                "checksum": _checksum(raw), "filing": filing,
            })
            self.fetched_at = _now()
            return raw
        except urllib.error.HTTPError as exc:
            self.failed += 1
            self.failure_reason_counts[f"HTTP_{exc.code}"] += 1
            return None
        except (OSError, urllib.error.URLError, ValueError) as exc:
            self.failed += 1
            self.failure_reason_counts[type(exc).__name__] += 1
            return None

    def records(self, records: Iterable[SecurityMasterRecord], as_of: str,
                refresh: bool = False) -> list[dict[str, Any]]:
        requested = list(records)
        if not requested:
            return []
        cover_selector = getattr(self.bulk_provider, "latest_cover_filing", None)
        filings = (cover_selector(requested, refresh=False) if callable(cover_selector)
                   else self.bulk_provider.latest_periodic(requested, refresh=False))
        output: list[dict[str, Any]] = []
        for record in requested:
            filing = filings.get(_normalise_cik(record.cik))
            if not filing:
                self.unmatched += 1
                continue
            filing = dict(filing)
            filing["source_url"] = (
                f"https://www.sec.gov/Archives/edgar/data/{int(_normalise_cik(record.cik))}/"
                f"{str(filing.get('accession', '')).replace('-', '')}/{filing.get('primary_document', '')}"
            )
            raw = self._load_document(filing, refresh=refresh)
            if raw is None:
                continue
            row = self.parse_cover_page(raw, filing, record.ticker, record.cik, record.exchange)
            if row is None:
                continue
            self.resolved += 1
            if row.get("identity_conflicted"):
                self.conflicted += 1
            output.append(row)
        self.remaining_unknown = max(0, len(requested) - self.resolved)
        return output

class SECSubmissionsCompositeMetadataProvider:
    """Use the official bulk archive first, with bounded cached per-CIK fallback."""

    URL = SECSubmissionsBulkMetadataProvider.URL

    def __init__(self, bulk: SECSubmissionsBulkMetadataProvider,
                 fallback: "SECSubmissionsMetadataProvider"):
        self.bulk = bulk
        self.fallback = fallback
        self.calls = 0
        self.failed = 0
        self.unmatched = 0
        self.bulk_downloads = 0
        self.individual_calls = 0
        self.fallback_cache_hits = 0
        self.bulk_error_reason_code = ""
        self.source_as_of = "UNKNOWN"
        self.fetched_at = ""
        self.progress_callback = None

    def set_progress_callback(self, callback) -> None:
        self.progress_callback = callback
        self.bulk.set_progress_callback(callback)

    def profiles(self, records: Iterable[SecurityMasterRecord], refresh: bool = False) -> dict[str, dict[str, Any]]:
        records = list(records)
        try:
            result = self.bulk.profiles(records, refresh=refresh)
            self.calls += self.bulk.calls
            self.failed += self.bulk.failed
            self.unmatched += self.bulk.unmatched
            self.bulk_downloads += self.bulk.bulk_downloads
            self.source_as_of = self.bulk.source_as_of
            self.fetched_at = self.bulk.fetched_at
            return result
        except SecurityMasterBootstrapError as bulk_error:
            # A failed bulk refresh must not turn one archive failure into a
            # thousands-request refresh storm.  Use the bounded per-CIK cache
            # as a last-known-good fallback; a later explicit refresh can retry
            # the bulk source after operators have inspected the error.
            result = self.fallback.profiles(records, refresh=False)
            self.calls += self.fallback.calls
            self.failed += self.fallback.failed
            self.unmatched += self.fallback.unmatched
            self.individual_calls += self.fallback.calls
            self.fallback_cache_hits += self.fallback.cache_hits
            self.bulk_error_reason_code = bulk_error.reason_code
            self.source_as_of = getattr(self.fallback, "source_as_of", "UNKNOWN")
            self.fetched_at = getattr(self.fallback, "fetched_at", "")
            result["_bulk_error"] = {"reason_code": bulk_error.reason_code}
            return {key: value for key, value in result.items() if not key.startswith("_")}


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
        self.cache_hits = 0
        self.source_as_of = "UNKNOWN"
        self.fetched_at = ""
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
            self.cache_hits += 1
            cached = _read_json_cache(cache_path)
            if isinstance(cached, dict):
                self.source_as_of = str(cached.get("lastUpdate") or self.source_as_of)
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
                self.source_as_of = source_as_of or "UNKNOWN"
                self.fetched_at = _now()
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
        country=str(row.get("country") or "UNKNOWN").upper(),
        listing_country=str(row.get("listing_country") or "").upper(),
        listing_market=str(row.get("listing_market") or "").upper(),
        issuer_country=str(row.get("issuer_country") or "UNKNOWN").upper(),
        is_common_stock=row.get("is_common_stock"), is_etf=row.get("is_etf"),
        is_unit=row.get("is_unit"), is_warrant=row.get("is_warrant"),
        is_preferred=row.get("is_preferred"), is_adr=row.get("is_adr"),
        is_test_issue=row.get("is_test_issue"),
        sector_canonical=str(row.get("sector_canonical") or "UNKNOWN"),
        industry_canonical=str(row.get("industry_canonical") or "UNKNOWN"),
        sic=str(row.get("sic") or ""), sic_description=str(row.get("sic_description") or ""),
        active_status=str(row.get("active_status") or "ACTIVE").upper(),
        source=str(row.get("source") or "SEC_DIRECTORY+VALIDATED_ENRICHMENT"),
        source_as_of=str(row.get("source_as_of") or ""),
        ingested_at=str(row.get("ingested_at") or ""),
        themes=tuple(row.get("themes") or ()),
        identity_conflicted=bool(row.get("identity_conflicted", False)),
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
        # SEC's baseline can contain a blank exchange.  That is an unknown
        # listing attribute, not a reason to discard the whole snapshot.  The
        # executable-universe filter rejects it as MISSING_EXCHANGE; snapshot
        # validation only rejects malformed/non-normalizable values.
        if cik and not re.fullmatch(r"\d{10}", cik):
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_MALFORMED_CIK")
        identity_key = (cik, ticker)
        if identity_key in seen_identity:
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_DUPLICATE_IDENTITY")
        seen_identity.add(identity_key)
        true_flags = []
        test_issue = row.get("is_test_issue")
        if test_issue is not None and not isinstance(test_issue, bool):
            raise SecurityMasterSnapshotValidationError("SECURITY_MASTER_BOOLEAN_INVALID")
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
                 min_sector_coverage_pct: float = 90.0,
                 lock_ttl_seconds: int = 6 * 60 * 60,
                 cover_identity_provider=None):
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
        self.cover_identity_provider = cover_identity_provider
        self.candidate_path = self.snapshot_path.with_name("security_master_candidate.json")
        self.failed_candidate_path = self.snapshot_path.with_name("security_master_failed_candidate.json")
        self.diagnostics_path = self.snapshot_path.with_name("security_master_build_diagnostics.json")
        self.progress_path = self.snapshot_path.with_name("security_master_build_progress.json")
        self.lock_path = self.snapshot_path.with_name("security_master_build.lock")
        self.lock_ttl_seconds = max(60, int(lock_ttl_seconds))
        self._current_build_id = ""
        self._current_started_at = ""
        self._identity_diagnostics: dict[str, Any] = {}

    def _progress(self, stage: str, processed: int = 0, total: int = 0,
                  cached: int = 0, downloaded: int = 0, failed: int = 0,
                  unknown: int = 0, conflicted: int = 0, **extra) -> None:
        existing = _read_json_cache(self.progress_path) or {}
        if not self._current_build_id:
            self._current_build_id = f"SMB_{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}_{os.getpid()}"
            self._current_started_at = _now()
        manifest = {
            "build_id": self._current_build_id,
            "started_at": self._current_started_at,
            "source_versions": existing.get("source_versions", {}),
            "stage": stage,
            "processed": int(processed), "total": int(total),
            "cached": int(cached), "downloaded": int(downloaded),
            "failed": int(failed), "unknown": int(unknown), "conflicted": int(conflicted),
            "elapsed": extra.pop("elapsed", None),
            "last_checkpoint": _now(),
            **extra,
        }
        _write_json_atomic(self.progress_path, manifest)

    @contextmanager
    def _build_lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, json.dumps({"pid": os.getpid(), "started_at": _now()}).encode("utf-8"))
                finally:
                    os.close(fd)
                break
            except FileExistsError:
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                except OSError:
                    age = 0
                if age <= self.lock_ttl_seconds:
                    raise SecurityMasterBootstrapError("SECURITY_MASTER_BUILD_ALREADY_RUNNING")
                self.lock_path.unlink(missing_ok=True)
        try:
            yield
        finally:
            self.lock_path.unlink(missing_ok=True)

    def build(self, as_of: str | None = None, refresh: bool = False) -> dict[str, Any]:
        as_of = as_of or _now()
        self._progress("BASELINE", refresh=bool(refresh), requested_as_of=as_of)
        baseline = self._records(self.listing_provider, as_of, refresh)
        if not baseline:
            raise SecurityMasterBootstrapError("SEC_DIRECTORY_EMPTY")
        self._progress("SECURITY_TYPE", processed=len(baseline), total=len(baseline))
        type_rows = self._records(self.security_type_provider, as_of, refresh)
        type_groups = self._join_type_rows(baseline, type_rows)

        prelim_records = []
        for base in baseline:
            group = type_groups.get(base.ticker, [])
            merged = self._merge_type_group(group)
            prelim_records.append(self._row_from_base(base, merged, as_of))

        identity_before_supported = [
            row for row in prelim_records
            if row.get("exchange") in self.supported_exchanges
        ]
        unresolved = [
            base for base, row in zip(baseline, prelim_records)
            if row.get("exchange") in self.supported_exchanges
            and (row.get("identity_conflicted") or
                 not all(row.get(flag) is not None for flag in IDENTITY_FLAGS))
        ]
        identity_before_by_ticker = {row.get("ticker"): row for row in prelim_records}
        self._identity_diagnostics = self._build_identity_diagnostics(
            baseline, type_rows, prelim_records, unresolved)
        if self.cover_identity_provider is not None and unresolved:
            self._progress("SEC_PERIODIC_COVER_PAGE", processed=0, total=len(unresolved),
                           unknown=len(unresolved))
            cover_rows = self.cover_identity_provider.records(unresolved, as_of, refresh=refresh)
            type_rows = list(type_rows) + list(cover_rows)
            type_groups = self._join_type_rows(baseline, type_rows)
            prelim_records = [
                self._row_from_base(base, self._merge_type_group(type_groups.get(base.ticker, [])), as_of)
                for base in baseline
            ]
            self._identity_diagnostics["identity_resolved_by_sec_cover_page"] = sum(
                1 for after in prelim_records
                for before in [identity_before_by_ticker.get(after.get("ticker"), {})]
                if before.get("exchange") in self.supported_exchanges
                and (before.get("identity_conflicted") or
                     not all(before.get(flag) is not None for flag in IDENTITY_FLAGS))
                and not after.get("identity_conflicted")
                and all(after.get(flag) is not None for flag in IDENTITY_FLAGS)
            )
            self._identity_diagnostics["identity_remaining_unknown"] = sum(
                1 for row in prelim_records
                if row.get("exchange") in self.supported_exchanges
                and (row.get("identity_conflicted") or
                     not all(row.get(flag) is not None for flag in IDENTITY_FLAGS))
            )
            self._identity_diagnostics["identity_conflicted"] = sum(
                bool(row.get("identity_conflicted")) for row in prelim_records
            )
            after_unresolved = [
                base for base, row in zip(baseline, prelim_records)
                if row.get("exchange") in self.supported_exchanges
                and not all(row.get(flag) is not None for flag in IDENTITY_FLAGS)
            ]
            after_diagnostics = self._build_identity_diagnostics(
                baseline, type_rows, prelim_records, after_unresolved)
            self._identity_diagnostics["buckets_after_cover_page"] = after_diagnostics.get("buckets", {})
        self._identity_diagnostics["ordinary_shares_audit"] = self._ordinary_shares_audit(
            type_rows, baseline, prelim_records)

        sector_records = [
            _record_from_row(row) for row in prelim_records
            if row["exchange"] in self.supported_exchanges
            and row.get("is_common_stock") is True
            and not row.get("identity_conflicted")
            and all(row.get(flag) is not None for flag in IDENTITY_FLAGS)
        ]
        issuer_profiles: dict[str, dict[str, Any]] = {}
        if self.sector_provider is not None and sector_records:
            setter = getattr(self.sector_provider, "set_progress_callback", None)
            if callable(setter):
                setter(lambda stage, processed, total, cached, downloaded, failed, unknown:
                       self._progress(stage, processed, total, cached, downloaded, failed, unknown))
            issuer_profiles = self.sector_provider.profiles(sector_records, refresh=refresh)
        self._progress("SNAPSHOT_VALIDATION", processed=len(prelim_records), total=len(prelim_records),
                       unknown=sum(row.get("identity_conflicted") or
                                   not all(row.get(flag) is not None for flag in IDENTITY_FLAGS)
                                   for row in prelim_records),
                       conflicted=sum(bool(row.get("identity_conflicted")) for row in prelim_records))

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
                "mapping_version": "SEC_SIC_RANGE_V2",
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
        self._current_build_id = ""
        self._current_started_at = ""
        with self._build_lock():
            self._progress("BUILDING", refresh=bool(refresh), requested_as_of=as_of or _now())
            try:
                payload = self.build(as_of=as_of, refresh=refresh)
                # Re-validate before any candidate or active publication.
                validate_snapshot(payload)
                payload["status"] = self._readiness_status(payload["metrics"])
                payload["reason_codes"] = self._readiness_reasons(payload["metrics"])
                payload["publication"] = {
                    "active": payload["status"] == "SECURITY_MASTER_READY",
                    "candidate_path": str(self.candidate_path),
                    "active_path": str(self.snapshot_path),
                }
                _write_json_atomic(self.candidate_path, payload)
                _write_json_atomic(self.diagnostics_path, {
                    "status": payload["status"],
                    "reason_codes": payload["reason_codes"],
                    "metrics": payload["metrics"],
                    "generated_at": payload["generated_at"],
                    "source_as_of": payload["source_as_of"],
                })
                if payload["status"] != "SECURITY_MASTER_READY":
                    _write_json_atomic(self.failed_candidate_path, payload)
                    self._progress("COVERAGE_INSUFFICIENT", processed=len(payload["records"]),
                                   total=len(payload["records"]), status=payload["status"],
                                   reason_codes=payload["reason_codes"])
                    payload["snapshot_path"] = str(self.snapshot_path) if self.snapshot_path.is_file() else ""
                    return payload
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
                _write_json_atomic(normalized, normalized_payload)
                _atomic_write_bytes(
                    self.snapshot_path,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
                )
                payload["snapshot_path"] = str(self.snapshot_path)
                payload["normalized_cache_path"] = str(normalized)
                self._progress("ACTIVE", processed=len(payload["records"]), total=len(payload["records"]),
                               status=payload["status"], reason_codes=payload["reason_codes"])
                return payload
            except Exception as exc:
                reason = getattr(exc, "reason_code", "SECURITY_MASTER_BUILD_FAILED")
                self._progress("FAILED", status="FAILED", reason_codes=[reason], error=str(exc))
                _write_json_atomic(self.diagnostics_path, {
                    "status": "FAILED", "reason_codes": [reason], "error": str(exc),
                    "failed_at": _now(), "active_snapshot_preserved": self.snapshot_path.is_file(),
                })
                raise

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
        merged["conflicted"] = any(bool(row.get("identity_conflicted")) for row in group)
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
        test_issue_values = {row.get("is_test_issue") for row in group
                             if row.get("is_test_issue") is not None}
        if test_issue_values == {True}:
            merged["is_test_issue"] = True
        elif test_issue_values == {False}:
            merged["is_test_issue"] = False
        else:
            merged["is_test_issue"] = None
        categories = {str(row.get("security_type") or "UNKNOWN") for row in group}
        known_categories = categories - {"UNKNOWN"}
        if merged["conflicted"]:
            merged["security_type"] = "UNKNOWN"
            merged["identity"] = {flag: None for flag in IDENTITY_FLAGS}
        elif len(known_categories) == 1:
            merged["security_type"] = next(iter(known_categories))
        elif len(known_categories) > 1:
            merged["security_type"] = "UNKNOWN"
            merged["conflicted"] = True
        return merged

    @staticmethod
    def _row_from_base(base: SecurityMasterRecord, merged: dict[str, Any], as_of: str) -> dict[str, Any]:
        identity = merged["identity"]
        conflict = bool(merged.get("conflicted"))
        if conflict:
            identity = {flag: None for flag in IDENTITY_FLAGS}
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
            "country": base.country or "UNKNOWN",
            "listing_country": getattr(base, "listing_country", "") or "US",
            "listing_market": getattr(base, "listing_market", "") or "US",
            "issuer_country": getattr(base, "issuer_country", "UNKNOWN") or "UNKNOWN",
            "active_status": base.active_status,
            "security_type": merged.get("security_type", "UNKNOWN"),
            "is_test_issue": merged.get("is_test_issue"),
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
                 "source_as_of": row.get("source_as_of", ""),
                 "cover_title": row.get("cover_title", ""),
                 "cover_symbol": row.get("cover_symbol", ""),
                 "cover_exchange": row.get("cover_exchange", ""),
                 "filing_form": row.get("filing_form", ""),
                 "filing_accession": row.get("filing_accession", ""),
                 "provenance": row.get("provenance", {}),
                 "identity_conflicted": bool(row.get("identity_conflicted"))}
                for row in merged.get("sources", [])
            ],
        }

    @classmethod
    def _build_identity_diagnostics(cls, baseline: list[SecurityMasterRecord],
                                    type_rows: list[dict[str, Any]],
                                    prelim_records: list[dict[str, Any]],
                                    unresolved: list[SecurityMasterRecord]) -> dict[str, Any]:
        by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in type_rows:
            by_ticker[str(row.get("ticker") or "").upper()].append(row)
        bucket_names = (
            "NO_OFFICIAL_SYMBOL_DIRECTORY_ROW",
            "NASDAQ_LISTED_TYPE_AMBIGUOUS",
            "OTHER_LISTED_TYPE_AMBIGUOUS",
            "ONLY_ETF_FIELD_KNOWN",
            "CIK_MISMATCH",
            "EXCHANGE_MISMATCH",
            "MULTIPLE_OFFICIAL_CONFLICT",
            "CLASS_SHARE_AMBIGUITY",
            "FOREIGN_OR_DEPOSITARY_AMBIGUITY",
            "OTHER",
        )
        buckets = {name: {"count": 0, "examples": []} for name in bucket_names}
        for record in unresolved:
            rows = by_ticker.get(record.ticker, [])
            nasdaq_rows = [row for row in rows if str(row.get("source_name") or "").upper() == "NASDAQ"]
            other_rows = [row for row in rows if str(row.get("source_name") or "").upper() == "OTHER"]
            mismatch_cik = any(
                _normalise_cik(row.get("cik")) and _normalise_cik(row.get("cik")) != _normalise_cik(record.cik)
                for row in rows
            )
            mismatch_exchange = any(
                _normalise_exchange(row.get("exchange")) and
                _normalise_exchange(row.get("exchange")) != _normalise_exchange(record.exchange)
                for row in rows
            )
            names = " ".join(str(row.get("company_name") or "") for row in rows).upper()
            known = set()
            for row in rows:
                known.update(flag for flag in IDENTITY_FLAGS if row.get("identity", {}).get(flag) is not None)
            signatures = {
                (
                    re.sub(r"\s+", " ", str(row.get("company_name") or "")).strip().upper(),
                    _normalise_exchange(row.get("exchange")),
                    str(row.get("security_type") or "UNKNOWN").upper(),
                    tuple(row.get("identity", {}).get(flag) for flag in IDENTITY_FLAGS),
                )
                for row in rows
            }
            has_multi_conflict = len(signatures) > 1 or any(
                bool(row.get("identity_conflicted")) for row in rows)
            unknown_nasdaq = any(str(row.get("security_type") or "UNKNOWN").upper() == "UNKNOWN"
                                 for row in nasdaq_rows)
            unknown_other = any(str(row.get("security_type") or "UNKNOWN").upper() == "UNKNOWN"
                                for row in other_rows)
            if mismatch_cik:
                bucket = "CIK_MISMATCH"
            elif mismatch_exchange:
                bucket = "EXCHANGE_MISMATCH"
            elif has_multi_conflict:
                bucket = "MULTIPLE_OFFICIAL_CONFLICT"
            elif not rows:
                bucket = "NO_OFFICIAL_SYMBOL_DIRECTORY_ROW"
            elif known and known <= {"is_etf"}:
                bucket = "ONLY_ETF_FIELD_KNOWN"
            elif unknown_nasdaq:
                bucket = "NASDAQ_LISTED_TYPE_AMBIGUOUS"
            elif unknown_other:
                bucket = "OTHER_LISTED_TYPE_AMBIGUOUS"
            elif re.search(r"\bCLASS\s+[A-Z0-9]", names) and not re.search(
                    r"COMMON|SUBORDINATE\s+VOTING|LIMITED\s+VOTING", names):
                bucket = "CLASS_SHARE_AMBIGUITY"
            elif re.search(r"ADR|ADS|DEPOSITARY|FOREIGN", names):
                bucket = "FOREIGN_OR_DEPOSITARY_AMBIGUITY"
            else:
                bucket = "OTHER"
            entry = buckets[bucket]
            entry["count"] += 1
            if len(entry["examples"]) < 20:
                entry["examples"].append(record.ticker)
        total = len(unresolved)
        return {
            "total": total,
            "buckets": {
                key: {"count": value["count"],
                              "pct": round(value["count"] / total * 100, 4) if total else 0.0,
                              "examples": value["examples"]}
                for key, value in buckets.items()
                if value["count"]
            },
            "identity_known_before_supported_count": sum(
                1 for row in prelim_records
                if row.get("exchange") in SUPPORTED_EXCHANGES
                and not row.get("identity_conflicted")
                and all(row.get(flag) is not None for flag in IDENTITY_FLAGS)
            ),
            "identity_resolved_by_sec_cover_page": 0,
            "identity_remaining_unknown": total,
            "identity_conflicted": 0,
        }

    @staticmethod
    def _ordinary_shares_audit(type_rows: list[dict[str, Any]],
                               baseline: list[SecurityMasterRecord],
                               prelim_records: list[dict[str, Any]]) -> dict[str, Any]:
        by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in type_rows:
            by_ticker[str(row.get("ticker") or "").upper()].append(row)
        base_by_ticker = {record.ticker: record for record in baseline}
        title_counts: Counter[str] = Counter()
        by_exchange: Counter[str] = Counter()
        resolved_tickers: list[str] = []
        overlap = 0
        foreign = 0
        foreign_unknown = 0
        conflicted = 0
        row_count = 0
        for record in prelim_records:
            source_rows = by_ticker.get(str(record.get("ticker") or "").upper(), [])
            ordinary_rows = []
            for row in source_rows:
                title = str(row.get("company_name") or "")
                category, _ = _classification_from_name(title, _as_bool(row.get("etf")))
                if re.search(r"\bORDINARY\s+SHARES?\b", title, re.IGNORECASE) and category == "COMMON_STOCK":
                    ordinary_rows.append(row)
            if not ordinary_rows:
                continue
            row_count += len(ordinary_rows)
            for row in ordinary_rows:
                title = re.sub(r"\s+", " ", str(row.get("company_name") or "")).strip()
                title_counts[title] += 1
                if re.search(r"ADR|ADS|DEPOSITARY", title, re.IGNORECASE):
                    overlap += 1
                issuer_country = str(row.get("issuer_country") or "").upper()
                if issuer_country and issuer_country not in {"UNKNOWN", "US"}:
                    foreign += 1
                else:
                    foreign_unknown += 1
            if record.get("identity_conflicted"):
                conflicted += 1
            elif record.get("is_common_stock") is True and all(
                    record.get(flag) is not None for flag in IDENTITY_FLAGS):
                ticker = str(record.get("ticker") or "").upper()
                resolved_tickers.append(ticker)
                by_exchange[str(record.get("exchange") or "UNKNOWN").upper()] += 1
        return {
            "ordinary_shares_row_count": row_count,
            "ordinary_shares_resolved_total": len(resolved_tickers),
            "ordinary_shares_resolved_by_exchange": dict(sorted(by_exchange.items())),
            "ordinary_shares_top_security_titles": [
                {"title": title, "count": count} for title, count in title_counts.most_common(20)
            ],
            "ordinary_shares_adr_ads_depositary_overlap": overlap,
            "ordinary_shares_foreign_issuer_count": foreign,
            "ordinary_shares_foreign_issuer_unknown_count": foreign_unknown,
            "ordinary_shares_conflicted_count": conflicted,
            "ordinary_shares_resolved_tickers": sorted(set(resolved_tickers)),
        }

    def _metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = payload["records"]
        records = [_record_from_row(row) for row in rows]
        supported = [row for row in records if row.exchange.upper() in self.supported_exchanges]
        identity_known_global = sum(self._identity_known(row) for row in records)
        identity_known_supported = sum(self._identity_known(row) for row in supported)
        conflict_count = sum(bool(row.get("identity_conflicted")) for row in rows)
        conflict_supported_count = sum(bool(row.identity_conflicted) for row in supported)
        integrity = UniverseIntegrityEngine(exchanges=set(self.supported_exchanges)).build(
            InMemorySecurityMasterProvider(records), payload["source_as_of"])
        health = integrity["health"]
        accepted = integrity["records"]
        sector_known = sum(row.sector_canonical.upper() != "UNKNOWN" for row in accepted)
        raw_count = len(records)
        supported_count = len(supported)
        source_matches = sum(bool(row.get("identity_sources")) for row in rows)
        sic_known = sum(bool(str(row.sic or "").strip()) for row in accepted)
        sector_unknown_due_missing_sic = sum(
            not str(row.sic or "").strip() for row in accepted
            if str(row.sector_canonical or "UNKNOWN").upper() == "UNKNOWN"
        )
        sector_unknown_due_mapper_gap = sum(
            bool(str(row.sic or "").strip()) for row in accepted
            if str(row.sector_canonical or "UNKNOWN").upper() == "UNKNOWN"
        )
        identity_supported_pct = round(identity_known_supported / supported_count * 100, 4) if supported_count else 0.0
        sector_pct = round(sector_known / len(accepted) * 100, 4) if accepted else 0.0
        identity_status = "IDENTITY_READY" if identity_supported_pct >= self.min_identity_coverage_pct else "IDENTITY_COVERAGE_INSUFFICIENT"
        sector_status = "SECTOR_READY" if sector_pct >= self.min_sector_coverage_pct else "SECTOR_COVERAGE_INSUFFICIENT"
        diagnostics = dict(self._identity_diagnostics)
        diagnostics.setdefault("identity_known_before_supported_count", identity_known_supported)
        diagnostics.setdefault("identity_resolved_by_sec_cover_page", 0)
        diagnostics.setdefault("identity_remaining_unknown", supported_count - identity_known_supported)
        diagnostics.setdefault("identity_conflicted", conflict_count)
        ordinary_audit = diagnostics.get("ordinary_shares_audit", {})
        ordinary_resolved = set(ordinary_audit.get("ordinary_shares_resolved_tickers", []))
        ordinary_audit["ordinary_shares_accepted_count"] = sum(
            1 for row in accepted if row.ticker in ordinary_resolved)
        diagnostics["ordinary_shares_audit"] = ordinary_audit
        return {
            "raw_count": raw_count,
            "supported_exchange_scope_count": supported_count,
            "identity_known_global_count": identity_known_global,
            "identity_coverage_global_pct": round(identity_known_global / raw_count * 100, 4) if raw_count else 0.0,
            "identity_known_supported_count": identity_known_supported,
            "identity_coverage_supported_scope_pct": identity_supported_pct,
            # Backward-compatible alias; readiness uses the explicit supported metric.
            "identity_coverage_pct": round(identity_known_global / raw_count * 100, 4) if raw_count else 0.0,
            "accepted_common_stock_count": len(accepted),
            "sic_known_count": sic_known,
            "sic_coverage_pct": round(sic_known / len(accepted) * 100, 4) if accepted else 0.0,
            "sector_known_count": sector_known,
            "sector_coverage_pct": sector_pct,
            "sector_unknown_due_missing_sic": sector_unknown_due_missing_sic,
            "sector_unknown_due_mapper_gap": sector_unknown_due_mapper_gap,
            "unknown_identity_count": sum(not self._identity_known(row) for row in records),
            "identity_conflict_count": conflict_count,
            "duplicate_count": health.get("duplicate_count", 0),
            "rejection_counts": dict(integrity.get("rejected", {})),
            "source_matches": source_matches,
            "source_unmatched": raw_count - source_matches,
            "source_conflicted": conflict_count,
            "security_type_source_calls": int(getattr(self.security_type_provider, "calls", 0) or 0),
            "identity_known_before_supported_count": diagnostics.get("identity_known_before_supported_count", 0),
            "identity_resolved_by_sec_cover_page": diagnostics.get("identity_resolved_by_sec_cover_page", 0),
            "identity_remaining_unknown": diagnostics.get("identity_remaining_unknown", 0),
            "identity_unknown_buckets": diagnostics.get("buckets", {}),
            "identity_unknown_buckets_after_cover_page": diagnostics.get("buckets_after_cover_page", {}),
            "identity_conflicted_after_cover_page": diagnostics.get("identity_conflicted", conflict_count),
            "identity_known_non_conflicted_supported_count": identity_known_supported,
            "identity_conflicted_supported_count": conflict_supported_count,
            "identity_unknown_supported_count": max(0, supported_count - identity_known_supported - conflict_supported_count),
            "ordinary_shares_audit": ordinary_audit,
            "identity_readiness": identity_status,
            "sector_readiness": sector_status,
            "security_master_readiness": "SECURITY_MASTER_READY" if (
                identity_status == "IDENTITY_READY" and sector_status == "SECTOR_READY"
                and len(accepted) >= self.min_accepted
            ) else "SECURITY_MASTER_COVERAGE_INSUFFICIENT",
            "cover_page_calls": int(getattr(self.cover_identity_provider, "calls", 0) or 0),
            "cover_page_cache_hits": int(getattr(self.cover_identity_provider, "cache_hits", 0) or 0),
            "cover_page_resolved": int(getattr(self.cover_identity_provider, "resolved", 0) or 0),
            "cover_page_conflicted": int(getattr(self.cover_identity_provider, "conflicted", 0) or 0),
            "cover_page_failed": int(getattr(self.cover_identity_provider, "failed", 0) or 0),
            "cover_page_failure_reasons": dict(getattr(
                self.cover_identity_provider, "failure_reason_counts", {}) or {}),
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
        return not bool(getattr(record, "identity_conflicted", False)) and all(
            getattr(record, flag) is not None for flag in IDENTITY_FLAGS)

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
        def source_as_of(provider, default: str = "UNKNOWN") -> str:
            value = getattr(provider, "source_as_of", "")
            if value:
                return str(value)
            sources = getattr(provider, "SOURCES", None)
            if sources:
                return str(getattr(provider, "source_as_of", default) or default)
            return default

        def fetched_at(provider) -> str:
            return str(getattr(provider, "fetched_at", "") or "")

        return {
            "baseline": {
                "source": "SEC_DIRECTORY",
                "source_url": getattr(self.listing_provider, "URL", ""),
                "requested_as_of": as_of,
                "source_as_of": source_as_of(self.listing_provider),
                "fetched_at": fetched_at(self.listing_provider),
            },
            "security_type": {
                "source": "NASDAQ_TRADER_SYMBOL_DIRECTORY",
                "source_urls": list(getattr(self.security_type_provider, "SOURCES", {}).values()),
                "requested_as_of": as_of,
                "source_as_of": source_as_of(self.security_type_provider),
                "fetched_at": fetched_at(self.security_type_provider),
            },
            "periodic_cover_page": {
                "source": "SEC_PERIODIC_COVER_PAGE_INLINE_XBRL",
                "source_url": "https://www.sec.gov/Archives/edgar/data/",
                "requested_as_of": as_of,
                "source_as_of": source_as_of(self.cover_identity_provider),
                "fetched_at": fetched_at(self.cover_identity_provider),
                "calls": int(getattr(self.cover_identity_provider, "calls", 0) or 0),
                "cache_hits": int(getattr(self.cover_identity_provider, "cache_hits", 0) or 0),
                "resolved": int(getattr(self.cover_identity_provider, "resolved", 0) or 0),
                "conflicted": int(getattr(self.cover_identity_provider, "conflicted", 0) or 0),
            },
            "sector": {
                "source": "SEC_SUBMISSIONS",
                "source_url": getattr(self.sector_provider, "URL", "") if self.sector_provider else "",
                "requested_as_of": as_of,
                "source_as_of": source_as_of(self.sector_provider),
                "fetched_at": fetched_at(self.sector_provider),
                "bulk_downloads": int(getattr(self.sector_provider, "bulk_downloads", 0) or 0),
                "individual_fallback_calls": int(getattr(self.sector_provider, "individual_calls", 0) or 0),
                "fallback_cache_hits": int(getattr(self.sector_provider, "fallback_cache_hits", 0) or 0),
                "bulk_error_reason_code": str(getattr(self.sector_provider, "bulk_error_reason_code", "") or ""),
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
        fallback_sector = SECSubmissionsMetadataProvider(
            self.user_agent, self.sector_cache_dir,
            max_requests=int(self.config.get("discovery", {}).get("bootstrap", {}).get(
                "max_issuer_metadata_requests", 10_000)),
            max_rps=float(self.config.get("sec_max_rps", 4)))
        sector = SECSubmissionsCompositeMetadataProvider(
            SECSubmissionsBulkMetadataProvider(self.user_agent, self.raw_cache_dir), fallback_sector)
        bootstrap = self.config.get("discovery", {}).get("bootstrap", {})
        bulk_provider = sector.bulk
        cover_provider = SECPeriodicCoverIdentityProvider(
            self.user_agent, bulk_provider,
            self.raw_cache_dir / "sec_cover",
            max_requests=int(bootstrap.get("max_cover_filing_requests", 2_500)),
            max_rps=float(self.config.get("sec_max_rps", 4)),
        )
        self._builder = SecurityMasterBootstrapBuilder(
            listing, security_types, sector, self.snapshot_path, self.raw_cache_dir,
            self.normalized_cache_dir,
            supported_exchanges=UniverseIntegrityEngine.DEFAULT_EXCHANGES,
            min_accepted=int(bootstrap.get("min_accepted", 1)),
            min_identity_coverage_pct=float(bootstrap.get("min_identity_coverage_pct", 95)),
            min_sector_coverage_pct=float(bootstrap.get("min_sector_coverage_pct", 90)),
            cover_identity_provider=cover_provider,
        )
        return self._builder

    def bootstrap(self, refresh: bool = False) -> dict[str, Any]:
        return self.builder().build_and_write(refresh=refresh)

    def diagnostic_candidate_records(self) -> tuple[list[SecurityMasterRecord], dict[str, Any]]:
        """Return only fully validated candidate rows for diagnostic shadow.

        This deliberately does not publish or promote the candidate snapshot.
        It is a downstream plumbing smoke while the production coverage gate
        remains fail-closed.
        """
        candidate_path = self.snapshot_path.with_name("security_master_candidate.json")
        if not candidate_path.is_file():
            return [], {"status": "BOOTSTRAP_REQUIRED", "reason_codes": ["CANDIDATE_SNAPSHOT_MISSING"]}
        payload = read_snapshot(candidate_path)
        records = snapshot_records(payload or {})
        supported = set(UniverseIntegrityEngine.DEFAULT_EXCHANGES)
        selected = [record for record in records
                    if record.exchange.upper() in supported
                    and record.is_common_stock is True
                    and not bool(getattr(record, "identity_conflicted", False))
                    and all(getattr(record, flag) is not None for flag in IDENTITY_FLAGS)]
        return selected, {
            "status": "DEGRADED_DIAGNOSTIC_SHADOW_READY" if selected else "BOOTSTRAP_REQUIRED",
            "candidate_status": str(payload.get("status", "")) if payload else "",
            "candidate_count": len(records),
            "selected_count": len(selected),
            "reason_codes": [] if selected else ["NO_NON_CONFLICTED_VALIDATED_CANDIDATES"],
            "production_threshold_bypassed": False,
            "source": "SECURITY_MASTER_CANDIDATE_ONLY",
        }

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
        configured_market_provider = self.config.get(
            "market_data_provider", self.config.get("provider", "toss"))
        if not credentials.get("toss_app_key") or not credentials.get("toss_app_secret"):
            reasons.append("TOSS_CREDENTIALS_REQUIRED")
        # ``load_config`` may retain the application's normal mock default even
        # when the operator supplied real Toss credentials for this operational
        # health command.  Health must report the actual configured transport,
        # not the unrelated analysis default; credentials remain the gate.
        elif configured_market_provider == "toss" or (
                configured_market_provider == "mock"
                and credentials.get("toss_app_key")
                and credentials.get("toss_app_secret")):
            from ..toss import TossClient
            from .providers_live import TossDiscoveryBenchmarkProvider, TossDiscoveryMarketDataProvider
            market_data = TossDiscoveryMarketDataProvider(
                TossClient(credentials.get("toss_app_key", ""), credentials.get("toss_app_secret", "")))
            benchmark_provider = TossDiscoveryBenchmarkProvider(market_data)
        fundamental = None
        capital = None
        if not self.user_agent:
            reasons.append("SEC_USER_AGENT_REQUIRED")
        else:
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
        candidate_path = self.snapshot_path.with_name("security_master_candidate.json")
        diagnostics_path = self.snapshot_path.with_name("security_master_build_diagnostics.json")
        candidate = None
        diagnostics = None
        try:
            candidate = read_snapshot(candidate_path) if candidate_path.is_file() else None
        except SecurityMasterBootstrapError:
            candidate = None
        try:
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8")) \
                if diagnostics_path.is_file() else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            diagnostics = None
        result["publication"] = {
            "active_status": "ACTIVE" if payload is not None else "MISSING",
            "candidate_status": candidate.get("status", "") if candidate else "",
            "candidate_metrics": candidate.get("metrics", {}) if candidate else {},
            "diagnostics": diagnostics or {},
            "active_lkg_preserved": payload is not None,
        }
        result["credentials"] = {
            "sec_user_agent": "READY" if self.user_agent else "BLOCKED",
            "toss": "READY" if credentials.get("toss_app_key") and credentials.get("toss_app_secret") else "BLOCKED",
        }
        result["provider_readiness"] = {
            "security_master": {
                "configured": True,
                "constructed": True,
                "sample_executed": bool(records),
                "sample_ready": bool(result.get("security_master", False)),
                "blocked_by": (
                    "SNAPSHOT_MISSING"
                    if not payload and not candidate
                    else str((candidate or {}).get("status") or "SNAPSHOT_MISSING")
                    if not payload else ""
                ),
            },
            "toss": {
                "configured": bool(credentials.get("toss_app_key") and credentials.get("toss_app_secret")),
                "constructed": market_data is not None,
                "auth_probe": "NOT_EXECUTED",
                "transport_probe": "NOT_EXECUTED",
                "transport": "NOT_EXECUTED",
                "sample_executed": bool(records and market_data is not None),
                "benchmark_sample_executed": False,
                "sample_ready": bool(result.get("market_data", False)),
                "blocked_by": (
                    "TOSS_CREDENTIALS_REQUIRED" if not credentials.get("toss_app_key") or not credentials.get("toss_app_secret")
                    else "SECURITY_MASTER_NOT_READY" if not records else ""),
            },
            "fundamental": {
                "configured": bool(self.user_agent),
                "constructed": fundamental is not None,
                "auth_probe": "NOT_EXECUTED",
                "transport_probe": "NOT_EXECUTED",
                "sample_executed": bool(result.get("market_scan_status") == "MARKET_SCAN_READY" and fundamental is not None),
                "sample_ready": bool(result.get("fundamental_data", False)),
                "blocked_by": "SECURITY_MASTER_NOT_READY" if not records else "" if result.get("market_scan_status") == "MARKET_SCAN_READY" else "MARKET_NOT_READY",
            },
            "capital": {
                "configured": bool(self.user_agent),
                "constructed": capital is not None,
                "auth_probe": "NOT_EXECUTED",
                "transport_probe": "NOT_EXECUTED",
                "sample_executed": bool(result.get("market_scan_status") == "MARKET_SCAN_READY" and capital is not None),
                "sample_ready": bool(result.get("capital_preflight_data", False)),
                "blocked_by": "SECURITY_MASTER_NOT_READY" if not records else "" if result.get("market_scan_status") == "MARKET_SCAN_READY" else "MARKET_NOT_READY",
            },
        }
        if fundamental is not None:
            result["reason_codes"] = [code for code in result.get("reason_codes", [])
                                       if code != "FUNDAMENTAL_PROVIDER_MISSING"]
            if not records:
                result["reason_codes"].append("FUNDAMENTAL_SAMPLE_BLOCKED_SECURITY_MASTER")
        if capital is not None:
            result["reason_codes"] = [code for code in result.get("reason_codes", [])
                                       if code != "CAPITAL_PREFLIGHT_PROVIDER_MISSING"]
            if not records:
                result["reason_codes"].append("CAPITAL_SAMPLE_BLOCKED_SECURITY_MASTER")
        if records and market_data is not None:
            result["provider_readiness"]["toss"]["sample_executed"] = True
            result["provider_readiness"]["toss"]["transport_probe"] = (
                "PASS" if result.get("market_data") else "FAILED")
            result["provider_readiness"]["toss"]["transport"] = result["provider_readiness"]["toss"]["transport_probe"]
        if market_data is not None:
            benchmark_probe = bool(result.get("benchmark"))
            result["provider_readiness"]["toss"]["benchmark_sample_executed"] = benchmark_probe
            result["provider_readiness"]["toss"]["transport_probe"] = (
                "PASS" if result.get("benchmark_data") else "FAILED")
            result["provider_readiness"]["toss"]["transport"] = result["provider_readiness"]["toss"]["transport_probe"]
        if not records and market_data is not None:
            result["reason_codes"] = [code for code in result.get("reason_codes", [])
                                       if code != "MARKET_DATA_SAMPLE_UNAVAILABLE"]
            result["reason_codes"].append("CANDIDATE_QUOTE_SAMPLE_BLOCKED_SECURITY_MASTER")
        if not records and fundamental is not None:
            result["reason_codes"] = [code for code in result.get("reason_codes", [])
                                       if code != "FUNDAMENTAL_BLOCKED_MARKET_BOOTSTRAP"]
        if not records and capital is not None:
            result["reason_codes"] = [code for code in result.get("reason_codes", [])
                                       if code != "CAPITAL_PREFLIGHT_BLOCKED_MARKET_BOOTSTRAP"]
        result["reason_codes"] = sorted(set(result.get("reason_codes", []) + reasons))
        if result.get("status") == "DEEP_HANDOFF_READY" and reasons:
            result["status"] = "BOOTSTRAP_REQUIRED"
        result["command"] = "discovery-health"
        return result
