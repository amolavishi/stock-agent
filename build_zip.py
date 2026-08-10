from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


EXCLUDED_NAMES = {".env", "data", "vault", "__pycache__", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".db", ".zip"}


def iter_distribution_files(root: Path):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file():
            continue
        if any(part in EXCLUDED_NAMES for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        yield path, relative.as_posix()


def create_distribution_zip(root: Path, output: Path) -> Path:
    root = root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in iter_distribution_files(root):
            archive.write(path, arcname=arcname)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a portable Stock Agent distribution ZIP")
    parser.add_argument("--output", default="stock-agent-Hybrid-PAPER-v0.6.zip")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = Path(args.output)
    if not output.is_absolute():
        output = root.parent / output
    result = create_distribution_zip(root, output)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
