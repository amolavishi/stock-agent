from __future__ import annotations

import hashlib
import json
from typing import Iterable, Protocol

from .schemas import SecurityMasterRecord


class SecurityMasterProvider(Protocol):
    def records(self, as_of: str) -> Iterable[SecurityMasterRecord]: ...


class InMemorySecurityMasterProvider:
    def __init__(self, records: Iterable[SecurityMasterRecord]):
        self._records = tuple(records)
        self.calls = 0

    def records(self, as_of: str) -> tuple[SecurityMasterRecord, ...]:
        self.calls += 1
        return self._records


class EmptySecurityMasterProvider:
    def records(self, as_of: str) -> tuple[SecurityMasterRecord, ...]:
        return ()


def _snapshot_id(records: list[SecurityMasterRecord], as_of: str) -> str:
    payload = json.dumps([record.to_dict() for record in records], sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(f"{as_of}|{payload}".encode("utf-8")).hexdigest()[:16]
    return f"UNIVERSE_{as_of[:10].replace('-', '')}_{digest}"


class UniverseIntegrityEngine:
    # One canonical scope is shared by the live bootstrap builder and the
    # executable-universe filter.  Keep aliases here rather than duplicating
    # an exchange list in a provider.
    DEFAULT_EXCHANGES = {"NYSE", "NASDAQ", "NYSE AMERICAN", "NYSEAMERICAN", "AMEX"}
    SUPPORTED_EXCHANGES = frozenset(DEFAULT_EXCHANGES)

    def __init__(self, allow_adr: bool = False, exchanges: set[str] | None = None):
        self.allow_adr = allow_adr
        self.exchanges = {value.upper() for value in (exchanges or self.DEFAULT_EXCHANGES)}

    def build(self, provider: SecurityMasterProvider, as_of: str) -> dict:
        records = list(provider.records(as_of))
        accepted: list[SecurityMasterRecord] = []
        rejected: dict[str, int] = {}
        seen: set[str] = set()
        duplicate_count = 0
        unknown_identity_count = 0
        for record in records:
            reason = self._reject_reason(record, as_of, seen)
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
                if reason == "DUPLICATE_OR_EMPTY_TICKER":
                    duplicate_count += 1
                if reason.startswith("UNKNOWN_IDENTITY_"):
                    unknown_identity_count += 1
                continue
            seen.add(record.ticker)
            accepted.append(record)
        accepted.sort(key=lambda item: (item.ticker, item.security_id))
        raw_count = len(records)
        identity_known = sum(self._identity_known(record) for record in records)
        exchange_known = sum(bool(record.exchange) for record in records)
        sector_known = sum(record.sector_canonical.upper() != "UNKNOWN" for record in accepted)
        return {"snapshot_id": _snapshot_id(accepted, as_of), "as_of": as_of,
                "records": accepted, "raw_records": records, "raw_count": raw_count,
                "rejected": rejected,
                "coverage_identity": identity_known / raw_count if raw_count else 0.0,
                "health": {
                    "raw_count": raw_count,
                    "accepted_count": len(accepted),
                    "identity_coverage_pct": round(identity_known / raw_count * 100, 4) if raw_count else 0.0,
                    "exchange_coverage_pct": round(exchange_known / raw_count * 100, 4) if raw_count else 0.0,
                    "sector_coverage_pct": round(sector_known / len(accepted) * 100, 4) if accepted else 0.0,
                    "duplicate_count": duplicate_count,
                    "unknown_identity_count": unknown_identity_count,
                }}

    @staticmethod
    def _identity_known(record: SecurityMasterRecord) -> bool:
        return all(getattr(record, name) is not None for name in (
            "is_common_stock", "is_etf", "is_unit", "is_warrant", "is_preferred", "is_adr"))

    def _reject_reason(self, record: SecurityMasterRecord, as_of: str, seen: set[str]) -> str:
        if not record.ticker or record.ticker in seen:
            return "DUPLICATE_OR_EMPTY_TICKER"
        if record.country.upper() != "US":
            return "NON_US_SECURITY"
        if record.active_status.upper() != "ACTIVE":
            return "INACTIVE_LISTING"
        if record.delisting_date and record.delisting_date <= as_of[:10]:
            return "DELISTED_AS_OF"
        if record.exchange and record.exchange.upper() not in self.exchanges:
            return "UNSUPPORTED_EXCHANGE"
        for name in ("is_common_stock", "is_etf", "is_unit", "is_warrant", "is_preferred", "is_adr"):
            if getattr(record, name) is None:
                return f"UNKNOWN_IDENTITY_{name.upper()}"
        if not record.is_common_stock:
            return "NOT_COMMON_STOCK"
        if record.is_etf:
            return "ETF"
        if record.is_unit:
            return "UNIT"
        if record.is_warrant:
            return "WARRANT"
        if record.is_preferred:
            return "PREFERRED"
        if record.is_adr and not self.allow_adr:
            return "ADR_NOT_ALLOWED"
        return ""
