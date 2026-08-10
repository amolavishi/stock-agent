from __future__ import annotations

import os
import re


SECRET_NAMES = (
    "TOSS_APP_KEY", "TOSS_APP_SECRET", "DEEPSEEK_API_KEY",
    "DISCORD_RESEARCH_BOT_TOKEN", "DISCORD_CRITIC_BOT_TOKEN",
    "DISCORD_CHAIRMAN_BOT_TOKEN",
)

_REGISTERED_SECRETS: dict[str, str] = {}


def register_secret(name: str, value: str) -> None:
    """Register a runtime secret for value-based redaction without logging it."""
    if value and len(value) >= 4:
        _REGISTERED_SECRETS[name] = value


def register_known_secrets(values: dict[str, str]) -> None:
    for name, value in values.items():
        if any(marker in name.lower() for marker in ("secret", "token", "key", "password")):
            register_secret(name.upper(), value)


def redact_secrets(value: object) -> str:
    text = str(value)
    candidates = {name: os.getenv(name, "") for name in SECRET_NAMES}
    candidates.update(_REGISTERED_SECRETS)
    for name, secret in candidates.items():
        if secret:
            text = text.replace(secret, f"<{name}:REDACTED>")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bot|bearer)?\s*)[^\s,;]+", r"\1<REDACTED>", text)
    text = re.sub(r"(?i)(bearer\s+|bot\s+)[A-Za-z0-9._~-]+", r"\1<REDACTED>", text)
    text = re.sub(r"(?i)((?:api[_-]?key|client[_-]?secret|token)\s*[:=]\s*)[^\s,;]+",
                  r"\1<REDACTED>", text)
    return text
