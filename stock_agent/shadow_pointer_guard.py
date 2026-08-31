from __future__ import annotations

from typing import Any

from .store import SQLiteStore

_SENTINELS = {"unstarted", "not_started", "not-started"}
_GUARD_ATTR = "_shadow_pointer_guard_installed"


def _is_sentinel(value: object) -> bool:
    if value is None:
        return False
    return str(value).strip().casefold() in _SENTINELS


def _replacement_for_column(store: SQLiteStore, column: str) -> str | None:
    rows = store.connection.execute("PRAGMA table_info(shadow_runs)").fetchall()
    for row in rows:
        # cid, name, type, notnull, dflt_value, pk
        if str(row[1]) == column:
            return "" if bool(row[3]) else None
    return None


def _normalize_reserved_shadow_row(store: SQLiteStore, row: dict[str, Any]) -> dict[str, Any]:
    """Return a Shadow row whose run pointers can never expose legacy sentinels.

    The historical SQLite schema used non-empty placeholders such as
    ``unstarted``.  ``DailyShadowRunner`` intentionally treats any non-empty
    pointer as an already-created authoritative run id, so allowing a legacy
    placeholder through this boundary skips HUNT and later raises
    ``KeyError('unstarted')``.

    This guard operates *after* ``reserve_shadow_run`` has completed its INSERT
    or resume transaction.  Therefore it does not depend on the old table DDL,
    SQLite DEFAULT spelling, trigger behavior, or wrapper entry point.  It
    repairs only known historical sentinels and leaves every other run id
    untouched.
    """
    normalized = dict(row)
    updates: dict[str, str | None] = {}
    for column in ("hunt_run_id", "execution_run_id"):
        if _is_sentinel(normalized.get(column)):
            replacement = _replacement_for_column(store, column)
            updates[column] = replacement
            normalized[column] = replacement

    if not updates:
        return normalized

    shadow_run_id = normalized.get("shadow_run_id")
    if not shadow_run_id:
        raise RuntimeError("shadow pointer guard cannot repair row without shadow_run_id")

    assignments = ", ".join(f"{column}=?" for column in updates)
    values = [updates[column] for column in updates]
    with store.transaction() as db:
        db.execute(
            f"UPDATE shadow_runs SET {assignments} WHERE shadow_run_id=?",
            (*values, shadow_run_id),
        )
    return normalized


def install_shadow_pointer_guard() -> None:
    """Install one process-wide defense at the authoritative store boundary."""
    current = SQLiteStore.reserve_shadow_run
    if getattr(current, _GUARD_ATTR, False):
        return

    original = current

    def guarded_reserve_shadow_run(self: SQLiteStore, *args: Any, **kwargs: Any) -> dict[str, Any]:
        row = original(self, *args, **kwargs)
        return _normalize_reserved_shadow_row(self, row)

    setattr(guarded_reserve_shadow_run, _GUARD_ATTR, True)
    setattr(guarded_reserve_shadow_run, "_shadow_pointer_guard_original", original)
    SQLiteStore.reserve_shadow_run = guarded_reserve_shadow_run  # type: ignore[method-assign]
