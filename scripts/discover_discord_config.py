from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stock_agent.config import load_config


def main() -> int:
    credentials = load_config()["credentials"]
    headers = {"Authorization": f"Bot {credentials['discord_chairman_token']}"}
    guild_id = credentials["discord_guild_id"]
    with httpx.Client(timeout=20) as client:
        app = client.get("https://discord.com/api/v10/oauth2/applications/@me", headers=headers)
        channels = client.get(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers)
    result = {"application_ok": app.is_success, "channels_ok": channels.is_success,
              "application_id": "", "owner_user_id": "", "text_channels": []}
    if app.is_success:
        result["application_id"] = str(app.json().get("id", ""))
        owner = (app.json().get("owner") or {})
        result["owner_user_id"] = str(owner.get("id", ""))
    if channels.is_success:
        result["text_channels"] = [
            {"id": str(item["id"]), "name": item["name"]}
            for item in channels.json() if item.get("type") == 0
        ]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if app.is_success and channels.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
