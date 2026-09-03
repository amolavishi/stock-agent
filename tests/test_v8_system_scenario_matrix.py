from __future__ import annotations

import copy
import json
import unittest
from types import SimpleNamespace

from stock_agent import v8_next_certification as cert
from stock_agent.models import GateDecision, RunMode, canonical_hash
from stock_agent.pre_a_sidecar import PreASidecarError, validate_sidecar_payload
from stock_agent.store import SQLiteStore
from stock_agent.v8_main_scanner_failure_isolation import _isolated_round_payload
from stock_agent.v8_next_certification_v11 import install_v8_next_certification_v11
from stock_agent.v8_next_successor import V8_NEXT_POLICY_HASH, V8_NEXT_POLICY_VERSION, validate_v8_next_certification
from stock_agent.v8_primary import v8_blind_packet
from stock_agent.v8_system_semantics_v21 import (
    V8_SYSTEM_SEMANTICS_VERSION,
    candidate_conservation_v21,
    certification_terminal_state,
    validated_research_grade,
)
from tests.test_pre_a_sidecar import _bundle, _candidate, _payload
from tests.test_v8_next_certification_runtime import (
    robust_assumption_audit,
    robust_atomic_audit,
    robust_fd_bridge,
    robust_step18_draft,
)

install_v8_next_certification_v11()


def _packet(ticker: str = "SIM") -> dict:
    body = {"ticker": ticker, "analysis_as_of": "2026-09-03T00:00:00Z", "facts": {"revenue_delta": 0.25}}
    return {**body, "packet_hash": canonical_hash(body)}


def _grade_chain(target: str, ticker: str = "SIM") -> tuple[dict, dict]:
    draft = robust_step18_draft()
    if target == "B+":
        draft["grade_caps"] = [{
            "code": "SIMULATION_MAX_B_PLUS",
            "max_grade": "B+",
            "rationale": "one non-critical verification gate remains",
            "evidence_ids": ["E1"],
        }]
    elif target == "B":
        draft["score_components"] = {
            "catalyst_strength": 17.5,
            "time_immediacy": 10.0,
            "numeric_evidence": 10.0,
            "supply_demand": 7.0,
            "price_stage_fit": 10.0,
            "strategic_fit": 12.0,
            "expected_value": 7.0,
        }
    elif target == "EXCLUDE":
        draft["toxic_red_flag"] = True
    elif target != "A":
        raise ValueError(target)

    packet = _packet(ticker)
    assumption = robust_assumption_audit()
    atomic = robust_atomic_audit()
    fd = robust_fd_bridge()
    receipt = cert.finalize_certification(
        draft, assumption, atomic, fd, packet, 10.0, ["E1"],
        V8_NEXT_POLICY_VERSION, V8_NEXT_POLICY_HASH,
    )
    grade, failures = validate_v8_next_certification(receipt)
    if failures:
        raise AssertionError((target, grade, failures, receipt))
    validator = cert.research_validator(fd, atomic, packet, assumption, receipt, failures)
    if validator.get("route") != "PASS":
        raise AssertionError((target, validator))
    if grade != target:
        raise AssertionError((target, grade, receipt))
    return receipt, validator


def _record(store: SQLiteStore, run, stage: str, sid: str | None, value: dict, status: str = "SUCCEEDED") -> None:
    deps: list[str] = []
    store.record_stage_result(
        run.run_id, None, stage, sid, value, deps,
        store.dependency_hash(deps, run.rule_set.rule_set_hash, run.context_manifest_hash),
        store.current_evidence_epoch_for(deps), status=status,
    )


