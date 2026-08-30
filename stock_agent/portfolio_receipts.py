"""Deterministic portfolio receipts owned by Python.

A PositionSnapshotReceiptV2 is emitted only for an actually existing position.
The receipt contains exactly the fields allowed by the Prompt Library schema;
run/evidence lineage is carried separately by the SQLite StageResult that
persists this receipt.
"""
from __future__ import annotations

from .models import PortfolioSnapshot, PositionSnapshot, canonical_hash


def make_position_snapshot_receipt(
    position: PositionSnapshot | None,
    portfolio_snapshot: PortfolioSnapshot,
) -> dict[str, object] | None:
    if position is None or not position.position_exists or int(position.shares) <= 0:
        return None
    base: dict[str, object] = {
        "receipt_type": "PositionSnapshotReceiptV2",
        "subject_id": str(position.subject_id),
        "position_exists": True,
        "snapshot_hash": str(position.snapshot_hash),
        "as_of": str(position.as_of or portfolio_snapshot.as_of),
    }
    return {**base, "receipt_hash": canonical_hash(base)}

