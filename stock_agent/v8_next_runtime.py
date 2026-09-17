"""Production wiring for the V8 NEXT certification pipeline.

This layer is installed after V8 NEXT successor policy.  It turns the
repository-owned Step15/16/17/17.5/18/20 contracts into the actual strict HUNT
runtime without giving the model grade authority.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from . import hunt_integrity_v18 as v18
from . import runtime as runtime_module
from . import store as store_module
from . import v8_next_certification as cert
from . import v8_next_successor as successor
from .models import GateDecision, canonical_hash

V8_NEXT_RUNTIME_VERSION = "V8_NEXT_CERTIFICATION_RUNTIME_V1.0"

STEP15_DRAFT = "V8_CAPITAL_STRUCTURE_BRIDGE_DRAFT"
STEP16_DRAFT = "V8_ATOMIC_CLAIM_AUDIT_DRAFT"
STEP17_5_DRAFT = "V8_CRITICAL_ASSUMPTION_AUDIT_DRAFT"

_MODEL_DRAFT_STAGES = {STEP15_DRAFT, STEP16_DRAFT, STEP17_5_DRAFT, cert.STEP18_DRAFT_STAGE}


def _latest_payload(store: Any, run_id: str, stage: str, subject_id: str) -> dict[str, Any] | None:
    row = store.get_stage_result(run_id, stage, subject_id)
    if not row or row.get("status") != "SUCCEEDED":
        return None
    try:
        value = json.loads(row.get("result_json") or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _record_stage(store: Any, run: Any, stage: str, subject_id: str, payload: dict[str, Any], evidence_ids: list[str], *, status: str = "SUCCEEDED") -> str:
    ids = list(dict.fromkeys(str(item) for item in evidence_ids))
    dep_hash = store.dependency_hash(ids, run.rule_set.rule_set_hash, run.context_manifest_hash)
    return store.record_stage_result(
        run.run_id, None, stage, subject_id, payload, ids, dep_hash,
        store.current_evidence_epoch_for(ids), status=status,
    )


def _augment_packet_with_sec_raw(store: Any, subject_id: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Attach persisted SEC raw observations without importing discovery anchors."""
    value = {key: copy.deepcopy(item) for key, item in packet.items() if key != "packet_hash"}
    rows = store.connection.execute(
        "SELECT artifact_id,artifact_type,provider,payload_json,payload_hash,source_observed_at,retrieved_at "
        "FROM raw_artifacts WHERE subject_id=? AND artifact_type LIKE 'SEC%' ORDER BY created_at DESC LIMIT 32",
        (subject_id,),
    ).fetchall()
    raw_items: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            continue
        evidence = store.connection.execute(
            "SELECT evidence_id FROM evidence WHERE raw_artifact_id=? AND subject_id=? AND status='ACTIVE' ORDER BY evidence_id",
            (row["artifact_id"], subject_id),
        ).fetchall()
        raw_items.append({
            "artifact_id": str(row["artifact_id"]),
            "artifact_type": str(row["artifact_type"]),
            "provider": str(row["provider"]),
            "payload_hash": str(row["payload_hash"]),
            "source_observed_at": row["source_observed_at"],
            "retrieved_at": row["retrieved_at"],
            "evidence_ids": [str(item["evidence_id"]) for item in evidence],
            "payload": payload,
        })
    value["sec_raw_artifacts"] = raw_items
    # Use the same firewall used by canonical Step17.  No discovery rank/score,
    # prior grade, target, position or PRE-A field may survive.
    from .v8_primary import v8_blind_packet, assert_pre18_grade_firewall
    value = v8_blind_packet(value)
    assert_pre18_grade_firewall(value)
    value["packet_hash"] = canonical_hash(value)
    return value