class V8SystemScenarioMatrixTests(unittest.TestCase):
    def test_s01_a_reaches_completed_pass_not_pre_a(self):
        receipt, validator = _grade_chain("A", "S01")
        self.assertEqual(validated_research_grade(receipt), "A")
        self.assertEqual(validator["route"], "PASS")
        self.assertEqual(
            certification_terminal_state("A", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False),
            ("PASS", "V8_CERTIFICATION_A"),
        )
        with self.assertRaisesRegex(PreASidecarError, "A/A-"):
            validate_sidecar_payload(
                _payload(_candidate(source_grade="A", promotion_readiness="PRE_A", a_trajectory="HIGH")),
                _bundle(source_grade="A", decision={"ticker": "ABC", "grade": "A"}),
            )

    def test_s02_b_plus_is_evaluated_next_stage_and_can_be_pre_a(self):
        receipt, validator = _grade_chain("B+", "S02")
        self.assertEqual(validated_research_grade(receipt), "B+")
        self.assertEqual(validator["route"], "PASS")
        self.assertEqual(
            certification_terminal_state("B+", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False),
            ("NEXT_STAGE", "V8_CERTIFICATION_B_PLUS_PRE_A"),
        )
        validate_sidecar_payload(_payload(), _bundle())

    def test_s03_b_is_completed_watch_not_not_evaluated(self):
        receipt, validator = _grade_chain("B", "S03")
        self.assertEqual(validated_research_grade(receipt), "B")
        self.assertEqual(validator["route"], "PASS")
        self.assertEqual(
            certification_terminal_state("B", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False),
            ("WATCH", "V8_CERTIFICATION_B_WATCH"),
        )
        with self.assertRaisesRegex(PreASidecarError, "B grade"):
            validate_sidecar_payload(
                _payload(_candidate(source_grade="B", promotion_readiness="PRE_A", a_trajectory="MEDIUM")),
                _bundle(source_grade="B", decision={"ticker": "ABC", "grade": "B"}),
            )

    def test_s04_exclude_is_completed_rejection_and_never_unknown(self):
        receipt, validator = _grade_chain("EXCLUDE", "S04")
        self.assertEqual(validated_research_grade(receipt), "EXCLUDE")
        self.assertEqual(validator["route"], "PASS")
        self.assertEqual(
            certification_terminal_state("EXCLUDE", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False),
            ("REJECT", "V8_CERTIFICATION_EXCLUDE"),
        )
        validate_sidecar_payload(
            _payload(_candidate(
                source_grade="EXCLUDE", promotion_readiness="NONE", a_trajectory="NONE",
                fundamental_direction="NEGATIVE", expectation_gap="ABSENT", price_lag="ABSENT", catalyst_window="OUTSIDE",
            )),
            _bundle(source_grade="EXCLUDE", decision={"ticker": "ABC", "grade": "EXCLUDE"}),
        )

    def test_s05_missing_certification_is_not_evaluated(self):
        self.assertEqual(
            certification_terminal_state(None, step20_route=None, expectation_gap_pass=True, has_evidence_debt=False),
            ("NOT_EVALUATED", "V8_CERTIFICATION_MISSING_OR_INVALID"),
        )

    def test_s06_step20_return_route_blocks_completed_grade(self):
        receipt, _ = _grade_chain("A", "S06")
        grade = validated_research_grade(receipt)
        self.assertEqual(
            certification_terminal_state(grade, step20_route="RETURN_TO_STEP17_5", expectation_gap_pass=True, has_evidence_debt=False),
            ("NOT_EVALUATED", "V8_STEP20_RETURN_TO_STEP17_5"),
        )

    def test_s07_discovery_path_cannot_change_blind_certification_packet(self):
        common = {"ticker": "PATH", "facts": {"fundamental_delta": 0.4}, "evidence_ids": ["E1"]}
        first = v8_blind_packet({**common, "discovery_priority_score": 99, "discovery_rank": 1, "pre_a_status": "PRE_A_HIGH"})
        second = v8_blind_packet({**common, "discovery_priority_score": 5, "discovery_rank": 199, "pre_a_status": "NONE"})
        self.assertEqual(first, second)

    def test_s08_scanner_engineering_failure_is_data_block_not_rejection(self):
        import stock_agent.v8_main_discovery_coach as coach
        raw = {"candidate_universe_packet": [{"security_id": "ERR1"}, {"security_id": "ERR2"}]}
        payload = _isolated_round_payload("02", raw, RuntimeError("simulated provider failure"))
        self.assertEqual(payload["execution_status"], "DATA_BLOCKED")
        self.assertEqual(payload["screened_count"], 0)
        self.assertFalse(payload["source_exhaustion"])
        self.assertFalse(payload["grade_authority"])
        self.assertEqual(payload["candidates"], [])
        self.assertEqual({row["disposition"] for row in payload["coverage_ledger"]}, {"DATA_BLOCK"})
        self.assertEqual({row["failure_class"] for row in payload["coverage_ledger"]}, {"DATA_INTEGRITY_BLOCK"})
        self.assertEqual(payload["scanner_source_sha256"], coach.V8_SCANNERS["02"]["sha256"])

    def test_s09_pre_a_complete_cannot_silently_omit_structured_candidate(self):
        bundle = _bundle()
        second = copy.deepcopy(bundle["candidates"][0])
        second["ticker"] = "XYZ"
        second["decision"] = {"ticker": "XYZ", "grade": "B+", "not_evaluated": False}
        bundle["candidates"].append(second)
        bundle["candidate_count"] = 2
        with self.assertRaisesRegex(PreASidecarError, "coverage mismatch"):
            validate_sidecar_payload(_payload(), bundle)

    def test_s10_insufficient_source_report_cannot_claim_pre_a(self):
        payload = _payload()
        payload["analysis_status"] = "INSUFFICIENT_SOURCE_REPORT"
        with self.assertRaisesRegex(PreASidecarError, "incomplete PRE-A analysis"):
            validate_sidecar_payload(payload, _bundle())

    def test_s11_exclude_cannot_claim_watch_trajectory(self):
        payload = _payload(_candidate(source_grade="EXCLUDE", promotion_readiness="WATCH_TRAJECTORY", a_trajectory="LOW"))
        with self.assertRaisesRegex(PreASidecarError, "EXCLUDE"):
            validate_sidecar_payload(payload, _bundle(source_grade="EXCLUDE", decision={"ticker": "ABC", "grade": "EXCLUDE"}))

    def test_s12_pre_a_high_cannot_have_unknown_core_semantics(self):
        payload = _payload(_candidate(fundamental_direction="UNKNOWN"))
        with self.assertRaisesRegex(PreASidecarError, "fundamental"):
            validate_sidecar_payload(payload, _bundle())

    def test_s13_grade_conflict_preserves_grade_but_blocks_pre_a_promotion(self):
        bundle = _bundle(source_grade="B+", grade_conflict=True)
        with self.assertRaisesRegex(PreASidecarError, "grade conflict"):
            validate_sidecar_payload(_payload(), bundle)
        self.assertEqual(bundle["candidates"][0]["source_grade"], "B+")

    def test_s14_real_conservation_ledger_distinguishes_a_bplus_b_exclude(self):
        store = SQLiteStore(":memory:")
        try:
            rules = store.resolve_rule_set()
            run = store.create_run(RunMode.HUNT_ONLY, rules, "c" * 64, 0)
            ids = ["A1", "BP1", "B1", "X1"]
            _record(store, run, "STOCK_DISCOVERY", None, {
                "candidates": [
                    {"security_id": sid, "recommended_discovery_action": "DEEP_DIVE_NOW"}
                    for sid in ids
                ]
            })
            targets = {"A1": "A", "BP1": "B+", "B1": "B", "X1": "EXCLUDE"}
            for sid, target in targets.items():
                receipt, validator = _grade_chain(target, sid)
                _record(store, run, "EXPECTATION_GAP_GATE", sid, {"decision": GateDecision.PASS.value})
                _record(store, run, cert.STEP18_STAGE, sid, receipt)
                _record(store, run, cert.STEP20_STAGE, sid, validator)

            agent = SimpleNamespace(store=store, _v18_candidate_failures={})
            ledger = candidate_conservation_v21(agent, run.run_id)
            by_id = {row["security_id"]: row for row in ledger}
            self.assertEqual(by_id["A1"]["state"], "PASS")
            self.assertEqual(by_id["BP1"]["state"], "NEXT_STAGE")
            self.assertEqual(by_id["B1"]["state"], "WATCH")
            self.assertEqual(by_id["X1"]["state"], "REJECT")
            self.assertTrue(all(row["evaluation_complete"] for row in ledger))
            funnel = {row["funnel_stage"]: row["count"] for row in store.list_funnel(run.run_id)}
            self.assertEqual(funnel.get("CONSERVATION_NOT_EVALUATED"), 0)
            self.assertEqual(funnel.get("CONSERVATION_ENGINEERING_FAILURE"), 0)
        finally:
            store.close()

    def test_s15_production_final_owner_remains_sentinel_with_semantic_guard_in_place(self):
        from stock_agent.production import production_composition
        composition = production_composition()
        self.assertEqual(composition["runtime_class"], "V8PreLiveSentinelProductionStockAgent")
        self.assertEqual(composition["v8_system_semantics_version"], V8_SYSTEM_SEMANTICS_VERSION)
        self.assertFalse(composition["discovery_recall_lite_runtime_installed"])


if __name__ == "__main__":
    unittest.main()
