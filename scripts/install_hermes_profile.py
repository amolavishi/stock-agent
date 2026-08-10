from __future__ import annotations

import shutil
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path.home() / "AppData" / "Local" / "hermes" / "profiles" / "stockagent"
HERMES_ROOT = Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent"
PYTHON = HERMES_ROOT / "venv" / "Scripts" / "python.exe"
ADAPTER = HERMES_ROOT / "plugins" / "platforms" / "discord" / "adapter.py"

TOOLS = [
    "market_get_snapshot", "market_get_regime", "market_get_benchmark_snapshots",
    "sec_get_filing_evidence", "sec_get_company_facts", "sec_request_additional_evidence",
    "state_get_company_state", "state_get_latest_thesis", "state_get_decision_history",
    "state_get_portfolio_state", "risk_evaluate", "sizing_calculate",
    "guard_validate_claims", "guard_validate_trade_plan", "guard_validate_final",
    "audit_start_run", "audit_save_stage_output", "audit_complete_run", "audit_fail_run",
    "paper_record_prediction", "paper_update_performance", "discord_publish_research",
    "discord_publish_critic", "discord_publish_chairman", "discord_publish_error",
]

PATCH_MARKER = "# STOCK_AGENT_NATIVE_SKILL_COMMANDS_V001"
INTENT_PATCH_MARKER = "# STOCK_AGENT_SLASH_ONLY_INTENTS_V001"
PATCH_ANCHOR = "        # Register skills under a single /skill command group with category"
PATCH_TEXT = '''        # STOCK_AGENT_NATIVE_SKILL_COMMANDS_V001
        # Compatibility shim for selected aliases that must appear as first-class
        # Discord commands while still falling through to Hermes skill dispatch.
        try:
            from hermes_cli.config import load_config as _stock_load_config
            _stock_native = (_stock_load_config() or {}).get(
                "discord_native_skill_commands", {}
            ) or {}
            if isinstance(_stock_native, dict):
                for _stock_alias in _stock_native:
                    _stock_discord_name = str(_stock_alias).lower()[:32]
                    if _stock_discord_name in already_registered:
                        continue
                    if len(already_registered) >= slot_cap:
                        dropped_over_cap += 1
                        continue
                    _stock_cmd = _build_auto_slash_command(
                        _stock_discord_name,
                        "Run evidence-based PAPER stock analysis",
                        "ticker",
                    )
                    tree.add_command(_stock_cmd)
                    already_registered.add(_stock_discord_name)
        except Exception as e:
            logger.warning("Discord native skill alias registration failed: %s", e)

'''


def read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("\n".join(f"{key}={value}" for key, value in sorted(values.items())) + "\n",
                    encoding="utf-8")


def merge(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def install_profile() -> None:
    if not PROFILE.exists():
        raise RuntimeError("stockagent profile is missing; create it first")
    config_path = PROFILE / "config.yaml"
    current = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    overlay = yaml.safe_load((ROOT / "hermes" / "profile-config.v001.yaml").read_text(encoding="utf-8"))
    merged = merge(current or {}, overlay or {})
    merged.setdefault("mcp_servers", {})["stock-agent"].update({
        "command": str(PYTHON),
        "args": [str(ROOT / "mcp_server.py")],
        "tools": {"include": TOOLS},
    })
    merged["discord"] = {
        "require_mention": True,
        "thread_require_mention": True,
        "auto_thread": False,
        "reactions": True,
        "history_backfill": False,
    }
    config_path.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True), encoding="utf-8")

    source_env = read_env(ROOT / ".env")
    required = ["DEEPSEEK_API_KEY", "DISCORD_CHAIRMAN_BOT_TOKEN", "DISCORD_DEBATE_CHANNEL_ID"]
    missing = [key for key in required if not source_env.get(key)]
    if missing:
        raise RuntimeError("missing project environment keys: " + ", ".join(missing))
    profile_env = read_env(PROFILE / ".env")
    profile_env.update({
        "DEEPSEEK_API_KEY": source_env["DEEPSEEK_API_KEY"],
        "DISCORD_BOT_TOKEN": source_env["DISCORD_CHAIRMAN_BOT_TOKEN"],
        "DISCORD_ALLOWED_CHANNELS": source_env["DISCORD_DEBATE_CHANNEL_ID"],
        "DISCORD_HOME_CHANNEL": source_env.get("DISCORD_REPORT_CHANNEL_ID", ""),
        "DISCORD_ALLOW_ALL_USERS": "false",
        "DISCORD_SLASH_ONLY": "true",
        "HERMES_MODEL": "deepseek-v4-flash",
    })
    write_env(PROFILE / ".env", profile_env)

    skill_target = PROFILE / "skills" / "stock-analyze"
    skill_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "hermes" / "skills" / "stock-analyze" / "SKILL.md",
                 skill_target / "SKILL.md")
    shutil.copy2(ROOT / "hermes" / "AGENTS.md", PROFILE / "SOUL.md")


def install_discord_patch() -> None:
    text = ADAPTER.read_text(encoding="utf-8")
    backup = ADAPTER.with_suffix(".py.stockagent-v001.bak")
    if not backup.exists():
        shutil.copy2(ADAPTER, backup)
    changed = False
    if PATCH_MARKER not in text:
        if PATCH_ANCHOR not in text:
            raise RuntimeError("unsupported Hermes Discord adapter; patch anchor not found")
        text = text.replace(PATCH_ANCHOR, PATCH_TEXT + PATCH_ANCHOR, 1)
        changed = True
    if INTENT_PATCH_MARKER not in text:
        intent_anchor = "            intents.message_content = True"
        intent_replacement = '''            # STOCK_AGENT_SLASH_ONLY_INTENTS_V001
            # A slash-command-only gateway does not need the privileged Message
            # Content intent. Normal Hermes profiles retain the upstream default.
            intents.message_content = os.getenv(
                "DISCORD_SLASH_ONLY", "false"
            ).strip().lower() not in {"true", "1", "yes", "on"}'''
        if intent_anchor not in text:
            raise RuntimeError("unsupported Hermes Discord intent block")
        text = text.replace(intent_anchor, intent_replacement, 1)
        changed = True
    if changed:
        ADAPTER.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    install_profile()
    install_discord_patch()
    print("stockagent profile installed; secrets were not printed")
