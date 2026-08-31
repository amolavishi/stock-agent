"""One-command PRIMARY Shadow -> PRE-A sidecar chain.

This wrapper deliberately keeps PRE-A outside the authoritative Stock Agent
pipeline.  It first executes exactly one PRIMARY ``--daily-shadow-run``.  Only
when PRIMARY exits successfully does it locate the DAILY_REPORT.md written by
that run and invoke the independent PRE-A sidecar against that completed
report.

The PRE-A step cannot mutate PRIMARY SQLite state, Shadow artifacts, Research
Grade, Execution Action, Position Size, or broker state.  A PRE-A failure also
does not rewrite or invalidate the completed PRIMARY run; it returns a nonzero
wrapper exit code so the operator sees that the secondary report is missing.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable


class DailyPreAChainError(RuntimeError):
    """Fail-closed wrapper error."""


def _option_value(argv: list[str], name: str, default: str) -> str:
    """Read one CLI option without interpreting the rest of PRIMARY's CLI."""
    prefix = name + "="
    for index, token in enumerate(argv):
        if token.startswith(prefix):
            value = token[len(prefix):]
            if not value:
                raise DailyPreAChainError(f"{name} requires a value")
            return value
        if token == name:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise DailyPreAChainError(f"{name} requires a value")
            return argv[index + 1]
    return default


def _snapshot_reports(root: Path) -> dict[Path, int]:
    """Return DAILY_REPORT mtimes without reading or mutating report contents."""
    if not root.exists():
        return {}
    snapshot: dict[Path, int] = {}
    for path in root.glob("*/DAILY_REPORT.md"):
        try:
            snapshot[path.resolve()] = path.stat().st_mtime_ns
        except OSError:
            continue
    return snapshot


def _select_changed_report(before: dict[Path, int], after: dict[Path, int]) -> Path:
    changed = [
        path
        for path, mtime in after.items()
        if path not in before or mtime > before[path]
    ]
    if len(changed) != 1:
        raise DailyPreAChainError(
            "expected exactly one PRIMARY DAILY_REPORT.md created/updated by this run; "
            f"observed {len(changed)}"
        )
    return changed[0]


def _prepare_primary_args(argv: Iterable[str]) -> list[str]:
    args = list(argv)
    if "--daily-shadow-with-v8" in args or any(token.startswith("--daily-shadow-with-v8=") for token in args):
        raise DailyPreAChainError("PRE-A daily wrapper supports PRIMARY --daily-shadow-run only; V8 remains independent")
    if "--daily-shadow-run" not in args:
        args.insert(0, "--daily-shadow-run")
    return args


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one authoritative PRIMARY Shadow cycle, then derive an independent PRE-A report from its DAILY_REPORT.md",
        add_help=True,
    )
    parser.add_argument("--pre-a-llm-provider", choices=["luna", "codex"], default="luna")
    parser.add_argument("--pre-a-reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--pre-a-output-root", type=Path, default=Path("pre_a_reports"))
    known, passthrough = parser.parse_known_args()

    try:
        primary_args = _prepare_primary_args(passthrough)
        shadow_output = Path(_option_value(primary_args, "--shadow-output", "shadow_runs"))
    except DailyPreAChainError as exc:
        parser.error(str(exc))

    before = _snapshot_reports(shadow_output)
    primary_cmd = [sys.executable, "-m", "stock_agent", *primary_args]
    primary = subprocess.run(primary_cmd, check=False)
    if primary.returncode != 0:
        print("PRE-A SKIPPED: PRIMARY daily Shadow run did not complete successfully.", file=sys.stderr)
        return int(primary.returncode or 2)

    after = _snapshot_reports(shadow_output)
    try:
        source_report = _select_changed_report(before, after)
    except DailyPreAChainError as exc:
        print(f"PRE-A SKIPPED: {exc}", file=sys.stderr)
        return 3

    run_id = source_report.parent.name
    output_path = known.pre_a_output_root / run_id / "PRE_A_REPORT.md"
    sidecar_cmd = [
        sys.executable,
        "-m",
        "stock_agent.pre_a_sidecar",
        "--source-report",
        str(source_report),
        "--output",
        str(output_path),
        "--llm-provider",
        known.pre_a_llm_provider,
    ]
    if known.pre_a_reasoning_effort:
        sidecar_cmd.extend(["--reasoning-effort", known.pre_a_reasoning_effort])

    print(f"PRIMARY COMPLETE: deriving PRE-A report from {source_report}")
    sidecar = subprocess.run(sidecar_cmd, check=False)
    if sidecar.returncode != 0:
        print(
            "PRIMARY remains complete, but PRE-A sidecar report generation failed. "
            "No PRIMARY state was modified by the sidecar.",
            file=sys.stderr,
        )
        return int(sidecar.returncode or 4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
