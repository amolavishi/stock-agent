from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".json", ".txt"}


def tracked_files(root: Path) -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root, text=False,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})
    return [root / item.decode("utf-8") for item in output.split(b"\0") if item]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for path in tracked_files(root):
        if path.suffix.lower() not in EXTENSIONS:
            continue
        try:
            path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(f"{path.relative_to(root)}: {exc}")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print("tracked source UTF-8 validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
