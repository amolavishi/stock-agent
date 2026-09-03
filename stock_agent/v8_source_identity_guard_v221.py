"""Idempotent exact-source identity guard for V8.4 MAIN.

Legacy preparation helpers historically rewrite ``coach.V8_SCANNERS`` with
older source SHAs.  A one-shot ``_PREPARED`` flag in the V8.4 source-lock
bridge cannot protect against a later legacy prepare call.  This guard makes
source identity a reassertable invariant without adding candidate, grade,
PRE-A, execution, sizing, or broker authority.
"""
from __future__ import annotations

from typing import Any

from . import v8_main_discovery_coach as coach
from . import v8_main_discovery_integrity as integrity
from . import v8_main_source_fidelity as source_fidelity

V8_SOURCE_IDENTITY_GUARD_VERSION = "V8_SOURCE_IDENTITY_GUARD_V2.2.1"
_INSTALLED = False
_BASE_LEGACY_PREPARE = integrity.prepare_v8_main_discovery_integrity


def reassert_v8_4_source_identity() -> None:
    """Always restore exact V8.4 manifest identity; never rely on a one-shot flag."""
    for sid, entry in source_fidelity._scanner_entries().items():
        coach.V8_SCANNERS[sid]["sha256"] = str(entry["sha256"])
        coach.V8_SCANNERS[sid]["source_file"] = f"prompts/v8_4/{entry['path']}"
        coach.V8_SCANNERS[sid]["source_package_version"] = source_fidelity.V8_4_PACKAGE_VERSION


def prepare_v8_4_source_lock_idempotent() -> None:
    # Resolve/validate the manifest through the canonical source module, then
    # reassert every identity even when its historical _PREPARED flag is true.
    source_fidelity._load_lock()
    reassert_v8_4_source_identity()
    source_fidelity._PREPARED = True


def prepare_legacy_then_reassert() -> None:
    """Compatibility wrapper: legacy prepare may run, but cannot own final identity."""
    _BASE_LEGACY_PREPARE()
    prepare_v8_4_source_lock_idempotent()


def install_v8_source_identity_guard_v221() -> None:
    global _INSTALLED
    if _INSTALLED:
        # Reassert on repeated install as a defensive invariant.
        prepare_v8_4_source_lock_idempotent()
        return
    source_fidelity.prepare_v8_4_source_lock = prepare_v8_4_source_lock_idempotent  # type: ignore[assignment]
    integrity.prepare_v8_main_discovery_integrity = prepare_legacy_then_reassert  # type: ignore[assignment]
    prepare_v8_4_source_lock_idempotent()
    _INSTALLED = True


def source_identity_guard_status() -> dict[str, Any]:
    expected = source_fidelity._scanner_entries()
    mismatches = [
        sid for sid, entry in expected.items()
        if str(coach.V8_SCANNERS.get(sid, {}).get("sha256") or "") != str(entry.get("sha256") or "")
    ]
    return {
        "version": V8_SOURCE_IDENTITY_GUARD_VERSION,
        "scanner_count": len(expected),
        "mismatches": sorted(mismatches),
        "complete": not mismatches,
        "grade_authority": False,
        "execution_authority": False,
    }
