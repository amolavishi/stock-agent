"""One-command PRIMARY Shadow -> structured PRE-A sidecar chain.

PRIMARY is run first and remains authoritative.  PRE-A then opens the completed
PRIMARY SQLite database read-only and derives its model input from persisted
ShadowDecision/StageResult state.  DAILY_REPORT.md is located only to bind the
sidecar output to the human artifact; its wording is not PRE-A evidence.
"""
from __future__ import annotations

import argparse
import json
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
    while len(text) >= 2 and text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    text = text.strip("'\"").strip()
    return text in _LEGACY_SENTINELS


def _repair_legacy_shadow_schema(database: Path) -> bool:
    """Neutralize obsolete Shadow run-pointer sentinels in an existing DB."""
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
    """Return DAILY_REPORT mtimes from the canonical date/run directory tree."""
    if not root.exists():
        return {}
    snapshot: dict[Path, int] = {}
    # Canonical Shadow paths are <root>/<YYYY-MM-DD>/<RUN-ID>/DAILY_REPORT.md.
    # The previous one-level glob could never see a successfully written report
    # and therefore emitted `observed 0` after every PRIMARY run.
    for path in root.glob("*/*/DAILY_REPORT.md"):
        try:
            snapshot[path.resolve()] = path.stat().st_mtime_ns
        except OSError:
            continue
    return snapshot


def _select_changed_report(before: dict[Path, int], after: dict[Path, int]) -> Path:
    changed = [path for path, mtime in after.items() if path not in before or mtime > before[path]]
    if len(changed) != 1:
        raise DailyPreAChainError(
            "expected exactly one PRIMARY DAILY_REPORT.md created/updated by this run; "
            f"observed {len(changed)}"
        )
    return changed[0]


def _primary_is_pre_a_evaluable(source_report: Path) -> tuple[bool, str]:
    """PRE-A may observe only a completed, evaluable PRIMARY HUNT."""
    run_log = source_report.parent / "RUN_LOG.json"
    if not run_log.is_file():
        return False, "PRIMARY RUN_LOG.json is missing"
    try:
        payload = json.loads(run_log.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False, "PRIMARY RUN_LOG.json is malformed"
    if not isinstance(payload, dict):
        return False, "PRIMARY RUN_LOG.json is not an object"
    status = str(payload.get("status") or "UNKNOWN")
    conclusion = str(payload.get("investment_conclusion") or "UNKNOWN")
    hunt_contract = payload.get("hunt_contract") if isinstance(payload.get("hunt_contract"), dict) else {}
    hunt_status = str(hunt_contract.get("status") or "UNKNOWN")
    hunt_result = str(hunt_contract.get("result") or "UNKNOWN")
    if status != "SUCCEEDED":
        return False, f"PRIMARY Shadow status is {status}"
    if conclusion.startswith("NOT_EVALUABLE_") or hunt_result.startswith("NOT_EVALUABLE_"):
        return False, f"PRIMARY HUNT is non-evaluable: {hunt_result or conclusion}"
    if hunt_status == "FAILED" or hunt_result.startswith("BLOCKED"):
        return False, f"PRIMARY HUNT did not complete evaluably: {hunt_result}"
    return True, "EVALUABLE"


def _prepare_primary_args(argv: Iterable[str]) -> list[str]:
    args = list(argv)
    if "--daily-shadow-with-v8" in args or any(token.startswith("--daily-shadow-with-v8=") for token in args):
        raise DailyPreAChainError("PRE-A daily wrapper supports PRIMARY --daily-shadow-run only; V8 remains independent")
    if "--daily-shadow-run" not in args:
        args.insert(0, "--daily-shadow-run")
    return args


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one authoritative PRIMARY Shadow cycle, then derive PRE-A from read-only structured PRIMARY state",
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

    evaluable, reason = _primary_is_pre_a_evaluable(source_report)
    if not evaluable:
        # This is a valid fail-closed skip, not a PRE-A process failure.  PRIMARY
        # remains the authoritative artifact and must be diagnosed/re-run first.
        print(f"PRE-A SKIPPED: {reason}", file=sys.stderr)
        return 0

    shadow_run_id = source_report.parent.name
    output_path = known.pre_a_output_root / shadow_run_id / "PRE_A_REPORT.md"
    sidecar_cmd = [
        sys.executable,
        "-m",
        "stock_agent.pre_a_sidecar",
        "--source-report",
        str(source_report),
        "--database",
        str(database),
        "--shadow-run-id",
        shadow_run_id,
        "--output",
        str(output_path),
        "--llm-provider",
        known.pre_a_llm_provider,
    ]
    if known.pre_a_reasoning_effort:
        sidecar_cmd.extend(["--reasoning-effort", known.pre_a_reasoning_effort])

    print(f"PRIMARY COMPLETE: deriving PRE-A from structured SQLite state for {shadow_run_id}")
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