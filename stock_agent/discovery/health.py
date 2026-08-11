from __future__ import annotations

from datetime import datetime, timezone

from .universe import InMemorySecurityMasterProvider, UniverseIntegrityEngine


def _sample_tickers(records, limit: int = 3) -> list[str]:
    return [record.ticker for record in records[:limit] if getattr(record, "ticker", "")]


def bootstrap_health(database, security_master=None, market_data=None,
                     benchmark_provider=None, min_accepted: int = 1,
                     min_identity_pct: float = 95.0,
                     min_sector_pct: float = 90.0) -> dict:
    """Run a real, small bootstrap smoke instead of checking provider classes.

    A provider with no validated identity/sector coverage is intentionally reported
    as BOOTSTRAP_REQUIRED.  This keeps the universe fail-closed while making the
    missing enrichment visible to operators.
    """
    checks = {
        "schema": False,
        "security_master": False,
        "market_data": False,
        "daily_bar_cache": False,
        "benchmark_data": False,
        "status": "FAILED",
    }
    reason_codes: list[str] = []
    try:
        database.init()
        with database.connect() as connection:
            checks["schema"] = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version>=24").fetchone() is not None
            checks["daily_bar_cache"] = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_bars'").fetchone() is not None
    except Exception as exc:
        checks["reason_codes"] = ["SCHEMA_SMOKE_FAILED"]
        checks["error"] = str(exc)
        return checks

    records = []
    as_of = datetime.now(timezone.utc).isoformat()
    if security_master is not None and hasattr(security_master, "records"):
        try:
            records = list(security_master.records(as_of))
        except Exception:
            reason_codes.append("SECURITY_MASTER_FETCH_FAILED")
    integrity = (UniverseIntegrityEngine().build(InMemorySecurityMasterProvider(records), as_of)
                 if records else None)
    universe_health = integrity.get("health", {}) if integrity else {
        "raw_count": 0, "accepted_count": 0, "identity_coverage_pct": 0.0,
        "exchange_coverage_pct": 0.0, "sector_coverage_pct": 0.0,
        "duplicate_count": 0, "unknown_identity_count": 0,
    }
    checks["security_master"] = (
        universe_health["raw_count"] > 0
        and universe_health["accepted_count"] >= min_accepted
        and universe_health["identity_coverage_pct"] >= min_identity_pct
        and universe_health["sector_coverage_pct"] >= min_sector_pct
    )
    if not records:
        reason_codes.append("UNIVERSE_EMPTY")
    elif universe_health["accepted_count"] == 0:
        reason_codes.append("IDENTITY_ENRICHMENT_MISSING")
    if universe_health["sector_coverage_pct"] < min_sector_pct:
        reason_codes.append("SECTOR_ENRICHMENT_INSUFFICIENT")

    tickers = _sample_tickers(records)
    if market_data is not None and tickers and hasattr(market_data, "batch_quotes"):
        try:
            quotes = market_data.batch_quotes(tickers, as_of)
            checks["market_data"] = bool(quotes)
            if not checks["market_data"]:
                reason_codes.append("QUOTE_SAMPLE_EMPTY")
        except Exception:
            reason_codes.append("QUOTE_SAMPLE_FAILED")
    elif market_data is not None and tickers and hasattr(market_data, "quotes"):
        try:
            quotes = market_data.quotes(tickers, as_of)
            checks["market_data"] = bool(quotes)
        except Exception:
            reason_codes.append("QUOTE_SAMPLE_FAILED")
    else:
        reason_codes.append("MARKET_DATA_SAMPLE_UNAVAILABLE")

    if market_data is not None and tickers and hasattr(market_data, "daily_bars"):
        try:
            checks["daily_bar_cache"] = bool(market_data.daily_bars(tickers[0], as_of))
        except Exception:
            reason_codes.append("BAR_SAMPLE_FAILED")

    benchmark_provider = benchmark_provider or market_data
    if benchmark_provider is not None and hasattr(benchmark_provider, "benchmark_bars"):
        try:
            benchmark = benchmark_provider.benchmark_bars(("SPY", "QQQ", "IWM"), as_of)
            checks["benchmark_data"] = all(benchmark.get(ticker) for ticker in ("SPY", "QQQ", "IWM"))
        except Exception:
            reason_codes.append("BENCHMARK_SAMPLE_FAILED")
    else:
        reason_codes.append("BENCHMARK_PROVIDER_MISSING")

    checks["status"] = "DISCOVERY_READY" if all(
        checks[key] for key in ("schema", "security_master", "market_data", "daily_bar_cache", "benchmark_data")
    ) else "BOOTSTRAP_REQUIRED"
    checks["universe"] = universe_health
    checks["reason_codes"] = sorted(set(reason_codes))
    checks["checked_at"] = datetime.now(timezone.utc).isoformat()
    return checks
