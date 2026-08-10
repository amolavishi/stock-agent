from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stock_agent.config import load_config


def read_env(path: Path) -> dict[str, str]:
    result = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in raw and not raw.lstrip().startswith("#"):
            key, value = raw.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def main() -> int:
    env_path = ROOT / ".env"
    values = read_env(env_path)
    credentials = load_config()["credentials"]
    headers = {"Authorization": f"Bot {credentials['discord_chairman_token']}"}
    with httpx.Client(timeout=20) as client:
        app = client.get("https://discord.com/api/v10/oauth2/applications/@me", headers=headers)
        channels = client.get(
            f"https://discord.com/api/v10/guilds/{credentials['discord_guild_id']}/channels",
            headers=headers)
    app.raise_for_status(); channels.raise_for_status()
    by_name = {item["name"]: str(item["id"]) for item in channels.json() if item.get("type") == 0}
    required = {"DISCORD_COMMAND_CHANNEL_ID": "명령채널",
                "DISCORD_DEBATE_CHANNEL_ID": "토론의장",
                "DISCORD_REPORT_CHANNEL_ID": "보고서제출"}
    missing = [name for name in required.values() if name not in by_name]
    if missing:
        raise RuntimeError("missing Discord channels: " + ", ".join(missing))
    for key, name in required.items():
        values[key] = by_name[name]
    values["DISCORD_OWNER_USER_ID"] = str((app.json().get("owner") or {}).get("id", ""))
    order = list(values)
    env_path.write_text("# Generated configuration; keep private.\n" +
                        "\n".join(f"{key}={values[key]}" for key in order) + "\n",
                        encoding="utf-8")
    print("Discord command/debate/report channels and owner ID configured; secrets not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
