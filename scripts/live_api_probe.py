from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stock_agent.config import load_config
from stock_agent.tool_service import StockAgentToolService


def main() -> int:
    config = load_config()
    service = StockAgentToolService(config)
    summary: dict[str, object] = {}

    toss = service.market_get_snapshot("IONQ")
    summary["toss"] = {
        "ok": toss["ok"],
        "source": (toss.get("data") or {}).get("source"),
        "is_mock": (toss.get("data") or {}).get("is_mock"),
        "error_code": (toss.get("error") or {}).get("code"),
    }

    sec = service.sec_get_company_facts("IONQ")
    summary["sec"] = {
        "ok": sec["ok"],
        "error_code": (sec.get("error") or {}).get("code"),
    }

    discord: dict[str, object] = {}
    for role in ("research", "critic", "chairman"):
        result = service.discord_publish(role, f"[Stock Agent v0.6 연결 점검] {role} 봇/채널 정상 여부 테스트")
        discord[role] = {
            "ok": result["ok"],
            "error_code": (result.get("error") or {}).get("code"),
        }
    summary["discord_publish"] = discord

    credentials = config["credentials"]
    token = credentials["discord_chairman_token"]
    guild_id = credentials["discord_guild_id"]
    headers = {"Authorization": f"Bot {token}"}
    with httpx.Client(timeout=15) as client:
        me = client.get("https://discord.com/api/v10/users/@me", headers=headers)
        command_names: set[str] = set()
        if me.is_success:
            app_id = me.json()["id"]
            for url in (
                f"https://discord.com/api/v10/applications/{app_id}/commands",
                f"https://discord.com/api/v10/applications/{app_id}/guilds/{guild_id}/commands",
            ):
                response = client.get(url, headers=headers)
                if response.is_success:
                    command_names.update(item.get("name", "") for item in response.json())
        summary["discord_commands"] = {
            "bot_identity_ok": me.is_success,
            "analyze_registered": "analyze" in command_names,
            "registered_count": len(command_names),
        }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["toss"]["ok"] and all(item["ok"] for item in discord.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
