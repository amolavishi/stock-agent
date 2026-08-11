from __future__ import annotations

from datetime import datetime, timezone

from .universe import InMemorySecurityMasterProvider, UniverseIntegrityEngine


def _sample_tickers(records, limit: int = 3) -> list[str]:
    return [record.ticker for record in records[:limit] if getattr(record, "ticker", "")]


def bootstrap_health(database, security_master=None, market_data=None,
                     benchmark_provider=None, min_accepted: int = 1,
                     min_identity_coverage_pct: float = 95.0,
                     min_sector_coverage_pct: float = 90.0,
                     fundamental_provider=None, capital_preflight_provider=None,
                     max_actual_llm_calls: int = 0) -> dict:
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
        "fundamental_data": False,
        "capital_preflight_data": False,
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
        and universe_health["identity_coverage_pct"] >= min_identity_coverage_pct
        and universe_health["sector_coverage_pct"] >= min_sector_coverage_pct
    )
    if not records:
        reason_codes.append("UNIVERSE_EMPTY")
    elif universe_health["accepted_count"] == 0:
        reason_codes.append("IDENTITY_ENRICHMENT_MISSING")
    if universe_health["sector_coverage_pct"] < min_sector_coverage_pct:
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

    market_ready = all(
        checks[key] for key in ("schema", "security_master", "market_data", "daily_bar_cache", "benchmark_data")
    )

    if market_ready and fundamental_provider is not None and tickers and hasattr(fundamental_provider, "fundamentals"):
        try:
            fundamental_sample = fundamental_provider.fundamentals(tickers, as_of)
            checks["fundamental_data"] = bool(fundamental_sample) and any(
                getattr((fields or {}).get("primary_financial_evidence"), "known", False)
                and (fields or {}).get("primary_financial_evidence").value is True
                for fields in fundamental_sample.values())
            if not checks["fundamental_data"]:
                reason_codes.append("FUNDAMENTAL_SAMPLE_EMPTY")
        except Exception:
            reason_codes.append("FUNDAMENTAL_SAMPLE_FAILED")
    elif fundamental_provider is None:
        reason_codes.append("FUNDAMENTAL_PROVIDER_MISSING")
    elif not market_ready:
        reason_codes.append("FUNDAMENTAL_BLOCKED_MARKET_BOOTSTRAP")

    if market_ready and capital_preflight_provider is not None and tickers and hasattr(capital_preflight_provider, "preflight"):
        try:
            capital_sample = capital_preflight_provider.preflight(tickers, as_of)
            checks["capital_preflight_data"] = bool(capital_sample) and any(
                getattr((fields or {}).get("capital_overhang_status"), "known", False)
                for fields in capital_sample.values())
            if not checks["capital_preflight_data"]:
                reason_codes.append("CAPITAL_PREFLIGHT_SAMPLE_EMPTY")
        except Exception:
            reason_codes.append("CAPITAL_PREFLIGHT_SAMPLE_FAILED")
    elif capital_preflight_provider is None:
        reason_codes.append("CAPITAL_PREFLIGHT_PROVIDER_MISSING")
    elif not market_ready:
        reason_codes.append("CAPITAL_PREFLIGHT_BLOCKED_MARKET_BOOTSTRAP")

    checks["market_scan_status"] = "MARKET_SCAN_READY" if market_ready else "BOOTSTRAP_REQUIRED"
    enrichment_ready = market_ready and checks["fundamental_data"]
    checks["enrichment_status"] = "ENRICHMENT_READY" if enrichment_ready else "BOOTSTRAP_REQUIRED"
    deep_ready = enrichment_ready and checks["capital_preflight_data"] and max_actual_llm_calls > 0
    checks["deep_handoff_status"] = "DEEP_HANDOFF_READY" if deep_ready else "BOOTSTRAP_REQUIRED"
    checks["status"] = ("DEEP_HANDOFF_READY" if deep_ready else
                         "ENRICHMENT_READY" if enrichment_ready else
                         "MARKET_SCAN_READY" if market_ready else "BOOTSTRAP_REQUIRED")
    # Kept as an explicit compatibility signal for operators/tests from v2;
    # the canonical status above is now stage-specific.
    checks["legacy_discovery_ready"] = market_ready
    checks["universe"] = universe_health
    checks["reason_codes"] = sorted(set(reason_codes))
    checks["checked_at"] = datetime.now(timezone.utc).isoformat()
    return checks
