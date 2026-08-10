from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .database import Database


@dataclass(frozen=True)
class MigrationReceipt:
    source_path: str
    backup_path: str
    shadow_path: str
    source_schema_version: int
    target_schema_version: int
    integrity_check: str
    foreign_key_violations: int
    critical_checksums_match: bool
    source_counts: dict[str, int]
    shadow_counts: dict[str, int]
    source_checksums: dict[str, str]


class SafeMigrationManager:
    CRITICAL_COLUMNS = {
        "analysis_runs": ("run_id", "ticker", "status", "final_decision"),
        "company_states": ("ticker", "updated_at", "payload_json"),
        "paper_accounts": ("account_id", "cash", "reserved_cash", "realized_pnl"),
        "paper_transactions": ("id", "run_id", "ticker", "side", "quantity", "price"),
        "portfolio_positions": ("ticker", "account_id", "quantity", "average_price"),
    }

    @staticmethod
    def _backup(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)

    @staticmethod
    def _tables(connection: sqlite3.Connection) -> set[str]:
        return {str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}

    @classmethod
    def _snapshot(cls, path: Path) -> tuple[dict[str, int], dict[str, str]]:
        counts: dict[str, int] = {}
        checksums: dict[str, str] = {}
        with closing(sqlite3.connect(path)) as connection:
            tables = cls._tables(connection)
            for table in sorted(tables):
                counts[table] = int(connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table, columns in cls.CRITICAL_COLUMNS.items():
                if table not in tables:
                    continue
                existing = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                selected = [column for column in columns if column in existing]
                if not selected:
                    continue
                sql_columns = ",".join(f'"{column}"' for column in selected)
                rows = connection.execute(
                    f'SELECT {sql_columns} FROM "{table}" ORDER BY {sql_columns}').fetchall()
                raw = json.dumps(rows, ensure_ascii=False, default=str, separators=(",", ":"))
                checksums[table] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return counts, checksums

    @classmethod
    def prepare_shadow(cls, source_path: str, work_dir: str) -> MigrationReceipt:
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        root = Path(work_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = root / f"{source.stem}.{stamp}.backup.sqlite"
        shadow = root / f"{source.stem}.{stamp}.shadow.sqlite"
        with closing(sqlite3.connect(source)) as connection:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise RuntimeError(f"source integrity_check failed: {integrity}")
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            source_version = int(row[0] or 0) if row else 0
        source_counts, source_checksums = cls._snapshot(source)
        cls._backup(source, backup)
        cls._backup(source, shadow)
        Database(str(shadow)).init()
        shadow_counts, shadow_checksums = cls._snapshot(shadow)
        with closing(sqlite3.connect(shadow)) as connection:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            target_version = int(row[0] or 0) if row else 0
        preserved = all(shadow_counts.get(table, 0) >= count
                        for table, count in source_counts.items())
        checksums_match = all(shadow_checksums.get(table) == digest
                              for table, digest in source_checksums.items())
        if integrity != "ok" or foreign_keys or not preserved or not checksums_match:
            raise RuntimeError("shadow migration validation failed")
        return MigrationReceipt(str(source), str(backup), str(shadow), source_version,
            target_version, integrity, len(foreign_keys), checksums_match,
            source_counts, shadow_counts, source_checksums)

    @classmethod
    def activate_shadow(cls, receipt: MigrationReceipt, *, allow_replacement: bool = False) -> None:
        if not allow_replacement:
            raise PermissionError("shadow activation requires explicit allow_replacement=True")
        source = Path(receipt.source_path).resolve()
        shadow = Path(receipt.shadow_path).resolve()
        backup = Path(receipt.backup_path).resolve()
        if not source.is_file() or not shadow.is_file() or not backup.is_file():
            raise FileNotFoundError("source, validated shadow, and backup must all exist")
        current_counts, current_checksums = cls._snapshot(source)
        if current_counts != receipt.source_counts or current_checksums != receipt.source_checksums:
            raise RuntimeError("source database changed after shadow validation")
        with closing(sqlite3.connect(shadow)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("shadow integrity_check failed before activation")
            if list(connection.execute("PRAGMA foreign_key_check")):
                raise RuntimeError("shadow foreign_key_check failed before activation")
        os.replace(shadow, source)
        try:
            with closing(sqlite3.connect(source)) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
                version = int(connection.execute(
                    "SELECT MAX(version) FROM schema_migrations").fetchone()[0] or 0)
            if integrity != "ok" or foreign_keys or version != receipt.target_schema_version:
                raise RuntimeError("post-activation database validation failed")
        except Exception:
            cls._backup(backup, source)
            raise

    @staticmethod
    def write_receipt(receipt: MigrationReceipt, path: str) -> None:
        Path(path).write_text(json.dumps(asdict(receipt), ensure_ascii=False, indent=2),
                              encoding="utf-8")
