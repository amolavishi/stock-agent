­r‡^Ñf¥–Ø¦{OlyÊ'vÃ®¶›­from __future__ import annotations

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
    elif re.search(r"\bCOMMON\s+(?:STOCK|SHARES?)\b|\bORDINARY\s+SHARES?\b", text):
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
                    sample = json.loads(handle.read(self.MAÛM7êÚ$z{-®éÜj×vU÷7C Ð¢&V6öç2æVæB‚%4T5U$•E•ôÔ5DU%ô4õdU$tUô”å5Tdd”4”TåB"Ð¢–bÖWG&–72ævWB‚'6V7F÷%ö6÷fW&vU÷7B"Âã’Â6VÆbæÖ–å÷6V7F÷%ö6÷fW&vU÷7C Ð¢&V6öç2æVæB‚%4T5U$•E•ôÔ5DU%õ4T5Dõ%ô4õdU$tUô”å5Tdd”4”TåB"Ð¢&WGW&â&V6öç0Ð Ð¢FVb÷6÷W&6UöÖWFFF‡6VÆbÂ5ööc¢7G"’ÓâF–7E·7G"Âç•Ó ¢FVb6÷W&6Uö5ööb‡&÷f–FW"ÂFVfVÇC¢7G"Ò%Tä´äõtâ"’Óâ7G# ¢fÇVRÒvWFGG"‡&÷f–FW"Â'6÷W&6Uö5ööb"Â""¢–bfÇVS ¢&WGW&â7G"‡fÇVR¢6÷W&6W2ÒvWFGG"‡&÷f–FW"Â%4õU$4U2"ÂæöæR¢–b6÷W&6W3 ¢&WGW&â7G"†vWFGG"‡&÷f–FW"Â'6÷W&6Uö5ööb"ÂFVfVÇB’÷"FVfVÇB¢&WGW&âFVfVÇ@ ¢FVbfWF6†VEöB‡&÷f–FW"’Óâ7G# ¢&WGW&â7G"†vWFGG"‡&÷f–FW"Â&fWF6†VEöB"Â""’÷""" ¢&WGW&â°¢&&6VÆ–æR#¢°¢'6÷W&6R#¢%4T5ôD•$T5Dõ%’"À¢'6÷W&6U÷W&Â#¢vWFGG"‡6VÆbæÆ—7F–æu÷&÷f–FW"Â%U$Â"Â""’À¢'&WVW7FVEö5ööb#¢5ööbÀ¢'6÷W&6Uö5ööb#¢6÷W&6Uö5ööb‡6VÆbæÆ—7F–æu÷&÷f–FW"’À¢&fWF6†VEöB#¢fWF6†VEöB‡6VÆbæÆ—7F–æu÷&÷f–FW"’À¢ÒÀ¢'6V7W&—G•÷G—R#¢°¢'6÷W&6R#¢$ä4DõE$DU%õ5”Ô$ôÅôD•$T5Dõ%’"À¢'6÷W&6U÷W&Ç2#¢Æ—7B†vWFGG"‡6VÆbç6V7W&—G•÷G—U÷&÷f–FW"Â%4õU$4U2"Â·Ò’çfÇVW2‚’’À¢'&WVW7FVEö5ööb#¢5ööbÀ¢'6÷W&6Uö5ööb#¢6÷W&6Uö5ööb‡6VÆbç6V7W&—G•÷G—U÷&÷f–FW"’À¢&fWF6†VEöB#¢fWF6†VEöB‡6VÆbç6V7W&—G•÷G—U÷&÷f–FW"’À¢ÒÀ¢'W&–öF–5ö6÷fW%÷vR#¢°¢'6÷W&6R#¢%4T5õU$”ôD”5ô4õdU%õtUô”äÄ”äUõ„%$Â"À¢'6÷W&6U÷W&Â#¢&‡GG3¢ò÷wwrç6V2æv÷bô&6†—fW2öVFv"öFFò"À¢'&WVW7FVEö5ööb#¢5ööbÀ¢'6÷W&6Uö5ööb#¢6÷W&6Uö5ööb‡6VÆbæ6÷fW%ö–FVçF—G•÷&÷f–FW"’À¢&fWF6†VEöB#¢fWF6†VEöB‡6VÆbæ6÷fW%ö–FVçF—G•÷&÷f–FW"’À¢&6ÆÇ2#¢–çB†vWFGG"‡6VÆbæ6÷fW%ö–FVçF—G•÷&÷f–FW"Â&6ÆÇ2"Â’÷"’À¢&66†Uö†—G2#¢–çB†vWFGG"‡6VÆbæ6÷fW%ö–FVçF—G•÷&÷f–FW"Â&66†Uö†—G2"Â’÷"’À¢'&W6öÇfVB#¢–çB†vWFGG"‡6VÆbæ6÷fW%ö–FVçF—G•÷&÷f–FW"Â'&W6öÇfVB"Â’÷"’À¢&6öæfÆ–7FVB#¢–çB†vWFGG"‡6VÆbæ6÷fW%ö–FVçF—G•÷&÷f–FW"Â&6öæfÆ–7FVB"Â’÷"’À¢ÒÀ¢'6V7F÷"#¢°¢'6÷W&6R#¢%4T5õ5T$Ô•54”ôå2"À¢'6÷W&6U÷W&Â#¢vWFGG"‡6VÆbç6V7F÷%÷&÷f–FW"Â%U$Â"Â""’–b6VÆbç6V7F÷%÷&÷f–FW"VÇ6R""À¢'&WVW7FVEö5ööb#¢5ööbÀ¢'6÷W&6Uö5ööb#¢6÷W&6Uö5ööb‡6VÆbç6V7F÷%÷&÷f–FW"’À¢&fWF6†VEöB#¢fWF6†VEöB‡6VÆbç6V7F÷%÷&÷f–FW"’À¢&'VÆµöF÷væÆöG2#¢–çB†vWFGG"‡6VÆbç6V7F÷%÷&÷f–FW"Â&'VÆµöF÷væÆöG2"Â’÷"’À¢&–æF—f–GVÅöfÆÆ&6µö6ÆÇ2#¢–çB†vWFGG"‡6VÆbç6V7F÷%÷&÷f–FW"Â&–æF—f–GVÅö6ÆÇ2"Â’÷"’À¢&fÆÆ&6µö66†Uö†—G2#¢–çB†vWFGG"‡6VÆbç6V7F÷%÷&÷f–FW"Â&fÆÆ&6µö66†Uö†—G2"Â’÷"’À¢&'VÆµöW'&÷%÷&V6öåö6öFR#¢7G"†vWFGG"‡6VÆbç6V7F÷%÷&÷f–FW"Â&'VÆµöW'&÷%÷&V6öåö6öFR"Â""’÷"""’À¢ÒÀ¢Ð Ð Ð¦FVb6æ6†÷E÷&V6÷&G2‡–ÆöC¢F–7E·7G"Âç•Ò’ÓâÆ—7Eµ6V7W&—G”Ö7FW%&V6÷&EÓ Ð¢fÆ–FFU÷6æ6†÷B‡–ÆöBÐ¢&WGW&âµ÷&V6÷&Eög&öÕ÷&÷r‡&÷r’f÷"&÷r–â–ÆöBævWB‚'&V6÷&G2"ÂµÒ•ÐÐ Ð Ð¦FVb&VE÷6æ6†÷B‡Fƒ¢7G"ÂF‚’ÓâF–7E·7G"Âç•ÒÂæöæS Ð¢F&vWBÒF‚‡F‚Ð¢–bæ÷BF&vWBæ—5öf–ÆR‚“ Ð¢&WGW&âæöæPÐ¢G'“ Ð¢–ÆöBÒ§6öâæÆöG2‡F&vWBç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"’Ð¢W†6WB„õ4W'&÷"ÂVæ–6öFTFV6öFTW'&÷"Â§6öâä¥4ôäFV6öFTW'&÷"’2W†3 Ð¢&—6R6V7W&—G”Ö7FW$&ö÷G7G&W'&÷"‚%4T5U$•E•ôÔ5DU%õ4ä4„õEõTå$TD$ÄR"Â7G"†W†2’’g&öÒW†0Ð¢fÆ–FFU÷6æ6†÷B‡–ÆöBÐ¢–ÆöE²'6æ6†÷E÷F‚%ÒÒ7G"‡F&vWBÐ¢–ÆöE²'6æ6†÷Eöf–ÆUö×F–ÖR%ÒÒFFWF–ÖRæg&ö×F–ÖW7F×‡F&vWBç7FB‚’ç7Eö×F–ÖRÂF–ÖW¦öæRçWF2’æ—6öf÷&ÖB‚Ð¢&WGW&â–Æö@Ð Ð Ð¦6Æ726V7W&—G”Ö7FW$&ö÷G7G&6W'f–6S Ð¢""$6öæf–wW&F–öâÖ&÷VæB6W'f–6RW6VB'’&ö÷G7G&÷&Vg&W6‚ö†VÇF‚4Ä’6öÖÖæG2â"" Ð Ð¢FVbõö–æ—Eõò‡6VÆbÂ6öæf–s¢F–7E·7G"Âç•Ò“ Ð¢6VÆbæ6öæf–rÒ6öæf–pÐ¢F—66÷fW'’Ò6öæf–rævWB‚&F—66÷fW'’"Â·ÒÐ¢&ö÷G7G&ÒF—66÷fW'’ævWB‚&&ö÷G7G&"Â·ÒÐ¢7&VFVçF–Ç2Ò6öæf–rævWB‚&7&VFVçF–Ç2"Â·ÒÐ¢6VÆbçW6W%övVçBÒ7G"†7&VFVçF–Ç2ævWB‚'6V5÷W6W%övVçB"Â""’Ð¢6VÆbç6æ6†÷E÷F‚ÒF‚†&ö÷G7G&ævWB‚'6V7W&—G•öÖ7FW%öVç&–6†ÖVçE÷F‚"Â""’Ð¢6VÆbç&uö66†UöF—"ÒF‚†&ö÷G7G&ævWB‚'&uö66†UöF—""Â&FFö66†RöF—66÷fW'’÷6V7W&—G•öÖ7FW"÷&r"’Ð¢6VÆbææ÷&ÖÆ—¦VEö66†UöF—"ÒF‚†&ö÷G7G&ævWB‚&æ÷&ÖÆ—¦VEö66†UöF—""Â&FFö66†RöF—66÷fW'’÷6V7W&—G•öÖ7FW"öæ÷&ÖÆ—¦VB"’Ð¢6VÆbç6V7F÷%ö66†UöF—"ÒF‚†&ö÷G7G&ævWB‚'6V7F÷%ö66†UöF—""Â7G"‡6VÆbç&uö66†UöF—"ò'6V5÷7V&Ö—76–öç2"’’Ð¢6VÆbåö'V–ÆFW#¢6V7W&—G”Ö7FW$&ö÷G7G&'V–ÆFW"ÂæöæRÒæöæPÐ Ð¢FVb'V–ÆFW"‡6VÆb’Óâ6V7W&—G”Ö7FW$&ö÷G7G&'V–ÆFW# ¢–b6VÆbåö'V–ÆFW"—2æ÷BæöæS Ð¢&WGW&â6VÆbåö'V–ÆFW Ð¢–bæ÷B6VÆbçW6W%övVçC Ð¢&—6R6V7W&—G”Ö7FW$&ö÷G7G&W'&÷"‚%4T5õU4U%ôtTåEõ$UT•$TB"Ð¢g&öÒç&÷f–FW'5öÆ—fR–×÷'B4T46ö×ç•F–6¶W%6V7W&—G”Ö7FW%&÷f–FW ¢Æ—7F–ærÒ4T46ö×ç•F–6¶W%6V7W&—G”Ö7FW%&÷f–FW"€¢6VÆbçW6W%övVçBÂ6VÆbç&uö66†UöF—"ò&6ö×ç•÷F–6¶W'5öW†6†ævRæ§6öâ"¢6V7W&—G•÷G—W2Òæ6FG&FW%6V7W&—G•G—U&÷f–FW"‡6VÆbç&uö66†UöF—"ò&æ6F÷G&FW""¢fÆÆ&6µ÷6V7F÷"Ò4T57V&Ö—76–öç4ÖWFFF&÷f–FW"€¢6VÆbçW6W%övVçBÂ6VÆbç6V7F÷%ö66†UöF—"À¢Ö…÷&WVW7G3Ö–çB‡6VÆbæ6öæf–rævWB‚&F—66÷fW'’"Â·Ò’ævWB‚&&ö÷G7G&"Â·Ò’ævWB€¢&Ö…ö—77VW%öÖWFFF÷&WVW7G2"Âó’’À¢Ö…÷'3ÖfÆöB‡6VÆbæ6öæf–rævWB‚'6V5öÖ…÷'2"ÂB’’¢6V7F÷"Ò4T57V&Ö—76–öç46ö×÷6—FTÖWFFF&÷f–FW"€¢4T57V&Ö—76–öç4'VÆ´ÖWFFF&÷f–FW"‡6VÆbçW6W%övVçBÂ6VÆbç&uö66†UöF—"’ÂfÆÆ&6µ÷6V7F÷"¢&ö÷G7G&Ò6VÆbæ6öæf–rævWB‚&F—66÷fW'’"Â·Ò’ævWB‚&&ö÷G7G&"Â·Ò¢'VÆµ÷&÷f–FW"Ò6V7F÷"æ'VÆ°¢6÷fW%÷&÷f–FW"Ò4T5W&–öF–46÷fW$–FVçF—G•&÷f–FW"€¢6VÆbçW6W%övVçBÂ'VÆµ÷&÷f–FW"À¢6VÆbç&uö66†UöF—"ò'6V5ö6÷fW""À¢Ö…÷&WVW7G3Ö–çB†&ö÷G7G&ævWB‚&Ö…ö6÷fW%öf–Æ–æu÷&WVW7G2"Â%óS’’À¢Ö…÷'3ÖfÆöB‡6VÆbæ6öæf–rævWB‚'6V5öÖ…÷'2"ÂB’’À¢¢6VÆbåö'V–ÆFW"Ò6V7W&—G”Ö7FW$&ö÷G7G&'V–ÆFW"€¢Æ—7F–ærÂ6V7W&—G•÷G—W2Â6V7F÷"Â6VÆbç6æ6†÷E÷F‚Â6VÆbç&uö66†UöF—"ÀÐ¢6VÆbææ÷&ÖÆ—¦VEö66†UöF—"ÀÐ¢7W÷'FVEöW†6†ævW3ÕVæ—fW'6T–çFVw&—G”Væv–æRäDTdTÅEôU„4„ätU2ÀÐ¢Ö–åö66WFVCÖ–çB†&ö÷G7G&ævWB‚&Ö–åö66WFVB"Â’’À¢Ö–åö–FVçF—G•ö6÷fW&vU÷7CÖfÆöB†&ö÷G7G&ævWB‚&Ö–åö–FVçF—G•ö6÷fW&vU÷7B"Â“R’’À¢Ö–å÷6V7F÷%ö6÷fW&vU÷7CÖfÆöB†&ö÷G7G&ævWB‚&Ö–å÷6V7F÷%ö6÷fW&vU÷7B"Â“’’À¢6÷fW%ö–FVçF—G•÷&÷f–FW#Ö6÷fW%÷&÷f–FW"À¢¢&WGW&â6VÆbåö'V–ÆFW Ð Ð¢FVb&ö÷G7G&‡6VÆbÂ&Vg&W6ƒ¢&ööÂÒfÇ6R’ÓâF–7E·7G"Âç•Ó Ð¢&WGW&â6VÆbæ'V–ÆFW"‚’æ'V–ÆEöæE÷w&—FR‡&Vg&W6ƒ×&Vg&W6‚Ð Ð¢FVb†VÇF‚‡6VÆbÂFF&6SÔæöæR’ÓâF–7E·7G"Âç•Ó ¢""%&VBÖöæÇ’†VÇFƒ¢—BæWfW"7&VFW2÷"&WÆ6W26æ6†÷Bâ"" Ð¢g&öÒæ†VÇF‚–×÷'B&ö÷G7G&ö†VÇF€Ð Ð¢–ÆöBÒ&VE÷6æ6†÷B‡6VÆbç6æ6†÷E÷F‚’–b6VÆbç6æ6†÷E÷F‚æ—5öf–ÆR‚’VÇ6RæöæPÐ¢&V6öç3¢Æ—7E·7G%ÒÒµÐÐ¢–b–ÆöB—2æöæS Ð¢&V6öç2æVæB‚%4T5U$•E•ôÔ5DU%õ4ä4„õEôÔ•54”är"Ð¢&V6÷&G2ÒµÐÐ¢VÇ6S Ð¢&V6÷&G2Ò6æ6†÷E÷&V6÷&G2‡–ÆöBÐ¢g&öÒçVæ—fW'6R–×÷'B–äÖVÖ÷'•6V7W&—G”Ö7FW%&÷f–FW Ð¢6V7W&—G•öÖ7FW"Ò–äÖVÖ÷'•6V7W&—G”Ö7FW%&÷f–FW"‡&V6÷&G2Ð Ð¢7&VFVçF–Ç2Ò6VÆbæ6öæf–rævWB‚&7&VFVçF–Ç2"Â·ÒÐ¢Ö&¶WEöFFÒæöæPÐ¢&Væ6†Ö&µ÷&÷f–FW"ÒæöæPÐ¢–bæ÷B7&VFVçF–Ç2ævWB‚'F÷75öö¶W’"’÷"æ÷B7&VFVçF–Ç2ævWB‚'F÷75ö÷6V7&WB"“ Ð¢&V6öç2æVæB‚%Dõ55ô5$TDTåD”Å5õ$UT•$TB"Ð¢VÆ–b6VÆbæ6öæf–rævWB‚&Ö&¶WEöFF÷&÷f–FW""Â6VÆbæ6öæf–rævWB‚'&÷f–FW""Â'F÷72"’’ÓÒ'F÷72# Ð¢g&öÒâçF÷72–×÷'BF÷746Æ–Vç@Ð¢g&öÒç&÷f–FW'5öÆ—fR–×÷'BF÷74F—66÷fW'”&Væ6†Ö&µ&÷f–FW"ÂF÷74F—66÷fW'”Ö&¶WDFF&÷f–FW Ð¢Ö&¶WEöFFÒF÷74F—66÷fW'”Ö&¶WDFF&÷f–FW"€Ð¢F÷746Æ–VçB†7&VFVçF–Ç2ævWB‚'F÷75öö¶W’"Â""’Â7&VFVçF–Ç2ævWB‚'F÷75ö÷6V7&WB"Â""’’Ð¢&Væ6†Ö&µ÷&÷f–FW"ÒF÷74F—66÷fW'”&Væ6†Ö&µ&÷f–FW"†Ö&¶WEöFFÐ¢gVæFÖVçFÂÒæöæP¢6—FÂÒæöæP¢–bæ÷B6VÆbçW6W%övVçC ¢&V6öç2æVæB‚%4T5õU4U%ôtTåEõ$UT•$TB"¢VÇ6S ¢g&öÒç&÷f–FW'5öÆ—fR–×÷'B4T4F—66÷fW'”6—FÅ&VfÆ–v‡E&÷f–FW"Â4T4F—66÷fW'”gVæFÖVçFÅ&÷f–FW ¢66†UöF—"Ò6VÆbæ6öæf–rævWB‚&F—66÷fW'’"Â·Ò’ævWB‚&&ö÷G7G&"Â·Ò’ævWB‚&gVæFÖVçFÅö66†UöF—""Â""¢gVæFÖVçFÂÒ4T4F—66÷fW'”gVæFÖVçFÅ&÷f–FW"‡6VÆbçW6W%övVçBÂ66†UöF—"¢6—FÂÒ4T4F—66÷fW'”6—FÅ&VfÆ–v‡E&÷f–FW"‡6VÆbçW6W%övVçBÂ6VÆbç6V7F÷%ö66†UöF—" Ð¢–bFF&6R—2æöæS Ð¢g&öÒâæFF&6R–×÷'BFF&6PÐ¢FF&6RÒFF&6R‡6VÆbæ6öæf–u²&FF&6U÷F‚%ÒÐ¢&W7VÇBÒ&ö÷G7G&ö†VÇF‚€Ð¢FF&6RÂ6V7W&—G•öÖ7FW"ÂÖ&¶WEöFFÂ&Væ6†Ö&µ÷&÷f–FW"ÀÐ¢Ö–åö66WFVCÖ–çB‡6VÆbæ6öæf–rævWB‚&F—66÷fW'’"Â·Ò’ævWB‚&&ö÷G7G&"Â·Ò’ævWB‚&Ö–åö66WFVB"Â’’ÀÐ¢Ö–åö–FVçF—G•ö6÷fW&vU÷7CÖfÆöB‡6VÆbæ6öæf–rævWB‚&F—66÷fW'’"Â·Ò’ævWB‚&&ö÷G7G&"Â·Ò’ævWB€Ð¢&Ö–åö–FVçF—G•ö6÷fW&vU÷7B"Â“R’’ÀÐ¢Ö–å÷6V7F÷%ö6÷fW&vU÷7CÖfÆöB‡6VÆbæ6öæf–rævWB‚&F—66÷fW'’"Â·Ò’ævWB‚&&ö÷G7G&"Â·Ò’ævWB€Ð¢&Ö–å÷6V7F÷%ö6÷fW&vU÷7B"Â“’’ÀÐ¢gVæFÖVçFÅ÷&÷f–FW#ÖgVæFÖVçFÂÂ6—FÅ÷&VfÆ–v‡E÷&÷f–FW#Ö6—FÂÀÐ¢Ö…ö7GVÅöÆÆÕö6ÆÇ3Ö–çB‡6VÆbæ6öæf–rævWB‚&F—66÷fW'’"Â·Ò’ævWB‚&6÷7B"Â·Ò’ævWB€Ð¢&Ö…ö7GVÅöÆÆÕö6ÆÇ2"Â’÷"’Â–æ—F–Æ—¦UöFF&6SÔfÇ6RÀÐ¢Ð¢&W7VÇE²'6æ6†÷B%ÒÒ°¢&W†—7G2#¢–ÆöB—2æ÷BæöæRÀ¢'F‚#¢7G"‡6VÆbç6æ6†÷E÷F‚’À¢&vVæW&FVEöB#¢–ÆöBævWB‚&vVæW&FVEöB"Â""’–b–ÆöBVÇ6R""ÀÐ¢'6÷W&6Uö5ööb#¢–ÆöBævWB‚'6÷W&6Uö5ööb"Â""’–b–ÆöBVÇ6R""ÀÐ¢&ÖWG&–72#¢–ÆöBævWB‚&ÖWG&–72"Â·Ò’–b–ÆöBVÇ6R·ÒÀ¢Ð¢6æF–FFU÷F‚Ò6VÆbç6æ6†÷E÷F‚çv—F…öæÖR‚'6V7W&—G•öÖ7FW%ö6æF–FFRæ§6öâ"¢F–væ÷7F–75÷F‚Ò6VÆbç6æ6†÷E÷F‚çv—F…öæÖR‚'6V7W&—G•öÖ7FW%ö'V–ÆEöF–væ÷7F–72æ§6öâ"¢6æF–FFRÒæöæP¢F–væ÷7F–72ÒæöæP¢G'“ ¢6æF–FFRÒ&VE÷6æ6†÷B†6æF–FFU÷F‚’–b6æF–FFU÷F‚æ—5öf–ÆR‚’VÇ6RæöæP¢W†6WB6V7W&—G”Ö7FW$&ö÷G7G&W'&÷# ¢6æF–FFRÒæöæP¢G'“ ¢F–væ÷7F–72Ò§6öâæÆöG2†F–væ÷7F–75÷F‚ç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"’’À¢–bF–væ÷7F–75÷F‚æ—5öf–ÆR‚’VÇ6RæöæP¢W†6WB„õ4W'&÷"ÂVæ–6öFTFV6öFTW'&÷"Â§6öâä¥4ôäFV6öFTW'&÷"“ ¢F–væ÷7F–72ÒæöæP¢&W7VÇE²'V&Æ–6F–öâ%ÒÒ°¢&7F—fU÷7FGW2#¢$5D•dR"–b–ÆöB—2æ÷BæöæRVÇ6R$Ô•54”är"À¢&6æF–FFU÷7FGW2#¢6æF–FFRævWB‚'7FGW2"Â""’–b6æF–FFRVÇ6R""À¢&6æF–FFUöÖWG&–72#¢6æF–FFRævWB‚&ÖWG&–72"Â·Ò’–b6æF–FFRVÇ6R·ÒÀ¢&F–væ÷7F–72#¢F–væ÷7F–72÷"·ÒÀ¢&7F—fUöÆ¶u÷&W6W'fVB#¢–ÆöB—2æ÷BæöæRÀ¢Ð¢&W7VÇE²&7&VFVçF–Ç2%ÒÒ°¢'6V5÷W6W%övVçB#¢%$TE’"–b6VÆbçW6W%övVçBVÇ6R$$Äô4´TB"ÀÐ¢'F÷72#¢%$TE’"–b7&VFVçF–Ç2ævWB‚'F÷75öö¶W’"’æB7&VFVçF–Ç2ævWB‚'F÷75ö÷6V7&WB"’VÇ6R$$Äô4´TB"ÀÐ¢Ð¢&W7VÇE²'&÷f–FW%÷&VF–æW72%ÒÒ°¢'6V7W&—G•öÖ7FW"#¢°¢&6öæf–wW&VB#¢G'VRÀ¢&6öç7G'V7FVB#¢G'VRÀ¢'6×ÆUöW†V7WFVB#¢&ööÂ‡&V6÷&G2’À¢'6×ÆU÷&VG’#¢&ööÂ‡&W7VÇBævWB‚'6V7W&—G•öÖ7FW""ÂfÇ6R’’À¢&&Æö6¶VEö'’#¢€¢%4ä4„õEôÔ•54”är ¢–bæ÷B–ÆöBæBæ÷B6æF–FFP¢VÇ6R7G"‚†6æF–FFR÷"·Ò’ævWB‚'7FGW2"’÷"%4ä4„õEôÔ•54”är"¢–bæ÷B–ÆöBVÇ6R" ¢’À¢ÒÀ¢'F÷72#¢°¢&6öæf–wW&VB#¢&ööÂ†7&VFVçF–Ç2ævWB‚'F÷75öö¶W’"’æB7&VFVçF–Ç2ævWB‚'F÷75ö÷6V7&WB"’’À¢&6öç7G'V7FVB#¢Ö&¶WEöFF—2æ÷BæöæRÀ¢'G&ç7÷'B#¢%$TE’"–bÖ&¶WEöFF—2æ÷BæöæRVÇ6R$$Äô4´TB"À¢'6×ÆUöW†V7WFVB#¢fÇ6RÀ¢'6×ÆU÷&VG’#¢fÇ6RÀ¢&&Æö6¶VEö'’#¢%4T5U$•E•ôÔ5DU%ôäõEõ$TE’"–bæ÷B&V6÷&G2VÇ6R""À¢ÒÀ¢&gVæFÖVçFÂ#¢°¢&6öæf–wW&VB#¢&ööÂ‡6VÆbçW6W%övVçB’À¢&6öç7G'V7FVB#¢gVæFÖVçFÂ—2æ÷BæöæRÀ¢'6×ÆUöW†V7WFVB#¢fÇ6RÀ¢'6×ÆU÷&VG’#¢fÇ6RÀ¢&&Æö6¶VEö'’#¢%4T5U$•E•ôÔ5DU%ôäõEõ$TE’"–bæ÷B&V6÷&G2VÇ6R""À¢ÒÀ¢&6—FÂ#¢°¢&6öæf–wW&VB#¢&ööÂ‡6VÆbçW6W%övVçB’À¢&6öç7G'V7FVB#¢6—FÂ—2æ÷BæöæRÀ¢'6×ÆUöW†V7WFVB#¢fÇ6RÀ¢'6×ÆU÷&VG’#¢fÇ6RÀ¢&&Æö6¶VEö'’#¢%4T5U$•E•ôÔ5DU%ôäõEõ$TE’"–bæ÷B&V6÷&G2VÇ6R""À¢ÒÀ¢Ð¢–bgVæFÖVçFÂ—2æ÷BæöæS ¢&W7VÇE²'&V6öåö6öFW2%ÒÒ¶6öFRf÷"6öFR–â&W7VÇBævWB‚'&V6öåö6öFW2"ÂµÒ¢–b6öFRÒ$eTäDÔTåDÅõ$õd”DU%ôÔ•54”är%Ð¢–bæ÷B&V6÷&G3 ¢&W7VÇE²'&V6öåö6öFW2%ÒæVæB‚$eTäDÔTåDÅõ4ÕÄUô$Äô4´TEõ4T5U$•E•ôÔ5DU""¢–b6—FÂ—2æ÷BæöæS ¢&W7VÇE²'&V6öåö6öFW2%ÒÒ¶6öFRf÷"6öFR–â&W7VÇBævWB‚'&V6öåö6öFW2"ÂµÒ¢–b6öFRÒ$4•DÅõ$TdÄ”t…Eõ$õd”DU%ôÔ•54”är%Ð¢–bæ÷B&V6÷&G3 ¢&W7VÇE²'&V6öåö6öFW2%ÒæVæB‚$4•DÅõ4ÕÄUô$Äô4´TEõ4T5U$•E•ôÔ5DU""¢–b&V6÷&G2æBÖ&¶WEöFF—2æ÷BæöæS ¢&W7VÇE²'&÷f–FW%÷&VF–æW72%Õ²'F÷72%Õ²'6×ÆUöW†V7WFVB%ÒÒG'VP¢–bæ÷B&V6÷&G2æBÖ&¶WEöFF—2æ÷BæöæS ¢&W7VÇE²'&V6öåö6öFW2%ÒÒ¶6öFRf÷"6öFR–â&W7VÇBævWB‚'&V6öåö6öFW2"ÂµÒ¢–b6öFRÒ$Ô$´UEôDDõ4ÕÄUõTäd”Ä$ÄR%Ð¢&W7VÇE²'&V6öåö6öFW2%ÒæVæB‚$4äD”DDUõTõDUõ4ÕÄUô$Äô4´TEõ4T5U$•E•ôÔ5DU""¢–bæ÷B&V6÷&G2æBgVæFÖVçFÂ—2æ÷BæöæS ¢&W7VÇE²'&V6öåö6öFW2%ÒÒ¶6öFRf÷"6öFR–â&W7VÇBævWB‚'&V6öåö6öFW2"ÂµÒ¢–b6öFRÒ$eTäDÔTåDÅô$Äô4´TEôÔ$´UEô$ôõE5E$%Ð¢–bæ÷B&V6÷&G2æB6—FÂ—2æ÷BæöæS ¢&W7VÇE²'&V6öåö6öFW2%ÒÒ¶6öFRf÷"6öFR–â&W7VÇBævWB‚'&V6öåö6öFW2"ÂµÒ¢–b6öFRÒ$4•DÅõ$TdÄ”t…Eô$Äô4´TEôÔ$´UEô$ôõE5E$%Ð¢&W7VÇE²'&V6öåö6öFW2%ÒÒ6÷'FVB‡6WB‡&W7VÇBævWB‚'&V6öåö6öFW2"ÂµÒ’²&V6öç2’¢–b&W7VÇBævWB‚'7FGW2"’ÓÒ$DTUô„äDôdeõ$TE’"æB&V6öç3 Ð¢&W7VÇE²'7FGW2%ÒÒ$$ôõE5E$õ$UT•$TB Ð¢&W7VÇE²&6öÖÖæB%ÒÒ&F—66÷fW'’Ö†VÇF‚ Ð¢&WGW&â&W7VÇ@Ð