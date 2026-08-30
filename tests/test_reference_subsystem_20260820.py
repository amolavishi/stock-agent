from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stock_agent.models import Evidence, RawArtifact, canonical_hash, utc_now
from stock_agent.references import (
    ReferenceBuilder,
    ReferenceContractError,
    ReferencePackCompiler,
    ReferenceRecord,
    ReferenceRequirement,
    ReferenceResolver,
)
from stock_agent.store import SQLiteStore


class ReferenceSubsystemTests(unittest.TestCase):
    @staticmethod
    def _persist_artifact(store: SQLiteStore, artifact_id: str) -> str:
        stamp = utc_now()
        payload = {"artifact_id": artifact_id, "content": "source-backed reference material"}
        artifact = RawArtifact(
            artifact_id,
            "test-source",
            "REFERENCE_SOURCE",
            "REFERENCE",
            stamp,
            payload,
            canonical_hash(payload),
            stamp,
            stamp,
        )
        store.save_raw_artifact(artifact)
        return f"raw-artifact:{artifact_id}"

    def test_generated_reference_is_source_backed_and_resolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(":memory:")
            builder = ReferenceBuilder(store, directory)
            receipt_z = self._persist_artifact(store, "artifact-z")
            receipt_a = self._persist_artifact(store, "artifact-a")
            record = builder.build(
                ReferenceRequirement("capital-structure-basics", "1", "GENERATED_REFERENCE"),
                {"title": "Capital structure", "body": "Stable reusable rule"},
                [receipt_z, receipt_a, receipt_a],
            )
            resolved = ReferenceResolver(store).resolve(
                ReferenceRequirement("capital-structure-basics", "1", "GENERATED_REFERENCE")
            )
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.source_receipts, tuple(sorted((receipt_a, receipt_z))))
            self.assertEqual(resolved.content_hash, canonical_hash(resolved.content))
            self.assertTrue((Path(directory) / resolved.obsidian_path).exists())
            store.close()

    def test_forged_reference_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(":memory:")
            builder = ReferenceBuilder(store, directory)
            for receipt in ("receipt:anything", "r:a", "raw-artifact:missing", "evidence:missing", "stage-result:missing"):
                with self.subTest(receipt=receipt):
                    with self.assertRaises(ReferenceContractError):
                        builder.build(
                            ReferenceRequirement("forged", "1", "GENERATED_REFERENCE"),
                            "body",
                            [receipt],
                        )
            store.close()

    def test_active_evidence_receipt_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(":memory:")
            stamp = utc_now()
            payload = {"content": "evidence source"}
            artifact = RawArtifact(
                "artifact-evidence",
                "test-source",
                "REFERENCE_SOURCE",
                "REFERENCE",
                stamp,
                payload,
                canonical_hash(payload),
                stamp,
                stamp,
            )
            store.save_raw_artifact(artifact)
            store.upsert_evidence(Evidence("E-REFERENCE", "REFERENCE", "test-source", stamp, 0, artifact.payload_hash, "RAW"))
            record = ReferenceBuilder(store, directory).build(
                ReferenceRequirement("evidence-backed", "1", "GENERATED_REFERENCE"),
                "body",
                ["evidence:E-REFERENCE"],
            )
            self.assertEqual(record.status, "ACTIVE")
            store.close()

    def test_dynamic_evidence_cannot_be_promoted_to_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(":memory:")
            builder = ReferenceBuilder(store, directory)
            receipt = self._persist_artifact(store, "artifact-dynamic")
            with self.assertRaises(ReferenceContractError):
                builder.build(
                    ReferenceRequirement("company-2026", "1", "GENERATED_REFERENCE"),
                    {"kind": "DYNAMIC_EVIDENCE", "security_id": "SEC1"},
                    [receipt],
                )
            store.close()

    def test_reference_requires_receipt_and_active_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(":memory:")
            builder = ReferenceBuilder(store, directory)
            with self.assertRaises(ReferenceContractError):
                builder.build(ReferenceRequirement("missing-receipt", "1", "GENERATED_REFERENCE"), "body", [])
            record = ReferenceRecord(
                "canonical", "1", "INACTIVE", canonical_hash("body"), "references/canonical.md",
                ("canonical-policy-receipt",), utc_now(), None, "CANONICAL_REFERENCE", "body",
            )
            store.upsert_reference(record)
            self.assertIsNone(ReferenceResolver(store).resolve(ReferenceRequirement("canonical", "1", "CANONICAL_REFERENCE")))
            store.close()

    def test_pack_compiler_has_stable_order_and_cache_key(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(":memory:")
            builder = ReferenceBuilder(store, directory)
            receipt_z = self._persist_artifact(store, "artifact-pack-z")
            receipt_a = self._persist_artifact(store, "artifact-pack-a")
            builder.build(ReferenceRequirement("z-ref", "1", "GENERATED_REFERENCE"), "Z", [receipt_z])
            builder.build(ReferenceRequirement("a-ref", "1", "GENERATED_REFERENCE"), "A", [receipt_a])
            resolver = ReferenceResolver(store)
            compiler = ReferencePackCompiler()
            requirements = [
                ReferenceRequirement("z-ref", "1", "GENERATED_REFERENCE"),
                ReferenceRequirement("a-ref", "1", "GENERATED_REFERENCE"),
            ]
            first = compiler.compile(requirements, resolver)
            second = compiler.compile(list(reversed(requirements)), resolver)
            self.assertEqual([entry.reference_id for entry in first.entries], ["a-ref", "z-ref"])
            self.assertEqual(first.prefix, second.prefix)
            self.assertEqual(first.content_hash, second.content_hash)
            self.assertEqual(first.cache_key, second.cache_key)
            store.close()


if __name__ == "__main__":
    unittest.main()

