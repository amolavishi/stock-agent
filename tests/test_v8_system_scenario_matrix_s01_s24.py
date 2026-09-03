from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from stock_agent import v8_next_certification as cert
from stock_agent.gates import MarketContextGate
from stock_agent.hunt_integrity_v18 import _select_source_indices, _v18_excerpt
from stock_agent.models import EffectiveRuleSet, GateDecision, RunMode, canonical_hash
from stock_agent.pre_a_sidecar import PreASidecarError, validate_sidecar_payload
from stock_agent.store import SQLiteStore
from stock_agent.v8_evidence_origin_v19 import _normalized_text
from stock_agent.v8_main_recall_conservation import _record_debt
from stock_agent.v8_main_scanner_failure_isolation import _isolated_round_payload
from stock_agent.v8_market_discovery_admission import _MarketContextDiscoveryAdmissionGate
from stock_agent.v8_next_certification_v11 import install_v8_next_certification_v11
from stock_agent.v8_next_successor import V8_NEXT_POLICY_HASH, V8_NEXT_POLICY_VERSION, validate_v8_next_certification
from stock_agent.v8_pre_live_integrity_v202 import source_lineage_v202
from stock_agent.v8_semantic_core_v22 import (
    blind_certification_packet,
    candidate_conservation_v22,
    derive_authoritative_run_terminal_state,
)
from stock_agent.v8_system_semantics_v21 import certification_terminal_state, validated_research_grade
from tests.test_pre_a_sidecar import _bundle, _candidate, _payload
from tests.test_v8_next_certification_runtime import (
    robust_assumption_audit,
    robust_atomic_audit,
    robust_fd_bridge,
    robust_step18_draft,
)


install_v8_next_certification_v11()


def _packet(ticker: str) -> dict:
    body = {
        "ticker": ticker,
        "analysis_as_of": "2026-09-03T00:00:00Z",
        "facts": {"fundamental_delta": 0.25},
    }
    return {**body, "packet_hash": canonical_hash(body)}


