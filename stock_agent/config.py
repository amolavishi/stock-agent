from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class DiscoveryConfigError(ValueError):
    pass


def validate_discovery_config(discovery: dict[str, Any]) -> None:
    universe = discovery.get("universe", {})
    coverage = discovery.get("coverage", {})
    stage = discovery.get("stage", {})
    bootstrap = discovery.get("bootstrap", {})
    scorecard = discovery.get("final_scorecard", {})
    deprecated = {key for key in ("min_identity_pct", "min_sector_pct") if key in bootstrap}
    if deprecated:
        raise DiscoveryConfigError("deprecated discovery.bootstrap coverage keys: " + ", ".join(sorted(deprecated)))
    for key in ("min_identity_coverage_pct", "min_sector_coverage_pct"):
        value = bootstrap.get(key, 0)
        if not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise DiscoveryConfigError(f"discovery.bootstrap.{key} must be between 0 and 100")
    if not isinstance(discovery.get("enabled", False), bool) or not isinstance(discovery.get("shadow_mode", True), bool):
        raise DiscoveryConfigError("discovery.enabled and discovery.shadow_mode must be boolean")
    for key in ("market_min_pct", "feature_min_pct", "fundamental_enrichment_min_pct", "capital_preflight_min_pct"):
        value = coverage.get(key, 0)
        if not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise DiscoveryConfigError(f"discovery.coverage.{key} must be between 0 and 100")
    coverage_limit = scorecard.get("min_coverage_pct", 75)
    if not isinstance(coverage_limit, (int, float)) or not 0 <= coverage_limit <= 100:
        raise DiscoveryConfigError("discovery.final_scorecard.min_coverage_pct must be between 0 and 100")
    reward_risk = scorecard.get("min_reward_risk", 1.5)
    if not isinstance(reward_risk, (int, float)) or reward_risk <= 0:
        raise DiscoveryConfigError("discovery.final_scorecard.min_reward_risk must be positive")
    for key, value in discovery.get("cost", {}).items():
        if value is not None and (not isinstance(value, (int, float)) or value < 0):
            raise DiscoveryConfigError(f"discovery.cost.{key} must be non-negative or null")
    for key in ("min_price", "min_market_cap_usd", "min_adv20_usd"):
        value = universe.get(key, 0)
        if not isinstance(value, (int, float)) or value <= 0:
            raise DiscoveryConfigError(f"discovery.universe.{key} must be positive")
    for key in ("stage3_return_1d_pct", "stage3_return_5d_pct", "stage3_return_20d_pct", "stage3_distance_ma20_pct", "stage3_atr_multiple"):
        value = stage.get(key, 0)
        if not isinstance(value, (int, float)) or value <= 0:
            raise DiscoveryConfigError(f"discovery.stage.{key} must be positive")


