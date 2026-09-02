from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

from jsonschema import ValidationError, validate

from stock_agent import v8_evidence_origin_v19 as origin
from stock_agent import v8_main_discovery_integrity as integrity
from stock_agent.v8_pre_live_integrity_v20 import (
    MIN_OPERATIONAL_PROBE,
    SCANNER_OUTPUT_CONTRACT_VERSION,
    _contract_complete_v20,
    _finalize_atomic_audit_v20,
    _provider_exhaustion_v20,
    _sentinel_sample_v20,
    _system_rounds_v20,
    _technical_receipt_usable,
    _upsert_secondary_v20,
)
from stock_agent.v8_pre_live_integrity_v201 import (
    V8_PRE_LIVE_INTEGRITY_PATCH_VERSION,
    default_scanner_v201,
    scanner_schema_v201,
)
from stock_agent.v8_pre_live_integrity_v202 import (
    V8_PRE_LIVE_EVIDENCE_ORIGIN_PATCH_VERSION,
    source_lineage_v202,
)


class FunnelStore:
    def __init__(self, rows):
        self.rows = list(rows)

    def list_funnel(self, run_id):
        return list(self.rows)


class SecondaryStore:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE discovery_secondary_queue ("
            "security_id TEXT PRIMARY KEY, originating_run_id TEXT NOT NULL, scanner_ids_json TEXT NOT NULL, "
            "research_value TEXT NOT NULL, missing_evidence_json TEXT NOT NULL, verification_path_json TEXT NOT NULL, "
            "expected_resolution TEXT NOT NULL, recheck_trigger TEXT NOT NULL, expiry TEXT NOT NULL, status TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )

    def insert(self, *, sid="ABC", status="OPEN", expiry: str, payload=None):
        payload = dict(payload or {})
        payload.setdefault("first_seen_at", "2026-08-01T00:00:00Z")
        payload.setdefault("expiry", expiry)
        self.connection.execute(
            "INSERT INTO discovery_secondary_queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, "OLD_RUN", '["02"]', "HIGH", '["missing"]', '["verify"]',
                "WITHIN_1_8W_OR_NEXT_MATERIAL_DISCLOSURE",
                "MISSING_EVIDENCE_RESOLVED_OR_NEW_MATERIAL_EVENT",
                expiry, status, json.dumps(payload, sort_keys=True), "2026-08-01T00:00:00Z",
            ),
        )


