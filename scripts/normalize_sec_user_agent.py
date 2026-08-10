from __future__ import annotations

import re
from pathlib import Path


def main() -> int:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    text = env_path.read_text(encoding="utf-8-sig")
    # Recover a common manual-editing mistake where SEC_USER_AGENT is pasted
    # onto the end of the preceding AGENT_PROVIDER line.
    text = text.replace(
        "AGENT_PROVIDER=hermesSEC_USER_AGENT=",
        "AGENT_PROVIDER=hermes\nSEC_USER_AGENT=",
    )
    match = re.search(r"(?m)^SEC_USER_AGENT=(.*)$", text)
    if not match:
        raise SystemExit("SEC_USER_AGENT is missing")
    current = match.group(1).strip()
    if not current:
        raise SystemExit("SEC_USER_AGENT is empty")
    if current.lower().startswith("stockagent/"):
        env_path.write_text(text, encoding="utf-8")
        print("SEC_USER_AGENT already normalized")
        return 0
    normalized = f"StockAgent/0.6 {current}"
    updated = text[: match.start(1)] + normalized + text[match.end(1) :]
    env_path.write_text(updated, encoding="utf-8")
    print("SEC_USER_AGENT normalized without printing contact information")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