def load_dotenv() -> None:
    """Load the local .env without adding a third-party dependency."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_json_yaml(path: Path) -> dict[str, Any]:
    # The checked-in .yaml files are JSON-compatible YAML, so the MVP stays stdlib-only.
    return json.loads(path.read_text(encoding="utf-8"))


def load_config() -> dict[str, Any]:
    load_dotenv()
    config = load_json_yaml(ROOT / "config" / "config.yaml")
    config["mode"] = os.getenv("MODE", config.get("mode", "PAPER"))
    config["database_path"] = os.getenv("DATABASE_PATH", config["database_path"])
    config["vault_path"] = os.getenv("OBSIDIAN_VAULT_PATH", config["vault_path"])
    def resolved(value: str) -> str:
        path = Path(value)
        return str((path if path.is_absolute() else ROOT / path).resolve())

    config["database_path"] = resolved(config["database_path"])
    config["vault_path"] = resolved(config["vault_path"])
    config["obsidian"] = {
        "enabled": os.getenv("OBSIDIAN_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        "vault_path": config["vault_path"],
        "companies_dir": os.getenv("OBSIDIAN_COMPANIES_DIR", "02_Companies"),
        "reports_dir": os.getenv("OBSIDIAN_REPORTS_DIR", "05_Reports"),
        "decision_log_dir": os.getenv("OBSIDIAN_DECISION_LOG_DIR", "06_Decision_Log"),
    }
    config["report_dir"] = resolved(os.getenv("REPORT_DIR", config.get("report_dir", "data/reports")))
    config["max_concurrent_analysis_runs"] = int(os.getenv("MAX_CONCURRENT_ANALYSIS_RUNS", "1"))
    config["clarification_timeout_minutes"] = int(os.getenv("CLARIFICATION_TIMEOUT_MINUTES", "20"))
    config["edgar_mode"] = os.getenv("EDGAR_MODE", config.get("edgar_mode", "mock"))
    config["market_data_provider"] = os.getenv("MARKET_DATA_PROVIDER", config.get("provider", "mock"))
    config["agent_provider"] = os.getenv("AGENT_PROVIDER", config.get("agent_provider", "mock"))
    config["hermes_transport"] = os.getenv("HERMES_TRANSPORT", "cli")
    config["hermes_endpoint"] = os.getenv("HERMES_ENDPOINT", "http://127.0.0.1:9119/api/v1/rpc")
    config["hermes_model"] = os.getenv("HERMES_MODEL", os.getenv("LLM_RESEARCH_MODEL", config.get("hermes_model", "")))
    config["hermes_timeout_seconds"] = int(os.getenv("HERMES_TIMEOUT_SECONDS", "360"))
    config["hermes_parser_timeout_seconds"] = int(os.getenv("HERMES_PARSER_TIMEOUT_SECONDS", "90"))
    config["database"] = config.get("database", {}) | {
        "busy_timeout_ms": int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", config.get("database", {}).get("busy_timeout_ms", 5000))),
        "wal": os.getenv("SQLITE_WAL", str(config.get("database", {}).get("wal", True))).lower() in {"1", "true", "yes"},
    }
    paper = config.get("paper", {})
    config["paper"] = paper | {
        "account_id": os.getenv("PAPER_ACCOUNT_ID", paper.get("account_id", "PAPER_DEFAULT")),
        "initial_cash_usd": float(os.getenv("PAPER_INITIAL_CASH_USD", paper.get("initial_cash_usd", 100000))),
        "max_total_exposure_pct": float(os.getenv("PAPER_MAX_TOTAL_EXPOSURE_PCT", paper.get("max_total_exposure_pct", 60))),
        "max_sector_exposure_pct": float(os.getenv("PAPER_MAX_SECTOR_EXPOSURE_PCT", paper.get("max_sector_exposure_pct", 25))),
    }
    cost = config.get("cost_guard", {})
    config["cost_guard"] = cost | {
        "mode": os.getenv("COST_GUARD_MODE", cost.get("mode", "WARN")),
        "soft_cost_limit_usd": float(os.getenv("SOFT_COST_LIMIT_USD", cost.get("soft_cost_limit_usd", 0))),
        "hard_cost_limit_usd": float(os.getenv("HARD_COST_LIMIT_USD", cost.get("hard_cost_limit_usd", 0))),
    }
    config["credentials"] = {
        "toss_app_key": os.getenv("TOSS_APP_KEY", ""),
        "toss_app_secret": os.getenv("TOSS_APP_SECRET", ""),
        "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "sec_user_agent": os.getenv("SEC_USER_AGENT", ""),
        "discord_research_token": os.getenv("DISCORD_RESEARCH_BOT_TOKEN", ""),
        "discord_critic_token": os.getenv("DISCORD_CRITIC_BOT_TOKEN", ""),
        "discord_chairman_token": os.getenv("DISCORD_CHAIRMAN_BOT_TOKEN", ""),
        "discord_guild_id": os.getenv("DISCORD_GUILD_ID", ""),
        "discord_command_channel_id": os.getenv("DISCORD_COMMAND_CHANNEL_ID", os.getenv("DISCORD_CHANNEL_ID", "")),
        "discord_channel_id": os.getenv("DISCORD_CHANNEL_ID", ""),
        "discord_debate_channel_id": os.getenv("DISCORD_DEBATE_CHANNEL_ID", ""),
        "discord_report_channel_id": os.getenv("DISCORD_REPORT_CHANNEL_ID", ""),
        "discord_owner_user_id": os.getenv("DISCORD_OWNER_USER_ID", ""),
        "discord_allowed_user_ids": os.getenv("DISCORD_ALLOWED_USER_IDS", ""),
    }
    from .security import register_known_secrets
    register_known_secrets(config["credentials"])
    config["risk_rules"] = load_json_yaml(ROOT / "config" / "risk_rules.yaml")
    discovery = config.get("discovery", {})
    discovery = discovery | {
        "enabled": os.getenv("DISCOVERY_ENABLED", str(discovery.get("enabled", False))).lower() in {"1", "true", "yes", "on"},
        "shadow_mode": os.getenv("DISCOVERY_SHADOW_MODE", str(discovery.get("shadow_mode", True))).lower() in {"1", "true", "yes", "on"},
    }
    bootstrap = discovery.get("bootstrap", {})
    enrichment_path = os.getenv("DISCOVERY_SECURITY_MASTER_ENRICHMENT_PATH",
                               bootstrap.get("security_master_enrichment_path", ""))
    fundamental_cache = os.getenv("DISCOVERY_FUNDAMENTAL_CACHE_DIR",
                                  bootstrap.get("fundamental_cache_dir", "data/cache/discovery/fundamentals"))
    raw_cache = os.getenv("DISCOVERY_SECURITY_MASTER_RAW_CACHE_DIR",
                          bootstrap.get("raw_cache_dir", "data/cache/discovery/security_master/raw"))
    normalized_cache = os.getenv("DISCOVERY_SECURITY_MASTER_NORMALIZED_CACHE_DIR",
                                 bootstrap.get("normalized_cache_dir", "data/cache/discovery/security_master/normalized"))
    sector_cache = os.getenv("DISCOVERY_SECURITY_MASTER_SECTOR_CACHE_DIR",
                             bootstrap.get("sector_cache_dir", "data/cache/discovery/security_master/raw/sec_submissions"))
    discovery["bootstrap"] = bootstrap | {
        "security_master_enrichment_path": resolved(enrichment_path) if enrichment_path else "",
        "fundamental_cache_dir": resolved(fundamental_cache),
        "raw_cache_dir": resolved(raw_cache),
        "normalized_cache_dir": resolved(normalized_cache),
        "sector_cache_dir": resolved(sector_cache),
        "max_issuer_metadata_requests": int(os.getenv(
            "DISCOVERY_MAX_ISSUER_METADATA_REQUESTS",
            str(bootstrap.get("max_issuer_metadata_requests", 10000)))),
    }
    config["discovery"] = discovery
    validate_discovery_config(config["discovery"])
    return config
