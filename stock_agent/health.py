from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .hermes import HermesError, default_hermes_executable


def local_health(config: dict[str, Any], database) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        database.init()
        with database.connect() as connection:
            checks["sqlite_integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
            checks["sqlite_journal_mode"] = connection.execute("PRAGMA journal_mode").fetchone()[0]
            checks["schema_version"] = connection.execute(
                "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    except (OSError, sqlite3.Error) as exc:
        checks["sqlite_error"] = type(exc).__name__
    for name in ("report_dir", "vault_path"):
        path = Path(config[name])
        try:
            path.mkdir(parents=True, exist_ok=True)
            if name == "vault_path":
                # Never create a probe file in the user's real Obsidian Vault.
                checks[f"{name}_writable"] = path.is_dir() and os.access(path, os.W_OK)
            else:
                fd, test_path = tempfile.mkstemp(prefix=".stock_agent_health_", dir=path)
                os.close(fd)
                Path(test_path).unlink()
                checks[f"{name}_writable"] = True
        except OSError:
            checks[f"{name}_writable"] = False
    try:
        checks["hermes_executable"] = Path(default_hermes_executable()).is_file()
    except (HermesError, OSError):
        checks["hermes_executable"] = False
        checks["hermes_error"] = "NOT_FOUND"
    credentials = config.get("credentials", {})
    checks["credentials_present"] = {
        "toss": bool(credentials.get("toss_app_key") and credentials.get("toss_app_secret")),
        "deepseek": bool(credentials.get("deepseek_api_key")),
        "sec_user_agent": bool(credentials.get("sec_user_agent")),
        "discord_three_bots": all(bool(credentials.get(key)) for key in (
            "discord_research_token", "discord_critic_token", "discord_chairman_token")),
    }
    checks["paper_only"] = config.get("mode") == "PAPER"
    checks["healthy"] = (checks.get("sqlite_integrity") == "ok" and checks.get("paper_only")
                         and checks.get("report_dir_writable") and checks.get("vault_path_writable")
                         and checks.get("hermes_executable"))
    return checks
