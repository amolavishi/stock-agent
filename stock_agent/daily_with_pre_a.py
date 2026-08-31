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
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Iterable


class DailyPreAChainError(RuntimeError):
    """Fail-closed wrapper error."""


_LEGACY_SENTINELS = {"unstarted", "not_started", "not-started"}


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


def _legacy_default_is_sentinel(value: object) -> bool:
    if value is None:
        return False
    text = str(value).strip().casefold()
    # SQLite may expose a constant default as 'unstarted', ('unstarted'),
    # or with double quotes depending on the historical table DDL.  Normalize
    # only wrapping syntax; do not treat arbitrary default expressions as a
    # legacy sentinel.
    while len(text) >= 2 and text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    text = text.strip("'\"").strip()
    return text in _LEGACY_SENTINELS


def _repair_legacy_shadow_schema(database: Path) -> bool:
    """Neutralize obsolete Shadow run-pointer sentinels in an existing DB.

    Historical ``shadow_v1.db`` files can contain ``hunt_run_id`` or
    ``execution_run_id`` values such as ``'unstarted'``.  Some legacy tables
    also retain that value as a column default.  Current PRIMARY code treats
    any non-empty pointer as a real ``runs.run_id``; therefore either condition
    can skip HUNT and later fail with ``KeyError('unstarted')``.

    Repair is intentionally narrow:
    - persisted sentinel values are normalized even when the current column
      default is already NULL;
    - a compatibility trigger is installed only when PRAGMA confirms that a
      legacy sentinel is still the column default, so future INSERTs cannot
      recreate the invalid pointer;
    - unrelated run ids and current nullable schemas are untouched.

    Returns True only when a sentinel value/default was detected and repaired.
    """
    database = database.expanduser()
    if not database.exists() or not database.is_file():
        return False

    connection = sqlite3.connect(str(database))
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_runs'"
        ).fetchone()
        if table is None:
            return False

        columns = {
            str(row[1]): {"notnull": bool(row[3]), "default": row[4]}
            for row in connection.execute("PRAGMA table_info(shadow_runs)").fetchall()
        }
        relevant = {
            name: columns.get(name)
            for name in ("hunt_run_id", "execution_run_id")
            if columns.get(name) is not None
        }
        if not relevant:
            return False

        placeholders = ",".join("?" for _ in _LEGACY_SENTINELS)
        sentinel_params = tuple(sorted(_LEGACY_SENTINELS))
        default_is_legacy = any(_legacy_default_is_sentinel(info["default"]) for info in relevant.values())
        persisted_is_legacy = False
        for name in relevant:
            row = connection.execute(
                f"SELECT 1 FROM shadow_runs WHERE lower(trim(COALESCE({name}, ''))) IN ({placeholders}) LIMIT 1",
                sentinel_params,
            ).fetchone()
            if row is not None:
                persisted_is_legacy = True
                break
        if not default_is_legacy and not persisted_is_legacy:
            return False

        replacements = {
            name: "" if bool(info["notnull"]) else None
            for name, info in relevant.items()
        }
        with connection:
            for name, replacement in replacements.items():
                connection.execute(
                    f"UPDATE shadow_runs SET {name}=? WHERE lower(trim(COALESCE({name}, ''))) IN ({placeholders})",
                    (replacement, *sentinel_params),
                )

            if default_is_legacy:
                # The old column default remains part of the local table DDL,
                # so a one-time UPDATE alone is insufficient.  This trigger is
                # installed only for a confirmed legacy default.
                hunt_replacement = "''" if replacements.get("hunt_run_id") == "" else "NULL"
                execution_replacement = "''" if replacements.get("execution_run_id") == "" else "NULL"
                sentinels_sql = ",".join("'" + item.replace("'", "''") + "'" for item in sorted(_LEGACY_SENTINELS))
                connection.execute("DROP TRIGGER IF EXISTS shadow_runs_legacy_sentinel_normalizer")
                connection.execute(
                    f"""
                    CREATE TRIGGER shadow_runs_legacy_sentinel_normalizer
                    AFTER INSERT ON shadow_runs
                    FOR EACH ROW
                    WHEN lower(trim(COALESCE(NEW.hunt_run_id, ''))) IN ({sentinels_sql})
                      OR lower(trim(COALESCE(NEW.execution_run_id, ''))) IN ({sentinels_sql})
                    BEGIN
                        UPDATE shadow_runs
                        SET hunt_run_id = CASE
                                WHEN lower(trim(COALESCE(NEW.hunt_run_id, ''))) IN ({sentinels_sql}) THEN {hunt_replacement}
                                ELSE NEW.hunt_run_id
                            END,
                            execution_run_id = CASE
                                WHEN lower(trim(COALESCE(NEW.execution_run_id, ''))) IN ({sentinels_sql}) THEN {execution_replacement}
                                ELSE NEW.execution_run_id
                            END
                        WHERE shadow_run_id = NEW.shadow_run_id;
                    END
                    """
                )
        return True
    finally:
        connection.close()


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
        database = Path(_option_value(primary_args, "--database", "stock_agent.db"))
    except DailyPreAChainError as exc:
        parser.error(str(exc))

    try:
        repaired_legacy_schema = _repair_legacy_shadow_schema(database)
    except (OSError, sqlite3.DatabaseError) as exc:
        print(f"PRE-A CHAIN ABORTED: failed to inspect legacy Shadow DB compatibility: {exc}", file=sys.stderr)
        return 5
    if repaired_legacy_schema:
        print(f"LEGACY SHADOW DB REPAIRED: normalized obsolete run-id sentinels in {database}")

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