def _finalize_atomic_audit(draft: dict[str, Any], evidence_ids: list[str]) -> dict[str, Any]:
    value = copy.deepcopy(draft) if isinstance(draft, dict) else {}
    failures = cert.evidence_reference_failures(value, evidence_ids)
    claims = [item for item in (value.get("atomic_claims") or []) if isinstance(item, dict)]
    claim_ids = [str(item.get("claim_id") or "") for item in claims]
    if not claims:
        failures.append("NO_ATOMIC_CLAIMS")
    if len(claim_ids) != len(set(claim_ids)) or any(not item for item in claim_ids):
        failures.append("DUPLICATE_OR_MISSING_CLAIM_ID")
    event_counts: dict[str, int] = {}
    for item in claims:
        event_id = str(item.get("economic_event_id") or "")
        if event_id:
            event_counts[event_id] = event_counts.get(event_id, 0) + 1
    actual_duplicates = sorted(key for key, count in event_counts.items() if count > 1)
    declared_duplicates = sorted(set(str(item) for item in (value.get("duplicate_economic_event_ids") or [])))
    if declared_duplicates != actual_duplicates:
        failures.append("DUPLICATE_EVENT_LEDGER_MISMATCH")
    if any(str(item.get("verification_status") or "") != "VERIFIED" for item in claims):
        failures.append("UNVERIFIED_OR_CONTRADICTED_ATOMIC_CLAIM")
    if str(value.get("evidence_independence") or "") != "PASS":
        failures.append("EVIDENCE_INDEPENDENCE_NOT_PASS")
    realization = value.get("value_realization_bridge_1_8w") if isinstance(value.get("value_realization_bridge_1_8w"), dict) else {}
    if str(realization.get("status") or "") != "ROBUST":
        failures.append("REALIZATION_BRIDGE_NOT_ROBUST")
    if str(value.get("probability_provenance") or "") not in {"DATA_BACKED", "CALIBRATED_RANGE"}:
        failures.append("PROBABILITY_PROVENANCE_WEAK")
    value.update({
        "status": "COMPLETE" if str(value.get("status") or "") == "COMPLETE" and not failures else "INCOMPLETE",
        "validation_failures": sorted(set(failures)),
        "validation_authority": "PYTHON_V8_NEXT_ATOMIC_AUDIT_V1",
        "grade_authority": False,
    })
    return value


def _finalize_assumption_audit(draft: dict[str, Any], evidence_ids: list[str]) -> dict[str, Any]:
    value = copy.deepcopy(draft) if isinstance(draft, dict) else {}
    failures = cert.evidence_reference_failures(value, evidence_ids)
    assumptions = [item for item in (value.get("assumptions") or []) if isinstance(item, dict)]
    ids = [str(item.get("assumption_id") or "") for item in assumptions]
    if sorted(ids) != sorted(cert.ASSUMPTIONS) or len(ids) != len(set(ids)):
        failures.append("ASSUMPTION_SET_MISMATCH")
    value.update({
        "status": "COMPLETE" if str(value.get("status") or "") == "COMPLETE" and not failures else "INCOMPLETE",
        "validation_failures": sorted(set(failures)),
        "validation_authority": "PYTHON_V8_NEXT_STEP17_5_V1",
        "grade_authority": False,
    })
    return value


def _v18_next_certification_grade(payload: dict[str, Any] | None) -> str | None:
    grade, failures = successor.validate_v8_next_certification(payload)
    if failures or grade not in {"A", "A-", "B+", "B"}:
        return None
    return grade


def install_v8_next_runtime() -> type:
    current = runtime_module.ProductionStockAgent
    if getattr(current, "v8_next_runtime_version", None) == V8_NEXT_RUNTIME_VERSION:
        return current

    # V1.8's closure calls this module-global helper dynamically.  Replace its
    # legacy authority parser so the successor receipt is accepted only after
    # the V8 NEXT validator succeeds.
    v18._certification_grade = _v18_next_certification_grade
    v18._CANDIDATE_MODEL_STAGES.update(_MODEL_DRAFT_STAGES)

    # Add a final Step20 validator fence on top of the successor qualification.
    base_qualified = store_module.SQLiteStore.qualified_candidate_status

    def qualified_candidate_status_v8_next_runtime(self: Any, run_id: str, subject_id: str, strict: bool = True) -> tuple[bool, list[str]]:
        qualified, missing = base_qualified(self, run_id, subject_id, strict=strict)
        if not strict:
            return qualified, missing
        validator = _latest_payload(self, run_id, cert.STEP20_STAGE, subject_id)
        if not isinstance(validator, dict) or validator.get("status") != "PASS" or validator.get("route") != "PASS":
            qualified = False
            missing = [*missing, "V8_STEP20_RESEARCH_VALIDATOR_PASS"]
        return qualified, sorted(set(missing))

    store_module.SQLiteStore.qualified_candidate_status = qualified_candidate_status_v8_next_runtime

    class V8NextRuntimeProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_next_runtime_version = V8_NEXT_RUNTIME_VERSION
        STAGE_PREREQUISITES = {
            **getattr(current, "STAGE_PREREQUISITES", {}),
            STEP15_DRAFT: ("FULL_SEC_FORENSIC",),
            STEP16_DRAFT: (STEP15_DRAFT,),
            STEP17_5_DRAFT: (STEP16_DRAFT,),
            cert.STEP18_DRAFT_STAGE: (STEP17_5_DRAFT,),
        }

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            cert.install_runtime_prompt_contracts(self.prompts)

        def _profile_for_stage(self, stage: str) -> str:
            if stage == STEP15_DRAFT and "DEEP_REASONING" in self.router.profiles:
                return "DEEP_REASONING"
            if stage in {STEP16_DRAFT, STEP17_5_DRAFT, cert.STEP18_DRAFT_STAGE} and "CRITICAL_AUDIT" in self.router.profiles:
                return "CRITICAL_AUDIT"
            return super()._profile_for_stage(stage)

        def _run_next_model_stage(self, run: Any, stage: str, prompt_id: str, packet: dict[str, Any], subject_id: str, evidence_ids: list[str], default_payload: dict[str, Any]) -> dict[str, Any]:
            return self._work_stage(
                run, stage, prompt_id,
                {"raw_input": packet, "default_payload": default_payload},
                subject_id, evidence_ids,
                {"certification_packet": self._typed_context("V8_NEXT_CERTIFICATION_INPUT", "V8NextCertificationPacket", packet)},
            )

        def _persist_hunt_reverse_valuation(self, run: Any, candidate: dict[str, Any], evidence_ids: list[str], research_artifact: Any, research_evidence_id: str, universe_artifact: Any, raw_row: dict[str, Any]) -> tuple[bool, Any]:
            known, gate = super()._persist_hunt_reverse_valuation(
                run, candidate, evidence_ids, research_artifact, research_evidence_id, universe_artifact, raw_row
            )
            if gate.decision != GateDecision.PASS:
                return known, gate

            sid = str(candidate.get("security_id") or "").upper()
            if not sid:
                return known, gate
            try:
                packet = cert.build_step17_packet(
                    self.store, run.run_id, sid, candidate, evidence_ids,
                    research_artifact.payload if isinstance(getattr(research_artifact, "payload", None), dict) else {},
                    successor.V8_NEXT_POLICY_VERSION, successor.V8_NEXT_POLICY_HASH,
                )
                packet = _augment_packet_with_sec_raw(self.store, sid, packet)

                step15_draft = self._run_next_model_stage(
                    run, STEP15_DRAFT, cert.PROMPT_STEP15, packet, sid, evidence_ids,
                    cert.default_step15(evidence_ids),
                )
                if step15_draft.get("engineering_failure"):
                    return known, gate
                fd_bridge = cert.finalize_fd_bridge(step15_draft, evidence_ids)
                _record_stage(self.store, run, cert.STEP15_STAGE, sid, fd_bridge, evidence_ids)

                packet = cert.build_step17_packet(
                    self.store, run.run_id, sid, candidate, evidence_ids,
                    research_artifact.payload if isinstance(getattr(research_artifact, "payload", None), dict) else {},
                    successor.V8_NEXT_POLICY_VERSION, successor.V8_NEXT_POLICY_HASH,
                )
                packet = _augment_packet_with_sec_raw(self.store, sid, packet)
                step16_draft = self._run_next_model_stage(
                    run, STEP16_DRAFT, cert.PROMPT_STEP16, packet, sid, evidence_ids,
                    cert.default_step16(packet, evidence_ids),
                )
                if step16_draft.get("engineering_failure"):
                    return known, gate
                atomic_audit = _finalize_atomic_audit(step16_draft, evidence_ids)
                _record_stage(self.store, run, cert.STEP16_STAGE, sid, atomic_audit, evidence_ids)

                packet = cert.build_step17_packet(
                    self.store, run.run_id, sid, candidate, evidence_ids,
                    research_artifact.payload if isinstance(getattr(research_artifact, "payload", None), dict) else {},
                    successor.V8_NEXT_POLICY_VERSION, successor.V8_NEXT_POLICY_HASH,
                )
                packet = _augment_packet_with_sec_raw(self.store, sid, packet)
                _record_stage(self.store, run, cert.STEP17_STAGE, sid, packet, evidence_ids)

                step17_5_draft = self._run_next_model_stage(
                    run, STEP17_5_DRAFT, cert.PROMPT_STEP17_5, packet, sid, evidence_ids,
                    cert.default_step17_5(),
                )
                if step17_5_draft.get("engineering_failure"):
                    return known, gate
                assumption_audit = _finalize_assumption_audit(step17_5_draft, evidence_ids)
                _record_stage(self.store, run, cert.STEP17_5_STAGE, sid, assumption_audit, evidence_ids)

                current_price = candidate.get("price", candidate.get("last_price"))
                if not isinstance(current_price, (int, float)) or isinstance(current_price, bool) or current_price <= 0:
                    reverse = candidate.get("reverse_valuation") if isinstance(candidate.get("reverse_valuation"), dict) else {}
                    current_price = reverse.get("current_price")

                step18_draft = self._run_next_model_stage(
                    run, cert.STEP18_DRAFT_STAGE, cert.PROMPT_STEP18, packet, sid, evidence_ids,
                    cert.default_step18(current_price, evidence_ids),
                )
                if step18_draft.get("engineering_failure"):
                    return known, gate
                certification = cert.finalize_certification(
                    step18_draft, assumption_audit, atomic_audit, fd_bridge, packet,
                    current_price, evidence_ids,
                    successor.V8_NEXT_POLICY_VERSION, successor.V8_NEXT_POLICY_HASH,
                )
                grade, certification_failures = successor.validate_v8_next_certification(certification)
                _record_stage(self.store, run, cert.STEP18_STAGE, sid, certification, evidence_ids)

                validator = cert.research_validator(
                    fd_bridge, atomic_audit, packet, assumption_audit, certification,
                    certification_failures,
                )
                validator["research_grade_seen"] = grade
                _record_stage(self.store, run, cert.STEP20_STAGE, sid, validator, evidence_ids)
                return known, gate
            except Exception as exc:
                marker = getattr(self, "_v18_mark_candidate_failure", None)
                if callable(marker):
                    marker(run, "V8_NEXT_CERTIFICATION_PIPELINE", sid, exc, evidence_ids)
                    return known, gate
                raise

        def _run_strict(self, mode: Any, data: dict[str, Any]):
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            rows = self.store.list_stage_results(run_id)
            certifications: dict[str, str] = {}
            validators: dict[str, str] = {}
            for row in rows:
                sid = str(row.get("subject_id") or "")
                if not sid or row.get("status") != "SUCCEEDED":
                    continue
                try:
                    payload = json.loads(row.get("result_json") or "{}")
                except (TypeError, ValueError):
                    continue
                if row.get("stage") == cert.STEP18_STAGE and isinstance(payload, dict):
                    certifications[sid] = str(payload.get("research_grade") or "UNKNOWN")
                elif row.get("stage") == cert.STEP20_STAGE and isinstance(payload, dict):
                    validators[sid] = str(payload.get("route") or "UNKNOWN")
            counts = {grade: sum(1 for value in certifications.values() if value == grade) for grade in ("A", "A-", "B+", "B", "EXCLUDE")}
            self.store.record_funnel(run_id, "V8_NEXT_CERTIFICATION_WRITER", len(certifications), {
                "runtime_version": V8_NEXT_RUNTIME_VERSION,
                "grades": counts,
                "certified_security_ids": sorted(certifications)[:300],
                "step20_pass": sum(1 for value in validators.values() if value == "PASS"),
                "grade_quota_forbidden": True,
                "model_grade_authority": False,
                "python_grade_authority": True,
            })
            return outcome

    runtime_module.ProductionStockAgent = V8NextRuntimeProductionStockAgent
    return V8NextRuntimeProductionStockAgent
