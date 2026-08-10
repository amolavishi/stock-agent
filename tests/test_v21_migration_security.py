from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stock_agent.database import Database
from stock_agent.migration import SafeMigrationManager
from stock_agent.secret_scan import scan_tree


class MigrationTests(unittest.TestCase):
    def test_shadow_migration_preserves_counts_checksums_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite"
            db = Database(str(source)); db.init(); db.initialize_paper_account(12345)
            receipt = SafeMigrationManager.prepare_shadow(str(source), str(Path(directory) / "work"))
            self.assertEqual(receipt.integrity_check, "ok")
            self.assertEqual(receipt.foreign_key_violations, 0)
            self.assertTrue(receipt.critical_checksums_match)
            self.assertGreaterEqual(receipt.target_schema_version, receipt.source_schema_version)
            self.assertEqual(receipt.source_counts["paper_accounts"],
                             receipt.shadow_counts["paper_accounts"])
            SafeMigrationManager.activate_shadow(receipt, allow_replacement=True)
            migrated = Database(str(source))
            with migrated.connect() as connection:
                self.assertEqual(connection.execute(
                    "SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                    Database.SCHEMA_VERSION)

    def test_shadow_activation_requires_explicit_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite"
            Database(str(source)).init()
            receipt = SafeMigrationManager.prepare_shadow(str(source), str(Path(directory) / "work"))
            with self.assertRaises(PermissionError):
                SafeMigrationManager.activate_shadow(receipt)


class SecretScanTests(unittest.TestCase):
    def test_secret_scan_detects_token_without_returning_secret_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.txt"
            secret = "Bearer " + "A" * 32
            path.write_text(secret, encoding="utf-8")
            findings = scan_tree(directory)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].kind, "BEARER_TOKEN")
            self.assertNotIn("A" * 32, repr(findings))

    def test_env_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".env").write_text("api_key=" + "B" * 40, encoding="utf-8")
            self.assertEqual(scan_tree(directory), [])

    def test_long_sec_html_identifier_is_not_a_discord_token(self):
        with tempfile.TemporaryDirectory() as directory:
            value = "A" * 150 + "." + "B" * 52 + "." + "C" * 30
            (Path(directory) / "filing.html").write_text(value, encoding="utf-8")
            self.assertEqual(scan_tree(directory), [])


if __name__ == "__main__":
    unittest.main()