def _grade_chain(target: str, ticker: str) -> tuple[dict, dict]:
    draft = robust_step18_draft()
    if target == "A-":
        # 89 / 105 = 84.7619..., inside canonical A- band [80, 85).
        draft["score_components"]["catalyst_strength"] = 15.0
        draft["why_not_one_grade_higher"] = ["Normalized score is below the A threshold."]
    elif target == "B+":
        draft["grade_caps"] = [{
            "code": "S03_MAX_B_PLUS",
            "max_grade": "B+",
            "rationale": "one non-critical verification constraint remains",
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
        draft,
        assumption,
        atomic,
        fd,
        packet,
        10.0,
        ["E1"],
        V8_NEXT_POLICY_VERSION,
        V8_NEXT_POLICY_HASH,
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


def _record(store: SQLiteStore, run, stage: str, sid: str | None, value: dict, *, status: str = "SUCCEEDED") -> None:
    deps: list[str] = []
    store.record_stage_result(
        run.run_id,
        None,
        stage,
        sid,
        value,
        deps,
        store.dependency_hash(deps, run.rule_set.rule_set_hash, run.context_manifest_hash),
        store.current_evidence_epoch_for(deps),
        status=status,
    )


def _new_run(context_char: str = "a"):
    store = SQLiteStore(":memory:")
    rules = store.resolve_rule_set()
    run = store.create_run(RunMode.HUNT_ONLY, rules, context_char * 64, 0)
    return store, run


def _discover(store: SQLiteStore, run, ids: list[str]) -> None:
    _record(store, run, "STOCK_DISCOVERY", None, {
        "candidates": [
            {"security_id": sid, "recommended_discovery_action": "DEEP_DIVE_NOW"}
            for sid in ids
        ]
    })


def _certify(store: SQLiteStore, run, sid: str, grade: str) -> None:
    receipt, validator = _grade_chain(grade, sid)
    _record(store, run, "EXPECTATION_GAP_GATE", sid, {"decision": GateDecision.PASS.value})
    _record(store, run, cert.STEP18_STAGE, sid, receipt)
    _record(store, run, cert.STEP20_STAGE, sid, validator)


def _complete_proof(*, qualified: bool = False, clean_no_trade: bool = False) -> dict:
    return {
        "source_integrity_pass": True,
        "scanner_required_count": 13,
        "scanner_executed_count": 13,
        "scanner_validated_count": 13,
        "scanner_coverage_complete": True,
        "sentinel_complete": True,
        "candidate_engineering_failure_count": 0,
        "scanner_data_block_count": 0,
        "source_exhaustion_proven": True,
        "search_stop_allowed": True,
        "candidate_conservation_complete": True,
        "candidate_not_evaluated_count": 0,
        "proof_status": "PASS",
        "qualified_pool_authorized": qualified,
        "clean_no_trade_authorized": clean_no_trade,
    }


class V8CanonicalS01S24AcceptanceTests(unittest.TestCase):
    def test_s01_a_candidate_is_completed_pass_and_never_pre_a(self):
        receipt, validator = _grade_chain("A", "S01")
        self.assertEqual(validated_research_grade(receipt), "A")
        self.assertEqual(validator["route"], "PASS")
        self.assertEqual(
            certification_terminal_state("A", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False),
            ("PASS", "V8_CERTIFICATION_A"),
        )
        with self.assertRaises(PreASidecarError):
            validate_sidecar_payload(
                _payload(_candidate(source_grade="A", promotion_readiness="PRE_A", a_trajectory="HIGH")),
                _bundle(source_grade="A", decision={"ticker": "ABC", "grade": "A"}),
            )

    def test_s02_a_minus_candidate_is_completed_pass_and_never_pre_a(self):
        receipt, validator = _grade_chain("A-", "S02")
        self.assertEqual(validated_research_grade(receipt), "A-")
        self.assertEqual(validator["route"], "PASS")
        self.assertEqual(
            certification_terminal_state("A-", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False),
            ("PASS", "V8_CERTIFICATION_A-"),
        )
        with self.assertRaises(PreASidecarError):
            validate_sidecar_payload(
                _payload(_candidate(source_grade="A-", promotion_readiness="PRE_A", a_trajectory="HIGH")),
                _bundle(source_grade="A-", decision={"ticker": "ABC", "grade": "A-"}),
            )

    def test_s03_b_plus_is_completed_next_stage_and_can_be_pre_a(self):
        receipt, validator = _grade_chain("B+", "S03")
        self.assertEqual(validated_research_grade(receipt), "B+")
        self.assertEqual(validator["route"], "PASS")
        self.assertEqual(
            certification_terminal_state("B+", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False),
            ("NEXT_STAGE", "V8_CERTIFICATION_B_PLUS_PRE_A"),
        )
        validate_sidecar_payload(_payload(), _bundle())

    def test_s04_b_is_completed_watch_and_cannot_be_pre_a(self):
        receipt, validator = _grade_chain("B", "S04")
        self.assertEqual(validated_research_grade(receipt), "B")
        self.assertEqual(validator["route"], "PASS")
        self.assertEqual(
            certification_terminal_state("B", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False),
            ("WATCH", "V8_CERTIFICATION_B_WATCH"),
        )
        with self.assertRaises(PreASidecarError):
            validate_sidecar_payload(
                _payload(_candidate(source_grade="B", promotion_readiness="PRE_A", a_trajectory="MEDIUM")),
                _bundle(source_grade="B", decision={"ticker": "ABC", "grade": "B"}),
            )

    def test_s05_exclude_is_completed_reject_not_unknown(self):
        receipt, validator = _grade_chain("EXCLUDE", "S05")
        self.assertEqual(validated_research_grade(receipt), "EXCLUDE")
        self.assertEqual(validator["route"], "PASS")
        self.assertEqual(
            certification_terminal_state("EXCLUDE", step20_route="PASS", expectation_gap_pass=True, has_evidence_debt=False),
            ("REJECT", "V8_CERTIFICATION_EXCLUDE"),
        )

    def test_s06_missing_certification_is_not_evaluated(self):
        self.assertEqual(
            certification_terminal_state(None, step20_route=None, expectation_gap_pass=True, has_evidence_debt=False),
            ("NOT_EVALUATED", "V8_CERTIFICATION_MISSING_OR_INVALID"),
        )

    def test_s07_step20_return_blocks_completed_qualification(self):
        receipt, _ = _grade_chain("A", "S07")
        grade = validated_research_grade(receipt)
        self.assertEqual(
            certification_terminal_state(grade, step20_route="RETURN_TO_STEP17_5", expectation_gap_pass=True, has_evidence_debt=False),
            ("NOT_EVALUATED", "V8_STEP20_RETURN_TO_STEP17_5"),
        )

    def test_s08_scanner_provider_failure_is_data_block_not_rejection(self):
        payload = _isolated_round_payload(
            "02",
            {"candidate_universe_packet": [{"security_id": "ERR1"}, {"security_id": "ERR2"}]},
            RuntimeError("provider failure"),
        )
        self.assertEqual(payload["execution_status"], "DATA_BLOCKED")
        self.assertEqual(payload["candidates"], [])
        self.assertFalse(payload["source_exhaustion"])
        self.assertEqual({row["disposition"] for row in payload["coverage_ledger"]}, {"DATA_BLOCK"})

    def test_s09_research_model_failure_is_engineering_failure(self):
        store, run = _new_run("b")
        try:
            _discover(store, run, ["S09"])
            _record(store, run, "CANDIDATE_ENGINEERING_FAILURE", "S09", {
                "status": "ENGINEERING_FAILURE",
                "failed_stage": "DEEP_RESEARCH",
                "error_type": "ModelError",
            }, status="FAILED")
            ledger = candidate_conservation_v22(SimpleNamespace(store=store, _v18_candidate_failures={}), run.run_id)
            self.assertEqual(ledger[0]["state"], "ENGINEERING_FAILURE")
            self.assertFalse(ledger[0]["investment_reject"])
        finally:
            store.close()

    def test_s10_audit_evidence_incomplete_is_debt_or_engineering_not_reject(self):
        store, run = _new_run("c")
        try:
            _discover(store, run, ["S10"])
            _record(store, run, "ADVERSARIAL_AUDIT", "S10", {
                "status": "INCOMPLETE",
                "audit_recommendation": "AUDIT_EVIDENCE_INCOMPLETE",
                "engineering_failure": False,
            })
            ledger = candidate_conservation_v22(SimpleNamespace(store=store, _v18_candidate_failures={}), run.run_id)
            self.assertEqual(ledger[0]["state"], "EVIDENCE_DEBT")
            self.assertFalse(ledger[0]["investment_reject"])
        finally:
            store.close()

    def test_s11_full_sec_fetch_failure_is_candidate_engineering_failure(self):
        store, run = _new_run("d")
        try:
            _discover(store, run, ["S11"])
            _record(store, run, "CANDIDATE_ENGINEERING_FAILURE", "S11", {
                "status": "ENGINEERING_FAILURE",
                "failed_stage": "FULL_SEC_FORENSIC",
                "error_type": "SECTransportError",
            }, status="FAILED")
            ledger = candidate_conservation_v22(SimpleNamespace(store=store, _v18_candidate_failures={}), run.run_id)
            self.assertEqual(ledger[0]["state"], "ENGINEERING_FAILURE")
            self.assertEqual(ledger[0]["reason"], "FULL_SEC_FORENSIC")
        finally:
            store.close()

    def test_s12_one_candidate_failure_does_not_kill_other_valid_a(self):
        store, run = _new_run("e")
        try:
            _discover(store, run, ["FAIL_A", "VALID_A"])
            _record(store, run, "CANDIDATE_ENGINEERING_FAILURE", "FAIL_A", {
                "status": "ENGINEERING_FAILURE",
                "failed_stage": "DEEP_RESEARCH",
                "error_type": "TimeoutError",
            }, status="FAILED")
            _certify(store, run, "VALID_A", "A")
            ledger = candidate_conservation_v22(SimpleNamespace(store=store, _v18_candidate_failures={}), run.run_id)
            by_id = {row["security_id"]: row for row in ledger}
            self.assertEqual(by_id["FAIL_A"]["state"], "ENGINEERING_FAILURE")
            self.assertEqual(by_id["VALID_A"]["state"], "PASS")
        finally:
            store.close()

    def test_s13_weak_market_context_does_not_auto_reject_company_catalyst(self):
        stamp = "2026-09-01T20:00:00Z"
        context = {
            "assets": {
                symbol: {"observed_at": stamp, "source": "fixture", "observation_count": 2}
                for symbol in ("SPY", "QQQ", "IWM", "VIX")
            },
            "regime": "RISK_OFF",
            "breadth": "WEAK",
            "volatility": "HIGH",
            "normalization_status": "PARTIAL",
        }
        admitted = {}
        proxy = _MarketContextDiscoveryAdmissionGate(
            MarketContextGate(),
            lambda value, reason: admitted.update(value=value, reason=reason),
        )
        receipt = proxy.evaluate(
            context,
            EffectiveRuleSet(),
            evaluation_time=datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(receipt.decision, GateDecision.PASS)
        self.assertTrue(admitted["value"])
        grade_receipt, _ = _grade_chain("A", "S13_COMPANY_CATALYST")
        self.assertEqual(validated_research_grade(grade_receipt), "A")

    def test_s14_weak_or_missing_technical_is_research_debt_not_thesis_reject(self):
        class Store:
            def __init__(self):
                self.rows = []
            def dependency_hash(self, ids, rule_hash, context_hash):
                return "dep"
            def current_evidence_epoch_for(self, ids):
                return 0
            def record_stage_result(self, run_id, work_item_id, stage, subject_id, payload, dependency_ids, dependency_hash, evidence_epoch, status="SUCCEEDED"):
                self.rows.append({"stage": stage, "subject_id": subject_id, "payload": payload, "status": status})
        agent = SimpleNamespace(store=Store())
        run = SimpleNamespace(run_id="RUN-S14", rule_set=SimpleNamespace(rule_set_hash="rules"), context_manifest_hash="ctx")
        _record_debt(agent, run, "S14", "DEEP_DIVE_NOW")
        row = agent.store.rows[0]
        self.assertEqual(row["payload"]["status"], "EVIDENCE_DEBT")
        self.assertEqual(row["payload"]["discovery_action_after_debt"], "DEEP_DIVE_SECONDARY")
        self.assertNotIn("EXCLUDE", row["payload"].values())

    def test_s15_late_bearish_source_30_reaches_working_selection(self):
        values = [{"title": f"source {i}", "content": "routine neutral text"} for i in range(31)]
        values[30] = {"title": "late adverse", "content": "covenant breach and dilution risk"}
        selected = _select_source_indices(values, 12)
        self.assertIn(30, selected)

    def test_s16_late_bullish_source_30_reaches_working_selection(self):
        values = [{"title": f"source {i}", "content": "routine neutral text"} for i in range(31)]
        values[30] = {"title": "late catalyst", "content": "major customer contract expands backlog"}
        selected = _select_source_indices(values, 12)
        self.assertIn(30, selected)

    def test_s17_keywordless_middle_document_fact_survives_structural_sampling(self):
        marker = "UNEXPECTED_CONTROL_BREAKDOWN_FACT_X7Q"
        text = "A" * 12000 + marker + "B" * 12000
        excerpt = _v18_excerpt(text, 7000)
        self.assertIn(marker, excerpt)

    def test_s18_reprint_chain_without_proven_origin_separation_counts_one_origin(self):
        parent = "PARENT-AGGREGATE-1"
        sources = [
            {"source_class": "COMPANY_PR", "title": "same event", "content": "announcement body"},
            {"source_class": "REUTERS", "title": "reprint", "content": "reworded announcement"},
            {"source_class": "YAHOO", "title": "reprint 2", "content": "another rewording"},
        ]
        origins = {source_lineage_v202(source, parent)[0] for source in sources}
        self.assertEqual(len(origins), 1)
        self.assertTrue(all(_normalized_text(source["content"]) for source in sources))

    def test_s19_discovery_score_and_path_mutation_cannot_change_certification_input(self):
        factual = {"ticker": "S19", "facts": {"revenue_delta": 0.4}, "evidence_ids": ["E1"]}
        high = blind_certification_packet({**factual, "discovery_priority_score": 99, "discovery_rank": 1, "scanner_priority": "HIGH"})
        low = blind_certification_packet({**factual, "discovery_priority_score": 1, "discovery_rank": 999, "scanner_priority": "LOW"})
        self.assertEqual(high, low)
        self.assertEqual(canonical_hash(high), canonical_hash(low))

    def test_s20_pre_a_metadata_mutation_cannot_change_certification_input(self):
        factual = {"ticker": "S20", "facts": {"revenue_delta": 0.4}, "evidence_ids": ["E1"]}
        pre_a = blind_certification_packet({**factual, "pre_a_status": "PRE_A_HIGH", "promotion_readiness": "PRE_A", "a_trajectory": "HIGH"})
        none = blind_certification_packet({**factual, "pre_a_status": "NONE", "promotion_readiness": "NONE", "a_trajectory": "NONE"})
        self.assertEqual(pre_a, none)

    def test_s21_grade_quota_contamination_is_scrubbed_before_certification(self):
        factual = {"ticker": "S21", "facts": {"revenue_delta": 0.4}, "evidence_ids": ["E1"]}
        contaminated = blind_certification_packet({
            **factual,
            "candidate_shortage": True,
            "target_verified_a_minus_or_better": 5,
            "verified_a_count": 0,
            "remaining_a_needed": 5,
        })
        clean = blind_certification_packet(factual)
        self.assertEqual(contaminated, clean)

    def test_s22_market_context_failure_is_not_evaluable_never_no_trade(self):
        terminal, reason = derive_authoritative_run_terminal_state(
            "NOT_EVALUABLE_MARKET_CONTEXT",
            RunMode.HUNT_ONLY,
            _complete_proof(clean_no_trade=True),
        )
        self.assertEqual(terminal, "NOT_EVALUABLE_MARKET_CONTEXT")
        self.assertIsNotNone(reason)
        self.assertNotEqual(terminal, "NO_TRADE")

    def test_s23_source_integrity_failure_is_not_evaluable_input_integrity(self):
        proof = _complete_proof(clean_no_trade=True)
        proof["source_integrity_pass"] = False
        terminal, _ = derive_authoritative_run_terminal_state(
            "NO_QUALIFIED_CANDIDATE",
            RunMode.HUNT_ONLY,
            proof,
        )
        self.assertEqual(terminal, "NOT_EVALUABLE_INPUT_INTEGRITY")
        self.assertNotEqual(terminal, "NO_TRADE")

    def test_s24_only_complete_proof_can_authorize_clean_no_trade(self):
        terminal, reason = derive_authoritative_run_terminal_state(
            "NO_QUALIFIED_CANDIDATE",
            RunMode.HUNT_ONLY,
            _complete_proof(clean_no_trade=True),
        )
        self.assertEqual(terminal, "NO_TRADE")
        self.assertIsNone(reason)

        incomplete = _complete_proof(clean_no_trade=True)
        incomplete["source_exhaustion_proven"] = False
        incomplete["proof_status"] = "INCOMPLETE"
        terminal, _ = derive_authoritative_run_terminal_state(
            "NO_QUALIFIED_CANDIDATE",
            RunMode.HUNT_ONLY,
            incomplete,
        )
        self.assertEqual(terminal, "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT")


if __name__ == "__main__":
    unittest.main()
