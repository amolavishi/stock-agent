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
        tickãn=¶‰žËkºwµçmô°€‰½¹™±¥Ñ•ˆè…±Í”°(€€€€€€€€€€€€‰Í½ÕÉ•ÌˆèÉ½ÕÀ°(€€€€€€€ô(€€€€€€€™½È™±…œ¥¸%9Q%Qe}1Lè(€€€€€€€€€€€Ù…±Õ•Ì€ôíÉ½Ü¹•Ð ‰¥‘•¹Ñ¥Ñäˆ°íô¤¹•Ð¡™±…œ¤™½ÈÉ½Ü¥¸É½ÕÀ(€€€€€€€€€€€€€€€€€€€€€¥˜É½Ü¹•Ð ‰¥‘•¹Ñ¥Ñäˆ°íô¤¹•Ð¡™±…œ¤¥Ì¹½Ð9½¹•ô(€€€€€€€€€€€¥˜±•¸¡Ù…±Õ•Ì¤€ø€Äè(€€€€€€€€€€€€€€€µ•É•‘l‰¥‘•¹Ñ¥Ñä‰um™±…t€ô9½¹”(€€€€€€€€€€€€€€€µ•É•‘l‰½¹™±¥Ñ•‰t€ôQÉÕ”(€€€€€€€€€€€•±¥˜Ù…±Õ•Ìè(€€€€€€€€€€€€€€€µ•É•‘l‰¥‘•¹Ñ¥Ñä‰um™±…t€ô¹•áÐ¡¥Ñ•È¡Ù…±Õ•Ì¤¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€µ•É•‘l‰¥‘•¹Ñ¥Ñä‰um™±…t€ô9½¹”(€€€€€€€€€€€µ•É•‘l‰ÁÉ½Ù•¹…¹”‰um™±…t€ômì(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆèÉ½Ü¹•Ð ‰Í½ÕÉ”ˆ°€ˆˆ¤°€‰Í½ÕÉ•}ÕÉ°ˆèÉ½Ü¹•Ð ‰Í½ÕÉ•}ÕÉ°ˆ°€ˆˆ¤°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}…Í}½˜ˆèÉ½Ü¹•Ð ‰Í½ÕÉ•}…Í}½˜ˆ°€ˆˆ¤°(€€€€€€€€€€€€€€€€‰Ù…±Õ”ˆèÉ½Ü¹•Ð ‰¥‘•¹Ñ¥Ñäˆ°íô¤¹•Ð¡™±…œ¤°(€€€€€€€€€€€ô™½ÈÉ½Ü¥¸É½ÕÀ¥˜É½Ü¹•Ð ‰¥‘•¹Ñ¥Ñäˆ°íô¤¹•Ð¡™±…œ¤¥Ì¹½Ð9½¹•t(€€€€€€€…Ñ•½É¥•Ì€ôíÍÑÈ¡É½Ü¹•Ð ‰Í•ÕÉ¥Ñå}ÑåÁ”ˆ¤½È€‰U9-9=]8ˆ¤™½ÈÉ½Ü¥¸É½ÕÁô(€€€€€€€­¹½Ý¹}…Ñ•½É¥•Ì€ô…Ñ•½É¥•Ì€´ì‰U9-9=]8‰ô(€€€€€€€¥˜±•¸¡­¹½Ý¹}…Ñ•½É¥•Ì¤€ôô€Äè(€€€€€€€€€€€µ•É•‘l‰Í•ÕÉ¥Ñå}ÑåÁ”‰t€ô¹•áÐ¡¥Ñ•È¡­¹½Ý¹}…Ñ•½É¥•Ì¤¤(€€€€€€€•±¥˜±•¸¡­¹½Ý¹}…Ñ•½É¥•Ì¤€ø€Äè(€€€€€€€€€€€µ•É•‘l‰Í•ÕÉ¥Ñå}ÑåÁ”‰t€ô€‰U9-9=]8ˆ(€€€€€€€€€€€µ•É•‘l‰½¹™±¥Ñ•‰t€ôQÉÕ”(€€€€€€€É•ÑÕÉ¸µ•É•((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}É½Ý}™É½µ}‰…Í”¡‰…Í”èM•ÕÉ¥Ñå5…ÍÑ•ÉI•½É°µ•É•è‘¥ÑmÍÑÈ°¹åt°…Í}½˜èÍÑÈ¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€¥‘•¹Ñ¥Ñä€ôµ•É•‘l‰¥‘•¹Ñ¥Ñä‰t(€€€€€€€½¹™±¥Ð€ô‰½½°¡µ•É•¹•Ð ‰½¹™±¥Ñ•ˆ¤¤(€€€€€€€ÍÑ…Ñ•Ì€ôì(€€€€€€€€€€€™±…œèì‰ÍÑ…Ñ”ˆè€‰U9-9=]9}=91%Qˆ¥˜½¹™±¥Ð…¹¥‘•¹Ñ¥Ñä¹•Ð¡™±…œ¤¥Ì9½¹”(€€€€€€€€€€€€€€€€€€•±Í”€‰-9=]8ˆ¥˜¥‘•¹Ñ¥Ñä¹•Ð¡™±…œ¤¥Ì¹½Ð9½¹”•±Í”€‰U9-9=]9}9=Q}Y%1	1‰ô(€€€€€€€€€€€™½È™±…œ¥¸%9Q%Qe}1L(€€€€€€€ô(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰Í•ÕÉ¥Ñå}¥ˆè‰…Í”¹Í•ÕÉ¥Ñå}¥°(€€€€€€€€€€€€‰Ñ¥­•Èˆè‰…Í”¹Ñ¥­•È°(€€€€€€€€€€€€‰½µÁ…¹å}¹…µ”ˆè‰…Í”¹½µÁ…¹å}¹…µ”°(€€€€€€€€€€€€‰¥¬ˆè}¹½Éµ…±¥Í•}¥¬¡‰…Í”¹¥¬¤°(€€€€€€€€€€€€‰•á¡…¹”ˆè}¹½Éµ…±¥Í•}•á¡…¹”¡‰…Í”¹•á¡…¹”¤°(€€€€€€€€€€€€‰½Õ¹ÑÉäˆè‰…Í”¹½Õ¹ÑÉä°(€€€€€€€€€€€€‰…Ñ¥Ù•}ÍÑ…ÑÕÌˆè‰…Í”¹…Ñ¥Ù•}ÍÑ…ÑÕÌ°(€€€€€€€€€€€€‰Í•ÕÉ¥Ñå}ÑåÁ”ˆèµ•É•¹•Ð ‰Í•ÕÉ¥Ñå}ÑåÁ”ˆ°€‰U9-9=]8ˆ¤°(€€€€€€€€€€€€¨©¥‘•¹Ñ¥Ñä°(€€€€€€€€€€€€‰Í•Ñ½É}…¹½¹¥…°ˆè€‰U9-9=]8ˆ°(€€€€€€€€€€€€‰¥¹‘ÕÍÑÉå}…¹½¹¥…°ˆè€‰U9-9=]8ˆ°(€€€€€€€€€€€€‰Í¥Œˆè€ˆˆ°(€€€€€€€€€€€€‰Í¥}‘•ÍÉ¥ÁÑ¥½¸ˆè€ˆˆ°(€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰M}%IQ=Id­9ME}QIHˆ°(€€€€€€€€€€€€‰Í½ÕÉ•}…Í}½˜ˆè…Í}½˜°(€€€€€€€€€€€€‰¥¹•ÍÑ•‘}…Ðˆè}¹½Ü ¤°(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}ÍÑ…Ñ•ÌˆèÍÑ…Ñ•Ì°(€€€€€€€€€€€€‰ÁÉ½Ù•¹…¹”ˆèµ•É•¹•Ð ‰ÁÉ½Ù•¹…¹”ˆ°íô¤°(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}½¹™±¥Ñ•ˆè½¹™±¥Ð°(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}Í½ÕÉ•Ìˆèl(€€€€€€€€€€€€€€€ì‰Í½ÕÉ”ˆèÉ½Ü¹•Ð ‰Í½ÕÉ”ˆ°€ˆˆ¤°€‰Í½ÕÉ•}ÕÉ°ˆèÉ½Ü¹•Ð ‰Í½ÕÉ•}ÕÉ°ˆ°€ˆˆ¤°(€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}…Í}½˜ˆèÉ½Ü¹•Ð ‰Í½ÕÉ•}…Í}½˜ˆ°€ˆˆ¥ô(€€€€€€€€€€€€€€€™½ÈÉ½Ü¥¸µ•É•¹•Ð ‰Í½ÕÉ•Ìˆ°mt¤(€€€€€€€€€€€t°(€€€€€€€ô((€€€‘•˜}µ•ÑÉ¥Ì¡Í•±˜°Á…å±½…è‘¥ÑmÍÑÈ°¹åt¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€É½ÝÌ€ôÁ…å±½…‘l‰É•½É‘Ì‰t(€€€€€€€É•½É‘Ì€ôm}É•½É‘}™É½µ}É½Ü¡É½Ü¤™½ÈÉ½Ü¥¸É½ÝÍt(€€€€€€€ÍÕÁÁ½ÉÑ•€ômÉ½Ü™½ÈÉ½Ü¥¸É•½É‘Ì¥˜É½Ü¹•á¡…¹”¹ÕÁÁ•È ¤¥¸Í•±˜¹ÍÕÁÁ½ÉÑ•‘}•á¡…¹•Ít(€€€€€€€¥‘•¹Ñ¥Ñå}­¹½Ý¹}±½‰…°€ôÍÕ´¡Í•±˜¹}¥‘•¹Ñ¥Ñå}­¹½Ý¸¡É½Ü¤™½ÈÉ½Ü¥¸É•½É‘Ì¤(€€€€€€€¥‘•¹Ñ¥Ñå}­¹½Ý¹}ÍÕÁÁ½ÉÑ•€ôÍÕ´¡Í•±˜¹}¥‘•¹Ñ¥Ñå}­¹½Ý¸¡É½Ü¤™½ÈÉ½Ü¥¸ÍÕÁÁ½ÉÑ•¤(€€€€€€€½¹™±¥Ñ}½Õ¹Ð€ôÍÕ´¡‰½½°¡É½Ü¹•Ð ‰¥‘•¹Ñ¥Ñå}½¹™±¥Ñ•ˆ¤¤™½ÈÉ½Ü¥¸É½ÝÌ¤(€€€€€€€¥¹Ñ•É¥Ñä€ôU¹¥Ù•ÉÍ•%¹Ñ•É¥Ñå¹¥¹”¡•á¡…¹•ÌõÍ•Ð¡Í•±˜¹ÍÕÁÁ½ÉÑ•‘}•á¡…¹•Ì¤¤¹‰Õ¥± (€€€€€€€€€€€%¹5•µ½ÉåM•ÕÉ¥Ñå5…ÍÑ•ÉAÉ½Ù¥‘•È¡É•½É‘Ì¤°Á…å±½…‘l‰Í½ÕÉ•}…Í}½˜‰t¤(€€€€€€€¡•…±Ñ €ô¥¹Ñ•É¥Ñål‰¡•…±Ñ ‰t(€€€€€€€…•ÁÑ•€ô¥¹Ñ•É¥Ñål‰É•½É‘Ì‰t(€€€€€€€Í•Ñ½É}­¹½Ý¸€ôÍÕ´¡É½Ü¹Í•Ñ½É}…¹½¹¥…°¹ÕÁÁ•È ¤€„ô€‰U9-9=]8ˆ™½ÈÉ½Ü¥¸…•ÁÑ•¤(€€€€€€€É…Ý}½Õ¹Ð€ô±•¸¡É•½É‘Ì¤(€€€€€€€ÍÕÁÁ½ÉÑ•‘}½Õ¹Ð€ô±•¸¡ÍÕÁÁ½ÉÑ•¤(€€€€€€€Í½ÕÉ•}µ…Ñ¡•Ì€ôÍÕ´¡‰½½°¡É½Ü¹•Ð ‰¥‘•¹Ñ¥Ñå}Í½ÕÉ•Ìˆ¤¤™½ÈÉ½Ü¥¸É½ÝÌ¤(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰É…Ý}½Õ¹ÐˆèÉ…Ý}½Õ¹Ð°(€€€€€€€€€€€€‰ÍÕÁÁ½ÉÑ•‘}•á¡…¹•}Í½Á•}½Õ¹ÐˆèÍÕÁÁ½ÉÑ•‘}½Õ¹Ð°(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}­¹½Ý¹}±½‰…±}½Õ¹Ðˆè¥‘•¹Ñ¥Ñå}­¹½Ý¹}±½‰…°°(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}½Ù•É…•}±½‰…±}ÁÐˆèÉ½Õ¹¡¥‘•¹Ñ¥Ñå}­¹½Ý¹}±½‰…°€¼É…Ý}½Õ¹Ð€¨€ÄÀÀ°€Ð¤¥˜É…Ý}½Õ¹Ð•±Í”€À¸À°(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}­¹½Ý¹}ÍÕÁÁ½ÉÑ•‘}½Õ¹Ðˆè¥‘•¹Ñ¥Ñå}­¹½Ý¹}ÍÕÁÁ½ÉÑ•°(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}½Ù•É…•}ÍÕÁÁ½ÉÑ•‘}Í½Á•}ÁÐˆèÉ½Õ¹ (€€€€€€€€€€€€€€€¥‘•¹Ñ¥Ñå}­¹½Ý¹}ÍÕÁÁ½ÉÑ•€¼ÍÕÁÁ½ÉÑ•‘}½Õ¹Ð€¨€ÄÀÀ°€Ð¤¥˜ÍÕÁÁ½ÉÑ•‘}½Õ¹Ð•±Í”€À¸À°(€€€€€€€€€€€€Œ	…­Ý…Éµ½µÁ…Ñ¥‰±”…±¥…ÌìÉ•…‘¥¹•ÍÌÕÍ•ÌÑ¡”•áÁ±¥¥ÐÍÕÁÁ½ÉÑ•µ•ÑÉ¥Œ¸(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}½Ù•É…•}ÁÐˆèÉ½Õ¹¡¥‘•¹Ñ¥Ñå}­¹½Ý¹}±½‰…°€¼É…Ý}½Õ¹Ð€¨€ÄÀÀ°€Ð¤¥˜É…Ý}½Õ¹Ð•±Í”€À¸À°(€€€€€€€€€€€€‰…•ÁÑ•‘}½µµ½¹}ÍÑ½­}½Õ¹Ðˆè±•¸¡…•ÁÑ•¤°(€€€€€€€€€€€€‰Í•Ñ½É}­¹½Ý¹}½Õ¹ÐˆèÍ•Ñ½É}­¹½Ý¸°(€€€€€€€€€€€€‰Í•Ñ½É}½Ù•É…•}ÁÐˆèÉ½Õ¹¡Í•Ñ½É}­¹½Ý¸€¼±•¸¡…•ÁÑ•¤€¨€ÄÀÀ°€Ð¤¥˜…•ÁÑ••±Í”€À¸À°(€€€€€€€€€€€€‰Õ¹­¹½Ý¹}¥‘•¹Ñ¥Ñå}½Õ¹ÐˆèÍÕ´¡¹½ÐÍ•±˜¹}¥‘•¹Ñ¥Ñå}­¹½Ý¸¡É½Ü¤™½ÈÉ½Ü¥¸É•½É‘Ì¤°(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}½¹™±¥Ñ}½Õ¹Ðˆè½¹™±¥Ñ}½Õ¹Ð°(€€€€€€€€€€€€‰‘ÕÁ±¥…Ñ•}½Õ¹Ðˆè¡•…±Ñ ¹•Ð ‰‘ÕÁ±¥…Ñ•}½Õ¹Ðˆ°€À¤°(€€€€€€€€€€€€‰É•©•Ñ¥½¹}½Õ¹ÑÌˆè‘¥Ð¡¥¹Ñ•É¥Ñä¹•Ð ‰É•©•Ñ•ˆ°íô¤¤°(€€€€€€€€€€€€‰Í½ÕÉ•}µ…Ñ¡•ÌˆèÍ½ÕÉ•}µ…Ñ¡•Ì°(€€€€€€€€€€€€‰Í½ÕÉ•}Õ¹µ…Ñ¡•ˆèÉ…Ý}½Õ¹Ð€´Í½ÕÉ•}µ…Ñ¡•Ì°(€€€€€€€€€€€€‰Í½ÕÉ•}½¹™±¥Ñ•ˆè½¹™±¥Ñ}½Õ¹Ð°(€€€€€€€€€€€€‰Í•ÕÉ¥Ñå}ÑåÁ•}Í½ÕÉ•}…±±Ìˆè¥¹Ð¡•Ñ…ÑÑÈ¡Í•±˜¹Í•ÕÉ¥Ñå}ÑåÁ•}ÁÉ½Ù¥‘•È°€‰…±±Ìˆ°€À¤½È€À¤°(€€€€€€€€€€€€‰Í•Ñ½É}Í½ÕÉ•}…±±Ìˆè¥¹Ð¡•Ñ…ÑÑÈ¡Í•±˜¹Í•Ñ½É}ÁÉ½Ù¥‘•È°€‰…±±Ìˆ°€À¤½È€À¤°(€€€€€€€€€€€€‰Í•Ñ½É}Í½ÕÉ•}™…¥±•ˆè¥¹Ð¡•Ñ…ÑÑÈ¡Í•±˜¹Í•Ñ½É}ÁÉ½Ù¥‘•È°€‰™…¥±•ˆ°€À¤½È€À¤°(€€€€€€€€€€€€‰Í•Ñ½É}Í½ÕÉ•}Õ¹µ…Ñ¡•ˆè¥¹Ð¡•Ñ…ÑÑÈ¡Í•±˜¹Í•Ñ½É}ÁÉ½Ù¥‘•È°€‰Õ¹µ…Ñ¡•ˆ°€À¤½È€À¤°(€€€€€€€€€€€€‰Í•ÕÉ¥Ñå}µ…ÍÑ•É}É•…‘äˆèÍ•±˜¹}É•…‘¥¹•ÍÍ}ÍÑ…ÑÕÌ¡ì(€€€€€€€€€€€€€€€€‰…•ÁÑ•‘}½µµ½¹}ÍÑ½­}½Õ¹Ðˆè±•¸¡…•ÁÑ•¤°(€€€€€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}½Ù•É…•}ÍÕÁÁ½ÉÑ•‘}Í½Á•}ÁÐˆèÉ½Õ¹ (€€€€€€€€€€€€€€€€€€€¥‘•¹Ñ¥Ñå}­¹½Ý¹}ÍÕÁÁ½ÉÑ•€¼ÍÕÁÁ½ÉÑ•‘}½Õ¹Ð€¨€ÄÀÀ°€Ð¤¥˜ÍÕÁÁ½ÉÑ•‘}½Õ¹Ð•±Í”€À¸À°(€€€€€€€€€€€€€€€€‰Í•Ñ½É}½Ù•É…•}ÁÐˆèÉ½Õ¹¡Í•Ñ½É}­¹½Ý¸€¼±•¸¡…•ÁÑ•¤€¨€ÄÀÀ°€Ð¤¥˜…•ÁÑ••±Í”€À¸À°(€€€€€€€€€€€ô¤€ôô€‰MUI%Qe}5MQI}Idˆ°(€€€€€€€ô((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}¥‘•¹Ñ¥Ñå}­¹½Ý¸¡É•½ÉèM•ÕÉ¥Ñå5…ÍÑ•ÉI•½É¤€´ø‰½½°è(€€€€€€€É•ÑÕÉ¸…±°¡•Ñ…ÑÑÈ¡É•½É°™±…œ¤¥Ì¹½Ð9½¹”™½È™±…œ¥¸%9Q%Qe}1L¤((€€€‘•˜}É•…‘¥¹•ÍÍ}ÍÑ…ÑÕÌ¡Í•±˜°µ•ÑÉ¥Ìè‘¥ÑmÍÑÈ°¹åt¤€´øÍÑÈè(€€€€€€€¥˜€¡µ•ÑÉ¥Ì¹•Ð ‰…•ÁÑ•‘}½µµ½¹}ÍÑ½­}½Õ¹Ðˆ°€À¤€øôÍ•±˜¹µ¥¹}…•ÁÑ•(€€€€€€€€€€€€€€€…¹µ•ÑÉ¥Ì¹•Ð ‰¥‘•¹Ñ¥Ñå}½Ù•É…•}ÍÕÁÁ½ÉÑ•‘}Í½Á•}ÁÐˆ°€À¸À¤€øôÍ•±˜¹µ¥¹}¥‘•¹Ñ¥Ñå}½Ù•É…•}ÁÐ(€€€€€€€€€€€€€€€…¹µ•ÑÉ¥Ì¹•Ð ‰Í•Ñ½É}½Ù•É…•}ÁÐˆ°€À¸À¤€øôÍ•±˜¹µ¥¹}Í•Ñ½É}½Ù•É…•}ÁÐ¤è(€€€€€€€€€€€É•ÑÕÉ¸€‰MUI%Qe}5MQI}Idˆ(€€€€€€€É•ÑÕÉ¸€‰MUI%Qe}5MQI}=YI}%9MU%%9Pˆ((€€€‘•˜}É•…‘¥¹•ÍÍ}É•…Í½¹Ì¡Í•±˜°µ•ÑÉ¥Ìè‘¥ÑmÍÑÈ°¹åt¤€´ø±¥ÍÑmÍÑÉtè(€€€€€€€É•…Í½¹Ì€ômt(€€€€€€€¥˜µ•ÑÉ¥Ì¹•Ð ‰…•ÁÑ•‘}½µµ½¹}ÍÑ½­}½Õ¹Ðˆ°€À¤€ðÍ•±˜¹µ¥¹}…•ÁÑ•è(€€€€€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ ‰MUI%Qe}5MQI}AQ}=U9Q}%9MU%%9Pˆ¤(€€€€€€€¥˜µ•ÑÉ¥Ì¹•Ð ‰¥‘•¹Ñ¥Ñå}½Ù•É…•}ÍÕÁÁ½ÉÑ•‘}Í½Á•}ÁÐˆ°€À¸À¤€ðÍ•±˜¹µ¥¹}¥‘•¹Ñ¥Ñå}½Ù•É…•}ÁÐè(€€€€€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ ‰MUI%Qe}5MQI}=YI}%9MU%%9Pˆ¤(€€€€€€€¥˜µ•ÑÉ¥Ì¹•Ð ‰Í•Ñ½É}½Ù•É…•}ÁÐˆ°€À¸À¤€ðÍ•±˜¹µ¥¹}Í•Ñ½É}½Ù•É…•}ÁÐè(€€€€€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ ‰MUI%Qe}5MQI}MQ=I}=YI}%9MU%%9Pˆ¤(€€€€€€€É•ÑÕÉ¸É•…Í½¹Ì((€€€‘•˜}Í½ÕÉ•}µ•Ñ…‘…Ñ„¡Í•±˜°…Í}½˜èÍÑÈ¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰‰…Í•±¥¹”ˆèì(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰M}%IQ=Idˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆè•Ñ…ÑÑÈ¡Í•±˜¹±¥ÍÑ¥¹}ÁÉ½Ù¥‘•È°€‰UI0ˆ°€ˆˆ¤°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}…Í}½˜ˆè…Í}½˜°(€€€€€€€€€€€ô°(€€€€€€€€€€€€‰Í•ÕÉ¥Ñå}ÑåÁ”ˆèì(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰9ME}QII}Me5	=1}%IQ=Idˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}ÕÉ±Ìˆè±¥ÍÐ¡•Ñ…ÑÑÈ¡Í•±˜¹Í•ÕÉ¥Ñå}ÑåÁ•}ÁÉ½Ù¥‘•È°€‰M=UILˆ°íô¤¹Ù…±Õ•Ì ¤¤°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}…Í}½˜ˆè…Í}½˜°(€€€€€€€€€€€ô°(€€€€€€€€€€€€‰Í•Ñ½Èˆèì(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰M}MU	5%MM%=9Lˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆè•Ñ…ÑÑÈ¡Í•±˜¹Í•Ñ½É}ÁÉ½Ù¥‘•È°€‰UI0ˆ°€ˆˆ¤¥˜Í•±˜¹Í•Ñ½É}ÁÉ½Ù¥‘•È•±Í”€ˆˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}…Í}½˜ˆè…Í}½˜°(€€€€€€€€€€€ô°(€€€€€€€ô(()‘•˜Í¹…ÁÍ¡½Ñ}É•½É‘Ì¡Á…å±½…è‘¥ÑmÍÑÈ°¹åt¤€´ø±¥ÍÑmM•ÕÉ¥Ñå5…ÍÑ•ÉI•½É‘tè(€€€Ù…±¥‘…Ñ•}Í¹…ÁÍ¡½Ð¡Á…å±½…¤(€€€É•ÑÕÉ¸m}É•½É‘}™É½µ}É½Ü¡É½Ü¤™½ÈÉ½Ü¥¸Á…å±½…¹•Ð ‰É•½É‘Ìˆ°mt¥t(()‘•˜É•…‘}Í¹…ÁÍ¡½Ð¡Á…Ñ èÍÑÈðA…Ñ ¤€´ø‘¥ÑmÍÑÈ°¹åtð9½¹”è(€€€Ñ…É•Ð€ôA…Ñ ¡Á…Ñ ¤(€€€¥˜¹½ÐÑ…É•Ð¹¥Í}™¥±” ¤è(€€€€€€€É•ÑÕÉ¸9½¹”(€€€ÑÉäè(€€€€€€€Á…å±½…€ô©Í½¸¹±½…‘Ì¡Ñ…É•Ð¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤¤(€€€•á•ÁÐ€¡=MÉÉ½È°U¹¥½‘••½‘•ÉÉ½È°©Í½¸¹)M=9•½‘•ÉÉ½È¤…Ì•áŒè(€€€€€€€É…¥Í”M•ÕÉ¥Ñå5…ÍÑ•É	½½ÑÍÑÉ…ÁÉÉ½È ‰MUI%Qe}5MQI}M9AM!=Q}U9I	1ˆ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ(€€€Ù…±¥‘…Ñ•}Í¹…ÁÍ¡½Ð¡Á…å±½…¤(€€€Á…å±½…‘l‰Í¹…ÁÍ¡½Ñ}Á…Ñ ‰t€ôÍÑÈ¡Ñ…É•Ð¤(€€€Á…å±½…‘l‰Í¹…ÁÍ¡½Ñ}™¥±•}µÑ¥µ”‰t€ô‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡Ñ…É•Ð¹ÍÑ…Ð ¤¹ÍÑ}µÑ¥µ”°Ñ¥µ•é½¹”¹ÕÑŒ¤¹¥Í½™½Éµ…Ð ¤(€€€É•ÑÕÉ¸Á…å±½…(()±…ÍÌM•ÕÉ¥Ñå5…ÍÑ•É	½½ÑÍÑÉ…ÁM•ÉÙ¥”è(€€€€ˆˆ‰½¹™¥ÕÉ…Ñ¥½¸µ‰½Õ¹Í•ÉÙ¥”ÕÍ•‰ä‰½½ÑÍÑÉ…À½É•™É•Í ½¡•…±Ñ 1$½µµ…¹‘Ì¸ˆˆˆ((€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°½¹™¥œè‘¥ÑmÍÑÈ°¹åt¤è(€€€€€€€Í•±˜¹½¹™¥œ€ô½¹™¥œ(€€€€€€€‘¥Í½Ù•Éä€ô½¹™¥œ¹•Ð ‰‘¥Í½Ù•Éäˆ°íô¤(€€€€€€€‰½½ÑÍÑÉ…À€ô‘¥Í½Ù•Éä¹•Ð ‰‰½½ÑÍÑÉ…Àˆ°íô¤(€€€€€€€É•‘•¹Ñ¥…±Ì€ô½¹™¥œ¹•Ð ‰É•‘•¹Ñ¥…±Ìˆ°íô¤(€€€€€€€Í•±˜¹ÕÍ•É}…•¹Ð€ôÍÑÈ¡É•‘•¹Ñ¥…±Ì¹•Ð ‰Í•}ÕÍ•É}…•¹Ðˆ°€ˆˆ¤¤(€€€€€€€Í•±˜¹Í¹…ÁÍ¡½Ñ}Á…Ñ €ôA…Ñ ¡‰½½ÑÍÑÉ…À¹•Ð ‰Í•ÕÉ¥Ñå}µ…ÍÑ•É}•¹É¥¡µ•¹Ñ}Á…Ñ ˆ°€ˆˆ¤¤(€€€€€€€Í•±˜¹É…Ý}…¡•}‘¥È€ôA…Ñ ¡‰½½ÑÍÑÉ…À¹•Ð ‰É…Ý}…¡•}‘¥Èˆ°€‰‘…Ñ„½…¡”½‘¥Í½Ù•Éä½Í•ÕÉ¥Ñå}µ…ÍÑ•È½É…Üˆ¤¤(€€€€€€€Í•±˜¹¹½Éµ…±¥é•‘}…¡•}‘¥È€ôA…Ñ ¡‰½½ÑÍÑÉ…À¹•Ð ‰¹½Éµ…±¥é•‘}…¡•}‘¥Èˆ°€‰‘…Ñ„½…¡”½‘¥Í½Ù•Éä½Í•ÕÉ¥Ñå}µ…ÍÑ•È½¹½Éµ…±¥é•ˆ¤¤(€€€€€€€Í•±˜¹Í•Ñ½É}…¡•}‘¥È€ôA…Ñ ¡‰½½ÑÍÑÉ…À¹•Ð ‰Í•Ñ½É}…¡•}‘¥Èˆ°ÍÑÈ¡Í•±˜¹É…Ý}…¡•}‘¥È€¼€‰Í•}ÍÕ‰µ¥ÍÍ¥½¹Ìˆ¤¤¤(€€€€€€€Í•±˜¹}‰Õ¥±‘•ÈèM•ÕÉ¥Ñå5…ÍÑ•É	½½ÑÍÑÉ…Á	Õ¥±‘•Èð9½¹”€ô9½¹”((€€€‘•˜‰Õ¥±‘•È¡Í•±˜¤€´øM•ÕÉ¥Ñå5…ÍÑ•É	½½ÑÍÑÉ…Á	Õ¥±‘•Èè(€€€€€€€¥˜Í•±˜¹}‰Õ¥±‘•È¥Ì¹½Ð9½¹”è(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}‰Õ¥±‘•È(€€€€€€€¥˜¹½ÐÍ•±˜¹ÕÍ•É}…•¹Ðè(€€€€€€€€€€€É…¥Í”M•ÕÉ¥Ñå5…ÍÑ•É	½½ÑÍÑÉ…ÁÉÉ½È ‰M}UMI}9Q}IEU%Iˆ¤(€€€€€€€™É½´€¹ÁÉ½Ù¥‘•ÉÍ}±¥Ù”¥µÁ½ÉÐM½µÁ…¹åQ¥­•ÉM•ÕÉ¥Ñå5…ÍÑ•ÉAÉ½Ù¥‘•È(€€€€€€€±¥ÍÑ¥¹œ€ôM½µÁ…¹åQ¥­•ÉM•ÕÉ¥Ñå5…ÍÑ•ÉAÉ½Ù¥‘•È (€€€€€€€€€€€Í•±˜¹ÕÍ•É}…•¹Ð°Í•±˜¹É…Ý}…¡•}‘¥È€¼€‰½µÁ…¹å}Ñ¥­•ÉÍ}•á¡…¹”¹©Í½¸ˆ¤(€€€€€€€Í•ÕÉ¥Ñå}ÑåÁ•Ì€ô9…Í‘…ÅQÉ…‘•ÉM•ÕÉ¥ÑåQåÁ•AÉ½Ù¥‘•È¡Í•±˜¹É…Ý}…¡•}‘¥È€¼€‰¹…Í‘…Å}ÑÉ…‘•Èˆ¤(€€€€€€€Í•Ñ½È€ôMMÕ‰µ¥ÍÍ¥½¹Í5•Ñ…‘…Ñ…AÉ½Ù¥‘•È (€€€€€€€€€€€Í•±˜¹ÕÍ•É}…•¹Ð°Í•±˜¹Í•Ñ½É}…¡•}‘¥È°(€€€€€€€€€€€µ…á}É•ÅÕ•ÍÑÌõ¥¹Ð¡Í•±˜¹½¹™¥œ¹•Ð ‰‘¥Í½Ù•Éäˆ°íô¤¹•Ð ‰‰½½ÑÍÑÉ…Àˆ°íô¤¹•Ð (€€€€€€€€€€€€€€€€‰µ…á}¥ÍÍÕ•É}µ•Ñ…‘…Ñ…}É•ÅÕ•ÍÑÌˆ°€ÄÁ|ÀÀÀ¤¤°(€€€€€€€€€€€µ…á}ÉÁÌõ™±½…Ð¡Í•±˜¹½¹™¥œ¹•Ð ‰Í•}µ…á}ÉÁÌˆ°€Ð¤¤¤(€€€€€€€‰½½ÑÍÑÉ…À€ôÍ•±˜¹½¹™¥œ¹•Ð ‰‘¥Í½Ù•Éäˆ°íô¤¹•Ð ‰‰½½ÑÍÑÉ…Àˆ°íô¤(€€€€€€€Í•±˜¹}‰Õ¥±‘•È€ôM•ÕÉ¥Ñå5…ÍÑ•É	½½ÑÍÑÉ…Á	Õ¥±‘•È (€€€€€€€€€€€±¥ÍÑ¥¹œ°Í•ÕÉ¥Ñå}ÑåÁ•Ì°Í•Ñ½È°Í•±˜¹Í¹…ÁÍ¡½Ñ}Á…Ñ °Í•±˜¹É…Ý}…¡•}‘¥È°(€€€€€€€€€€€Í•±˜¹¹½Éµ…±¥é•‘}…¡•}‘¥È°(€€€€€€€€€€€ÍÕÁÁ½ÉÑ•‘}•á¡…¹•ÌõU¹¥Ù•ÉÍ•%¹Ñ•É¥Ñå¹¥¹”¹U1Q}a!9L°(€€€€€€€€€€€µ¥¹}…•ÁÑ•õ¥¹Ð¡‰½½ÑÍÑÉ…À¹•Ð ‰µ¥¹}…•ÁÑ•ˆ°€Ä¤¤°(€€€€€€€€€€€µ¥¹}¥‘•¹Ñ¥Ñå}½Ù•É…•}ÁÐõ™±½…Ð¡‰½½ÑÍÑÉ…À¹•Ð ‰µ¥¹}¥‘•¹Ñ¥Ñå}½Ù•É…•}ÁÐˆ°€äÔ¤¤°(€€€€€€€€€€€µ¥¹}Í•Ñ½É}½Ù•É…•}ÁÐõ™±½…Ð¡‰½½ÑÍÑÉ…À¹•Ð ‰µ¥¹}Í•Ñ½É}½Ù•É…•}ÁÐˆ°€äÀ¤¤°(€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸Í•±˜¹}‰Õ¥±‘•È((€€€‘•˜‰½½ÑÍÑÉ…À¡Í•±˜°É•™É•Í è‰½½°€ô…±Í”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€É•ÑÕÉ¸Í•±˜¹‰Õ¥±‘•È ¤¹‰Õ¥±‘}…¹‘}ÝÉ¥Ñ”¡É•™É•Í õÉ•™É•Í ¤((€€€‘•˜¡•…±Ñ ¡Í•±˜°‘…Ñ…‰…Í”õ9½¹”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€€ˆˆ‰I•…µ½¹±ä¡•…±Ñ è¥Ð¹•Ù•ÈÉ•…Ñ•Ì½ÈÉ•Á±…•Ì„Í¹…ÁÍ¡½Ð¸ˆˆˆ(€€€€€€€™É½´€¹¡•…±Ñ ¥µÁ½ÉÐ‰½½ÑÍÑÉ…Á}¡•…±Ñ ((€€€€€€€Á…å±½…€ôÉ•…‘}Í¹…ÁÍ¡½Ð¡Í•±˜¹Í¹…ÁÍ¡½Ñ}Á…Ñ ¤¥˜Í•±˜¹Í¹…ÁÍ¡½Ñ}Á…Ñ ¹¥Í}™¥±” ¤•±Í”9½¹”(€€€€€€€É•…Í½¹Ìè±¥ÍÑmÍÑÉt€ômt(€€€€€€€¥˜Á…å±½…¥Ì9½¹”è(€€€€€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ ‰MUI%Qe}5MQI}M9AM!=Q}5%MM%9ˆ¤(€€€€€€€€€€€É•½É‘Ì€ômt(€€€€€€€•±Í”è(€€€€€€€€€€€É•½É‘Ì€ôÍ¹…ÁÍ¡½Ñ}É•½É‘Ì¡Á…å±½…¤(€€€€€€€™É½´€¹Õ¹¥Ù•ÉÍ”¥µÁ½ÉÐ%¹5•µ½ÉåM•ÕÉ¥Ñå5…ÍÑ•ÉAÉ½Ù¥‘•È(€€€€€€€Í•ÕÉ¥Ñå}µ…ÍÑ•È€ô%¹5•µ½ÉåM•ÕÉ¥Ñå5…ÍÑ•ÉAÉ½Ù¥‘•È¡É•½É‘Ì¤((€€€€€€€É•‘•¹Ñ¥…±Ì€ôÍ•±˜¹½¹™¥œ¹•Ð ‰É•‘•¹Ñ¥…±Ìˆ°íô¤(€€€€€€€µ…É­•Ñ}‘…Ñ„€ô9½¹”(€€€€€€€‰•¹¡µ…É­}ÁÉ½Ù¥‘•È€ô9½¹”(€€€€€€€¥˜¹½ÐÉ•‘•¹Ñ¥…±Ì¹•Ð ‰Ñ½ÍÍ}…ÁÁ}­•äˆ¤½È¹½ÐÉ•‘•¹Ñ¥…±Ì¹•Ð ‰Ñ½ÍÍ}…ÁÁ}Í•É•Ðˆ¤è(€€€€€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ ‰Q=MM}I9Q%1M}IEU%Iˆ¤(€€€€€€€•±¥˜Í•±˜¹½¹™¥œ¹•Ð ‰µ…É­•Ñ}‘…Ñ…}ÁÉ½Ù¥‘•Èˆ°Í•±˜¹½¹™¥œ¹•Ð ‰ÁÉ½Ù¥‘•Èˆ°€‰Ñ½ÍÌˆ¤¤€ôô€‰Ñ½ÍÌˆè(€€€€€€€€€€€™É½´€¸¹Ñ½ÍÌ¥µÁ½ÉÐQ½ÍÍ±¥•¹Ð(€€€€€€€€€€€™É½´€¹ÁÉ½Ù¥‘•ÉÍ}±¥Ù”¥µÁ½ÉÐQ½ÍÍ¥Í½Ù•Éå	•¹¡µ…É­AÉ½Ù¥‘•È°Q½ÍÍ¥Í½Ù•Éå5…É­•Ñ…Ñ…AÉ½Ù¥‘•È(€€€€€€€€€€€µ…É­•Ñ}‘…Ñ„€ôQ½ÍÍ¥Í½Ù•Éå5…É­•Ñ…Ñ…AÉ½Ù¥‘•È (€€€€€€€€€€€€€€€Q½ÍÍ±¥•¹Ð¡É•‘•¹Ñ¥…±Ì¹•Ð ‰Ñ½ÍÍ}…ÁÁ}­•äˆ°€ˆˆ¤°É•‘•¹Ñ¥…±Ì¹•Ð ‰Ñ½ÍÍ}…ÁÁ}Í•É•Ðˆ°€ˆˆ¤¤¤(€€€€€€€€€€€‰•¹¡µ…É­}ÁÉ½Ù¥‘•È€ôQ½ÍÍ¥Í½Ù•Éå	•¹¡µ…É­AÉ½Ù¥‘•È¡µ…É­•Ñ}‘…Ñ„¤(€€€€€€€™Õ¹‘…µ•¹Ñ…°€ô9½¹”(€€€€€€€…Á¥Ñ…°€ô9½¹”(€€€€€€€¥˜¹½ÐÍ•±˜¹ÕÍ•É}…•¹Ðè(€€€€€€€€€€€É•…Í½¹Ì¹…ÁÁ•¹ ‰M}UMI}9Q}IEU%Iˆ¤(€€€€€€€•±¥˜É•½É‘Ìè(€€€€€€€€€€€™É½´€¹ÁÉ½Ù¥‘•ÉÍ}±¥Ù”¥µÁ½ÉÐM¥Í½Ù•Éå…Á¥Ñ…±AÉ•™±¥¡ÑAÉ½Ù¥‘•È°M¥Í½Ù•ÉåÕ¹‘…µ•¹Ñ…±AÉ½Ù¥‘•È(€€€€€€€€€€€…¡•}‘¥È€ôÍ•±˜¹½¹™¥œ¹•Ð ‰‘¥Í½Ù•Éäˆ°íô¤¹•Ð ‰‰½½ÑÍÑÉ…Àˆ°íô¤¹•Ð ‰™Õ¹‘…µ•¹Ñ…±}…¡•}‘¥Èˆ°€ˆˆ¤(€€€€€€€€€€€™Õ¹‘…µ•¹Ñ…°€ôM¥Í½Ù•ÉåÕ¹‘…µ•¹Ñ…±AÉ½Ù¥‘•È¡Í•±˜¹ÕÍ•É}…•¹Ð°…¡•}‘¥È¤(€€€€€€€€€€€…Á¥Ñ…°€ôM¥Í½Ù•Éå…Á¥Ñ…±AÉ•™±¥¡ÑAÉ½Ù¥‘•È¡Í•±˜¹ÕÍ•É}…•¹Ð°Í•±˜¹Í•Ñ½É}…¡•}‘¥È¤((€€€€€€€¥˜‘…Ñ…‰…Í”¥Ì9½¹”è(€€€€€€€€€€€™É½´€¸¹‘…Ñ…‰…Í”¥µÁ½ÉÐ…Ñ…‰…Í”(€€€€€€€€€€€‘…Ñ…‰…Í”€ô…Ñ…‰…Í”¡Í•±˜¹½¹™¥l‰‘…Ñ…‰…Í•}Á…Ñ ‰t¤(€€€€€€€É•ÍÕ±Ð€ô‰½½ÑÍÑÉ…Á}¡•…±Ñ  (€€€€€€€€€€€‘…Ñ…‰…Í”°Í•ÕÉ¥Ñå}µ…ÍÑ•È°µ…É­•Ñ}‘…Ñ„°‰•¹¡µ…É­}ÁÉ½Ù¥‘•È°(€€€€€€€€€€€µ¥¹}…•ÁÑ•õ¥¹Ð¡Í•±˜¹½¹™¥œ¹•Ð ‰‘¥Í½Ù•Éäˆ°íô¤¹•Ð ‰‰½½ÑÍÑÉ…Àˆ°íô¤¹•Ð ‰µ¥¹}…•ÁÑ•ˆ°€Ä¤¤°(€€€€€€€€€€€µ¥¹}¥‘•¹Ñ¥Ñå}½Ù•É…•}ÁÐõ™±½…Ð¡Í•±˜¹½¹™¥œ¹•Ð ‰‘¥Í½Ù•Éäˆ°íô¤¹•Ð ‰‰½½ÑÍÑÉ…Àˆ°íô¤¹•Ð (€€€€€€€€€€€€€€€€‰µ¥¹}¥‘•¹Ñ¥Ñå}½Ù•É…•}ÁÐˆ°€äÔ¤¤°(€€€€€€€€€€€µ¥¹}Í•Ñ½É}½Ù•É…•}ÁÐõ™±½…Ð¡Í•±˜¹½¹™¥œ¹•Ð ‰‘¥Í½Ù•Éäˆ°íô¤¹•Ð ‰‰½½ÑÍÑÉ…Àˆ°íô¤¹•Ð (€€€€€€€€€€€€€€€€‰µ¥¹}Í•Ñ½É}½Ù•É…•}ÁÐˆ°€äÀ¤¤°(€€€€€€€€€€€™Õ¹‘…µ•¹Ñ…±}ÁÉ½Ù¥‘•Èõ™Õ¹‘…µ•¹Ñ…°°…Á¥Ñ…±}ÁÉ•™±¥¡Ñ}ÁÉ½Ù¥‘•Èõ…Á¥Ñ…°°(€€€€€€€€€€€µ…á}…ÑÕ…±}±±µ}…±±Ìõ¥¹Ð¡Í•±˜¹½¹™¥œ¹•Ð ‰‘¥Í½Ù•Éäˆ°íô¤¹•Ð ‰½ÍÐˆ°íô¤¹•Ð (€€€€€€€€€€€€€€€€‰µ…á}…ÑÕ…±}±±µ}…±±Ìˆ°€À¤½È€À¤°¥¹¥Ñ¥…±¥é•}‘…Ñ…‰…Í”õ…±Í”°(€€€€€€€€¤(€€€€€€€É•ÍÕ±Ñl‰Í¹…ÁÍ¡½Ð‰t€ôì(€€€€€€€€€€€€‰•á¥ÍÑÌˆèÁ…å±½…¥Ì¹½Ð9½¹”°(€€€€€€€€€€€€‰Á…Ñ ˆèÍÑÈ¡Í•±˜¹Í¹…ÁÍ¡½Ñ}Á…Ñ ¤°(€€€€€€€€€€€€‰•¹•É…Ñ•‘}…ÐˆèÁ…å±½…¹•Ð ‰•¹•É…Ñ•‘}…Ðˆ°€ˆˆ¤¥˜Á…å±½…•±Í”€ˆˆ°(€€€€€€€€€€€€‰Í½ÕÉ•}…Í}½˜ˆèÁ…å±½…¹•Ð ‰Í½ÕÉ•}…Í}½˜ˆ°€ˆˆ¤¥˜Á…å±½…•±Í”€ˆˆ°(€€€€€€€€€€€€‰µ•ÑÉ¥ÌˆèÁ…å±½…¹•Ð ‰µ•ÑÉ¥Ìˆ°íô¤¥˜Á…å±½…•±Í”íô°(€€€€€€€ô(€€€€€€€É•ÍÕ±Ñl‰É•‘•¹Ñ¥…±Ì‰t€ôì(€€€€€€€€€€€€‰Í•}ÕÍ•É}…•¹Ðˆè€‰Idˆ¥˜Í•±˜¹ÕÍ•É}…•¹Ð•±Í”€‰	1=-ˆ°(€€€€€€€€€€€€‰Ñ½ÍÌˆè€‰Idˆ¥˜É•‘•¹Ñ¥…±Ì¹•Ð ‰Ñ½ÍÍ}…ÁÁ}­•äˆ¤…¹É•‘•¹Ñ¥…±Ì¹•Ð ‰Ñ½ÍÍ}…ÁÁ}Í•É•Ðˆ¤•±Í”€‰	1=-ˆ°(€€€€€€€ô(€€€€€€€É•ÍÕ±Ñl‰É•…Í½¹}½‘•Ì‰t€ôÍ½ÉÑ•¡Í•Ð¡É•ÍÕ±Ð¹•Ð ‰É•…Í½¹}½‘•Ìˆ°mt¤€¬É•…Í½¹Ì¤¤(€€€€€€€¥˜É•ÍÕ±Ð¹•Ð ‰ÍÑ…ÑÕÌˆ¤€ôô€‰A}!9=}Idˆ…¹É•…Í½¹Ìè(€€€€€€€€€€€É•ÍÕ±Ñl‰ÍÑ…ÑÕÌ‰t€ô€‰	==QMQIA}IEU%Iˆ(€€€€€€€É•ÍÕ±Ñl‰½µµ…¹‰t€ô€‰‘¥Í½Ù•Éäµ¡•…±Ñ ˆ(€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð