"""Small dependency-free environment loader for local runtime commands."""
from __future__ import annotations

import os
from pathlib import Path


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return (key, value) if key else None


def load_environment(project_root: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Load `.env` without printing secrets; existing process env wins by default."""
    root = Path(project_root) if project_root else Path.cwd()
    path = root / ".env"
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        key, value = parsed
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def require_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing; set it in .env or the process environment")
    return value



