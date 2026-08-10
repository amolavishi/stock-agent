from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line: int
    kind: str


PATTERNS = {
    "PRIVATE_KEY": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "BEARER_TOKEN": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "DISCORD_TOKEN": re.compile(
        r"\b[A-Za-z\d_-]{20,40}\.[A-Za-z\d_-]{6,8}\.[A-Za-z\d_-]{20,120}\b"),
    "ASSIGNED_SECRET": re.compile(
        r"(?i)\b(?:api[_-]?key|client[_-]?secret|discord[_-]?token|authorization)\b\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{16,}"),
}


def scan_tree(root: str, *, exclude_names: set[str] | None = None) -> list[SecretFinding]:
    base = Path(root).resolve()
    excluded = exclude_names or {".env", ".git", "venv", "__pycache__", ".pytest_cache"}
    findings: list[SecretFinding] = []
    for path in base.rglob("*"):
        if not path.is_file() or any(part in excluded for part in path.parts):
            continue
        if path.stat().st_size > 5_000_000:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(lines, 1):
            for kind, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(SecretFinding(str(path), line_no, kind))
    return findings
