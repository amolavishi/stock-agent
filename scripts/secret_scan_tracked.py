from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stock_agent.secret_scan import PATTERNS


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=root,
                                  env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})
    findings: list[tuple[str, int, str]] = []
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        relative = encoded.decode("utf-8")
        if relative == ".env" or relative.endswith(".html"):
            continue
        path = root / relative
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(lines, 1):
            for kind, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((relative, line_no, kind))
    if findings:
        for relative, line_no, kind in findings:
            print(f"{relative}:{line_no}:{kind}", file=sys.stderr)
        return 1
    print("tracked secret scan: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
