from __future__ import annotations

import unittest

from stock_agent.models import PortfolioSnapshot, PositionSnapshot, canonical_hash
from stock_agent.portfolio_receipts import make_position_snapshot_receipt


class PositionSnapshotReceiptTests(unittest.TestCase):
    def test_existing_position_emits_schema_exact_receipt(self):
        position = PositionSnapshot("SEC1", True, 5, 9.5, "2026-08-20T17:00:00Z", "a" * 64)
        snapshot = PortfolioSnapshot("P1", "2026-08-20T17:00:00Z", 100.0, 150.0, (position,), True, "b" * 64)
        receipt = make_position_snapshot_receipt(position, snapshot)
        self.assertIsNotNone(receipt)
        assert receipt is not None
        self.assertEqual(set(receipt), {"receipt_type", "subject_id", "position_exists", "snapshot_hash", "as_of", "receipt_hash"})
        self.assertEqual(receipt["receipt_type"], "PositionSnapshotReceiptV2")
        self.assertEqual(receipt["subject_id"], "SEC1")
        self.assertIs(receipt["position_exists"], True)
        base = {key: receipt[key] for key in ("receipt_type", "subject_id", "position_exists", "snapshot_hash", "as_of")}
        self.assertEqual(receipt["receipt_hash"], canonical_hash(base))

    def test_absent_position_emits_no_receipt(self):
        snapshot = PortfolioSnapshot("P1", "2026-08-20T17:00:00Z", 100.0, 100.0, tuple(), True, "b" * 64)
        self.assertIsNone(make_position_snapshot_receipt(None, snapshot))

    def test_zero_share_position_cannot_forge_existing_receipt(self):
        position = PositionSnapshot("SEC1", False, 0, 9.5, "2026-08-20T17:00:00Z", "a" * 64)
        snapshot = PortfolioSnapshot("P1", "2026-08-20T17:00:00Z", 100.0, 100.0, (position,), True, "b" * 64)
        self.assertIsNone(make_position_snapshot_receipt(position, snapshot))


if __name__ == "__main__":
    unittest.main()

