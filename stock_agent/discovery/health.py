from __future__ import annotations


def bootstrap_health(database, security_master=None, market_data=None) -> dict:
    checks = {"schema": False, "security_master": False, "market_data": False,
              "daily_bar_cache": False, "status": "FAILED"}
    try:
        database.init()
        with database.connect() as connection:
            checks["schema"] = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version>=23").fetchone() is not None
            checks["daily_bar_cache"] = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_bars'").fetchone() is not None
    except Exception:
        return checks
    checks["security_master"] = security_master is not None and security_master.__class__.__name__ != "EmptySecurityMasterProvider"
    checks["market_data"] = market_data is not None and market_data.__class__.__name__ != "EmptyDiscoveryMarketDataProvider"
    checks["status"] = "DISCOVERY_READY" if all(checks.values()) else "BOOTSTRAP_REQUIRED"
    return checks
