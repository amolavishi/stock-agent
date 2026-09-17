from __future__ import annotations

import unittest

from stock_agent import v8_main_discovery_coach as coach
from stock_agent import v8_main_discovery_integrity as integrity
from stock_agent import v8_main_source_fidelity as source_fidelity
from stock_agent.v8_source_identity_guard_v221 import (
    V8_SOURCE_IDENTITY_GUARD_VERSION,
    _BASE_LEGACY_PREPARE,
    install_v8_source_identity_guard_v221,
    prepare_v8_4_source_lock_idempotent,
    source_identity_guard_status,
)


class V8SourceIdentityImportOrderTests(unittest.TestCase):
    def _expected(self) -> dict[str, str]:
        return {
            sid: str(entry["sha256"])
            for sid, entry in source_fidelity._scanner_entries().items()
        }

    def assert_manifest_identity(self) -> None:
        expected = self._expected()
        self.assertEqual(set(coach.V8_SCANNERS), set(expected))
        for sid, sha in expected.items():
            self.assertEqual(coach.V8_SCANNERS[sid]["sha256"], sha, sid)

    def test_guard_is_import_order_deterministic_against_stale_legacy_reference(self):
        # Reproduce the historical failure mode: source lock has already been
        # marked prepared while the legacy integrity helper is still capable of
        # writing an obsolete Scanner-08 SHA through a function object captured
        # before bootstrap monkeypatching.
        integrity._PREPARED = False
        source_fidelity._PREPARED = True
        coach.V8_SCANNERS["08"]["sha256"] = "0" * 64

        install_v8_source_identity_guard_v221()
        self.assert_manifest_identity()

        # This is the original function object, not the module attribute that
        # the guard replaces. It must now be source-identity inert.
        _BASE_LEGACY_PREPARE()
        self.assert_manifest_identity()

        status = source_identity_guard_status()
        self.assertEqual(V8_SOURCE_IDENTITY_GUARD_VERSION, "V8_SOURCE_IDENTITY_GUARD_V2.2.2")
        self.assertTrue(status["legacy_source_identity_authority_retired"])
        self.assertTrue(status["complete"])
        self.assertEqual(status["mismatches"], [])

    def test_idempotent_source_lock_repairs_every_scanner_after_arbitrary_drift(self):
        expected = self._expected()
        for sid in coach.V8_SCANNERS:
            coach.V8_SCANNERS[sid]["sha256"] = (sid * 32)[:64].ljust(64, "0")
        source_fidelity._PREPARED = True

        prepare_v8_4_source_lock_idempotent()
        self.assert_manifest_identity()
        self.assertTrue(source_fidelity._PREPARED)
        self.assertEqual(len(expected), 13)


if __name__ == "__main__":
    unittest.main()
