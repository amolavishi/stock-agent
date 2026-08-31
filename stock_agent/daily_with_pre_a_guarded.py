"""Hardened PRIMARY Shadow -> PRE-A chain for legacy local Shadow databases.

This module is intentionally a compatibility wrapper around
``stock_agent.daily_with_pre_a``.  Before PRIMARY starts it installs a narrow
SQLite AFTER INSERT guard on ``shadow_runs`` and normalizes any persisted
placeholder pointers such as ``unstarted``.  This protects old local databases
whose historical table DDL can recreate the placeholder on every new Shadow
run even after a one-time cleanup.

The guard does not alter Research Grade, execution logic, portfolio state,
broker state, or PRE-A authority.  It only ensures that placeholder strings are
never interpreted as real ``runs.run_id`` values.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from .daily_with_pre_a import _option_value, main as chained_main

_SENTINELS = ("not-started", "not_started", "unstarted")
_TRIGGER = "shadow_runs_persistent_sentinel_guard"


def install_shadow_pointer_guard(database: Path) -> bool:
    """Normalize legacy pointers and install a synchronous INSERT guard.

    Returns ``True`` when an existing ``shadow_runs`` table was inspected and
    guarded.  A missing database/table is not an error because PRIMARY may be
    starting with a brand-new database; the current schema already uses NULL
    pointers.
    """
    database = database.expanduser()
    if not database.exists() or not database.is_file():
        return False

    connection = sqlite3.connect(str(database))
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shadow_runs'"
        ).fetchone()
        if table is None:
            return False

        columns = {
            str(row[1]): {"notnull": bool(row[3])}
            for row in connection.execute("PRAGMA table_info(shadow_runs)").fetchall()
        }
        if "hunt_run_id" not in columns or "execution_run_id" not in columns:
            return False

        hunt_replacement = "''" if columns["hunt_run_id"]["notnull"] else "NULL"
        execution_replacement = "''" if columns["execution_run_id"]["notnull"] else "NULL"
        sentinels_sql = ",".join("'" + item.replace("'", "''") + "'" for item in _SENTINELS)
        placeholders = ",".join("?" for _ in _SENTINELS)

        with connection:
            for name, replacement in (
                ("hunt_run_id", "" if columns["hunt_run_id"]["notnull"] else None),
                ("execution_run_id", "" if columns["execution_run_id"]["notnull"] else None),
            ):
                connection.execute(
                    f"UPDATE shadow_runs SET {name}=? "
                    f"WHERE lower(trim(COALESCE({name}, ''))) IN ({placeholders})",
                    (replacement, *_SENTINELS),
                )

            # Always install the guard for an existing historical table.  This
            # is deliberately stronger than attempting to infer every legacy
            # SQLite DEFAULT spelling.  On a current nullable schema it is a
            # no-op unless a sentinel is actually inserted.
            connection.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER}")
            connection.execute(
                f"""
                CREATE TRIGGER {_TRIGGER}
                AFTER INSERT ON shadow_runs
                FOR EACH ROW
                WHEN lower(trim(COALESCE(NEW.hunt_run_id, ''))) IN ({sentinels_sql})
                  OR lower(trim(COALESCE(NEW.execution_run_id, ''))) IN ({sentinels_sql})
                BEGIN
                    UPDATE shadow_runs
                    SET hunt_run_id = CASE
                            WHEN lower(trim(COALESCE(NEW.hunt_run_id, ''))) IN ({sentinels_sql})
                                THEN {hunt_replacement}
                            ELSE NEW.hunt_run_id
                        END,
                        execution_run_id = CASE
                            WHEN lower(trim(COALESCE(NEW.execution_run_id, ''))) IN ({sentinels_sql})
                                THEN {execution_replacement}
                            ELSE NEW.execution_run_id
                        END
                    WHERE shadow_run_id = NEW.shadow_run_id;
                END
                """
            )
        return True
    finally:
        connection.close()


def main() -> int:
    argv = list(sys.argv[1:])
    database = Path(_option_value(argv, "--database", "stock_agent.db"))
    try:
        guarded = install_shadow_pointer_guard(database)
    except (OSError, sqlite3.DatabaseError) as exc:
        print(f"PRIMARY/PRE-A CHAIN ABORTED: failed to guard Shadow DB: {exc}", file=sys.stderr)
        return 6
    if guarded:
        print(f"SHADOW DB GUARDED: persistent sentinel protection active for {database}")
    return chained_main()


if __name__ == "__main__":
    raise SystemExit(main())
