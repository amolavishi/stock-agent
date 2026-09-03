"""Idempotent exact-source identity guard for V8.4 MAIN.

Legacy preparation helpers historically rewrite ``coach.V8_SCANNERS`` with
older source SHAs. A one-shot ``_PREPARED`` flag or monkeypatching a module
attribute is insufficient because callers may have imported the old function
object before production bootstrap. This guard therefore establishes two
invariants at installation time:

1. legacy scanner-schema preparation is marked complete, so every previously
   captured legacy prepare function becomes a no-op rather than a source-
   identity writer; and
2. canonical V8.4 identity is reasserted directly from the manifest on every
   guard/install/compatibility call, independently of historical one-shot flags.

The guard owns source identity only. It adds no candidate, Research Grade,
PRE-A, execution, sizing, or broker authority.
"""
from __future__ import annotations

from typing import Any

from . import v8_main_discovery_coach as coach
from . import v8_main_discovery_integrity as integrity
from . import v8_main_source_fidelity as source_fidelity

V8_SOURCE_IDENTITY_GUARD_VERSION = "V8_SOURCE_IDENTITY_GUARD_V2.2.2"
_INSTALLED = False
_BASE_LEGACY_PREPARE = integrity.prepare_v8_main_discovery_integrity


def reassert_v8_4_source_identity() -> None:
    """Always restore exact V8.4 manifest identity; never rely on a one-shot flag."""
    for sid, entry in source_fidelity._scanner_entries().items():
        coach.V8_SCANNERS[sid]["sha256"] = str(entry["sha256"])
        coach.V8_SCANNERS[sid]["source_file"] = f"prompts/v8_4/{entry['path']}"
        coach.V8_SCANNERS[sid]["source_package_version"] = source_fidelity.V8_4_PACKAGE_VERSION


def prepare_v8_4_source_lock_idempotent() -> None:
    """Validate the canonical manifest and reassert every identity every time."""
    source_fidelity._load_lock()
    reassert_v8_4_source_identity()
    source_fidelity._PREPARED = True


def _retire_legacy_source_identity_authority() -> None:
    """Make already-imported legacy prepare functions source-identity inert.

    ``prepare_v8_main_discovery_integrity`` historically contains a Scanner-08
    SHA repair. A caller that imported that function before this guard would
    otherwise bypass the module-attribute monkeypatch. The captured function
    consults ``integrity._PREPARED`` before any mutation, so marking schema
    preparation complete retires its obsolete source-identity authority for all
    existing references as well as future module lookups.
    """
    if not integrity._PREPARED:
        # Run once so the legacy schema/default-payload preparation still occurs.
        # Any obsolete identity written here is immediately overwritten below.
        _BASE_LEGACY_PREPARE()
    integrity._PREPARED = True


def prepare_legacy_then_reassert() -> None:
    """Compatibility entry point: preserve schema prep, canonicalize identity."""
    _retire_legacy_source_identity_authority()
    prepare_v8_4_source_lock_idempotent()


def install_v8_source_identity_guard_v221() -> None:
    global _INSTALLED

    # Retire the obsolete writer before exposing patched module attributes.
    # This also protects function objects imported by value before bootstrap.
    _retire_legacy_source_identity_authority()

    source_fidelity.prepare_v8_4_source_lock = prepare_v8_4_source_lock_idempotent  # type: ignore[assignment]
    integrity.prepare_v8_main_discovery_integrity = prepare_legacy_then_reassert  # type: ignore[assignment]

    # Reassert unconditionally, including repeated installation. This is the
    # final source-identity state regardless of earlier import/call order.
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
        "legacy_source_identity_authority_retired": bool(integrity._PREPARED),
        "complete": not mismatches,
        "grade_authority": False,
        "execution_authority": False,
    }