class V8PreLiveIntegrityTests(unittest.TestCase):
    @staticmethod
    def secondary_item(**updates):
        value = {
            "security_id": "ABC",
            "research_value": "HIGH",
            "unknowns": ["decision critical fact"],
            "verification_questions": ["verify decision critical fact"],
            "strategy_evidence": [
                {"dimension": "economic_change", "status": "UNKNOWN", "summary": "pending", "evidence_ids": ["E1"]}
            ],
            "recheck_trigger_fired": False,
            "recheck_trigger_evidence_ids": [],
        }
        value.update(updates)
        return value

    def test_secondary_rediscovery_does_not_extend_open_expiry(self):
        store = SecondaryStore()
        expiry = "2099-01-01T00:00:00Z"
        store.insert(expiry=expiry)
        _upsert_secondary_v20(store, "NEW_RUN", self.secondary_item(), ["02", "11"])
        row = store.connection.execute(
            "SELECT expiry,status,payload_json FROM discovery_secondary_queue WHERE security_id='ABC'"
        ).fetchone()
        self.assertEqual(row["expiry"], expiry)
        self.assertEqual(row["status"], "OPEN")
        payload = json.loads(row["payload_json"])
        self.assertEqual(payload["expiry"], expiry)
        self.assertEqual(payload["first_seen_at"], "2026-08-01T00:00:00Z")

    def test_expired_secondary_cannot_silently_reopen_on_rediscovery(self):
        store = SecondaryStore()
        expiry = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        store.insert(expiry=expiry)
        _upsert_secondary_v20(store, "NEW_RUN", self.secondary_item(), ["02"])
        row = store.connection.execute(
            "SELECT expiry,status FROM discovery_secondary_queue WHERE security_id='ABC'"
        ).fetchone()
        self.assertEqual(row["status"], "EXPIRED_WATCH")
        self.assertEqual(row["expiry"], expiry)

    def test_expired_secondary_reopens_only_with_explicit_trigger_evidence(self):
        store = SecondaryStore()
        expiry = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        store.insert(expiry=expiry, status="EXPIRED_WATCH")
        item = self.secondary_item(recheck_trigger_fired=True, recheck_trigger_evidence_ids=["E-NEW"])
        _upsert_secondary_v20(store, "NEW_RUN", item, ["11"])
        row = store.connection.execute(
            "SELECT expiry,status,payload_json FROM discovery_secondary_queue WHERE security_id='ABC'"
        ).fetchone()
        self.assertEqual(row["status"], "OPEN")
        self.assertGreater(row["expiry"], expiry)
        payload = json.loads(row["payload_json"])
        self.assertTrue(payload["recheck_trigger_fired"])
        self.assertEqual(payload["recheck_trigger_evidence_ids"], ["E-NEW"])

    def test_unproven_bundle_children_collapse_to_one_origin_even_if_text_or_class_differs(self):
        a, hash_a = source_lineage_v202(
            {"source_class": "NEWS", "title": "A", "content": "first story"}, "artifact-bundle-1"
        )
        b, hash_b = source_lineage_v202(
            {"source_class": "IR", "title": "B", "content": "different reprint"}, "artifact-bundle-1"
        )
        self.assertEqual(a, b)
        self.assertNotEqual(hash_a, hash_b)

    def test_explicit_origin_artifacts_remain_distinct(self):
        a, _ = source_lineage_v202(
            {"source_class": "NEWS", "origin_artifact_id": "publisher-A", "content": "same"}, "bundle"
        )
        b, _ = source_lineage_v202(
            {"source_class": "NEWS", "origin_artifact_id": "publisher-B", "content": "same"}, "bundle"
        )
        self.assertNotEqual(a, b)

    @staticmethod
    def atomic_draft(groups=("G1", "G2")):
        return {
            "status": "COMPLETE",
            "atomic_claims": [
                {
                    "claim_id": "C1", "statement": "claim 1", "verification_status": "VERIFIED",
                    "economic_event_id": "EVENT-1", "independent_evidence_group": groups[0],
                    "evidence_ids": ["E1"], "independent_origin_ids": [],
                },
                {
                    "claim_id": "C2", "statement": "claim 2", "verification_status": "VERIFIED",
                    "economic_event_id": "EVENT-2", "independent_evidence_group": groups[1],
                    "evidence_ids": ["E2"], "independent_origin_ids": [],
                },
            ],
            "evidence_independence": "PASS",
            "duplicate_economic_event_ids": [],
            "critical_unknowns": [],
            "value_realization_bridge_1_8w": {"status": "ROBUST", "summary": "bridge", "evidence_ids": ["E1"]},
            "probability_provenance": "DATA_BACKED",
            "grade_authority": False,
        }

    def test_two_verified_claims_one_python_origin_cannot_pass_independence(self):
        one_origin = "ORIGIN-" + "a" * 24
        token = origin._ORIGIN_CONTEXT.set({"E1": one_origin, "E2": one_origin})
        try:
            draft = self.atomic_draft()
            for claim in draft["atomic_claims"]:
                claim["independent_origin_ids"] = [one_origin]
            result = _finalize_atomic_audit_v20(draft, ["E1", "E2"])
        finally:
            origin._ORIGIN_CONTEXT.reset(token)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["evidence_independence"], "FAIL")
        self.assertIn("SINGLE_ORIGIN_CANNOT_PROVE_MULTI_CLAIM_INDEPENDENCE", result["validation_failures"])

    def test_one_origin_cannot_be_split_into_multiple_independent_groups(self):
        one_origin = "ORIGIN-" + "b" * 24
        token = origin._ORIGIN_CONTEXT.set({"E1": one_origin, "E2": one_origin})
        try:
            draft = self.atomic_draft(groups=("GROUP-A", "GROUP-B"))
            for claim in draft["atomic_claims"]:
                claim["independent_origin_ids"] = [one_origin]
            result = _finalize_atomic_audit_v20(draft, ["E1", "E2"])
        finally:
            origin._ORIGIN_CONTEXT.reset(token)
        self.assertTrue(any(code.startswith("FALSE_INDEPENDENT_GROUP_SPLIT:") for code in result["validation_failures"]))
        self.assertEqual(result["evidence_independence"], "FAIL")

    def test_small_fully_probed_subset_is_not_source_exhaustion(self):
        store = FunnelStore([
            {"funnel_stage": "RAW_UNIVERSE", "count": 150},
            {"funnel_stage": "ADV_PROBED", "count": 150},
            {"funnel_stage": "ADV_NOT_EVALUATED", "count": 0},
        ])
        exhausted, details = _provider_exhaustion_v20(store, "RUN", 150)
        self.assertFalse(exhausted)
        self.assertFalse(details["small_fully_probed_subset_is_source_exhaustion"])

    def test_explicit_1000_name_operational_probe_can_document_exhaustion_boundary(self):
        store = FunnelStore([
            {"funnel_stage": "RAW_UNIVERSE", "count": 3000},
            {"funnel_stage": "ADV_PROBED", "count": MIN_OPERATIONAL_PROBE},
            {"funnel_stage": "ADV_NOT_EVALUATED", "count": 2000},
        ])
        exhausted, details = _provider_exhaustion_v20(store, "RUN", 600)
        self.assertTrue(exhausted)
        self.assertTrue(details["explicit_operational_ceiling"])

    def test_cross_scanner_duplicate_evidence_counts_once_in_system_round(self):
        rounds = []
        for scanner_id in integrity.SCANNER_REQUIRED_DIMENSIONS:
            rounds.append({
                "round_id": f"{scanner_id}-R001", "scanner_id": scanner_id,
                "new_unique_tickers": 75,
                "new_signal_security_ids": ["ABC"],
                "new_secondary_security_ids": ["ABC"],
                "new_high_research_value_security_ids": ["ABC"],
                "new_deep_dive_security_ids": [],
                "new_independent_evidence_ids": ["E-SAME"],
            })
        system = _system_rounds_v20(rounds)
        self.assertEqual(system[0]["new_signal"], 1)
        self.assertEqual(system[0]["new_secondary"], 1)
        self.assertEqual(system[0]["new_independent_evidence"], 1)
        self.assertEqual(system[0]["new_independent_evidence_ids"], ["E-SAME"])

    def test_v201_schema_preserves_v12_scanner_specific_dimensions(self):
        value = default_scanner_v201("05", 0)
        value["strategy_contract"]["dimensions_evaluated"] = ["generic_signal"]
        with self.assertRaises(ValidationError):
            validate(value, scanner_schema_v201())

    def test_v201_schema_requires_coverage_ledger(self):
        value = default_scanner_v201("05", 0)
        value.pop("coverage_ledger")
        with self.assertRaises(ValidationError):
            validate(value, scanner_schema_v201())

    def test_contract_complete_rejects_coverage_count_mismatch(self):
        value = default_scanner_v201("02", 2)
        value["coverage_ledger"] = [{
            "security_id": "AAA", "disposition": "NO_SIGNAL", "failure_class": "NONE",
            "signal_strength": "NONE", "research_value": "LOW", "cheap_hard_gate_status": "PASS",
            "evidence_ids": [], "rationale": "no scanner signal",
        }]
        complete, failures = _contract_complete_v20("02", value, 2)
        self.assertFalse(complete)
        self.assertTrue(any(code.startswith("COVERAGE_LEDGER_COUNT:") for code in failures))

    def test_contract_complete_rejects_duplicate_coverage_ids(self):
        value = default_scanner_v201("02", 2)
        row = {
            "security_id": "AAA", "disposition": "NO_SIGNAL", "failure_class": "NONE",
            "signal_strength": "NONE", "research_value": "LOW", "cheap_hard_gate_status": "PASS",
            "evidence_ids": [], "rationale": "no scanner signal",
        }
        value["coverage_ledger"] = [dict(row), dict(row)]
        complete, failures = _contract_complete_v20("02", value, 2)
        self.assertFalse(complete)
        self.assertIn("COVERAGE_LEDGER_IDS_DUPLICATE_OR_MISSING", failures)

    def test_sentinel_can_sample_no_signal_name_not_present_in_candidates(self):
        result = default_scanner_v201("02", 1)
        result["coverage_ledger"] = [{
            "security_id": "OMITTED", "disposition": "NO_SIGNAL", "failure_class": "NONE",
            "signal_strength": "NONE", "research_value": "LOW", "cheap_hard_gate_status": "PASS",
            "evidence_ids": [], "rationale": "no signal",
        }]
        sample = _sentinel_sample_v20([result])
        self.assertEqual(len(sample), 1)
        self.assertEqual(sample[0]["security_id"], "OMITTED")
        self.assertEqual(sample[0]["coverage_disposition"], "NO_SIGNAL")

    def test_technical_key_presence_is_not_sufficient(self):
        self.assertFalse(_technical_receipt_usable({}))
        self.assertFalse(_technical_receipt_usable({"evaluation_status": "NOT_EVALUATED_PROVIDER_FAILURE"}))
        self.assertFalse(_technical_receipt_usable({"status": "INCOMPLETE", "rsi": None}))
        self.assertTrue(_technical_receipt_usable({"evaluation_status": "PASS", "rsi": 48.2}))

    def test_production_composition_installs_pre_live_layer_without_weak_parallel_runtime(self):
        code = """
import json
from stock_agent.production import production_composition
c = production_composition()
print(json.dumps({
    "v": c.get("v8_pre_live_integrity_version"),
    "p": c.get("v8_pre_live_integrity_patch_version"),
    "o": c.get("v8_main_scanner_output_contract_version"),
    "weak": c.get("discovery_recall_lite_runtime_installed"),
}))
"""
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        data = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(data["v"], "V8_PRE_LIVE_INTEGRITY_V2.0")
        self.assertEqual(data["p"], V8_PRE_LIVE_INTEGRITY_PATCH_VERSION)
        self.assertEqual(data["o"], SCANNER_OUTPUT_CONTRACT_VERSION)
        self.assertFalse(data["weak"])
        self.assertEqual(V8_PRE_LIVE_EVIDENCE_ORIGIN_PATCH_VERSION, "V8_PRE_LIVE_EVIDENCE_ORIGIN_V2.0.2")


if __name__ == "__main__":
    unittest.main()
