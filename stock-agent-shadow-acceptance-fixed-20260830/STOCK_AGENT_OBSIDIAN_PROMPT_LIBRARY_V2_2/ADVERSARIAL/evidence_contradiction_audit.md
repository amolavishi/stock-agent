---
prompt_id: adversarial.evidence_contradiction_audit
version: 2.2.0
schema_version: prompt-meta-2.2
layer: ADVERSARIAL
category: audit
role: adversarial_reviewer
compatible_agents:
- adversarial_reviewer
task_tags:
- audit
- evidence-contradiction-audit
required_rule_packs:
- '00'
- '05'
- 09
- '10'
required_inputs:
- effective_rule_pack
- target_analysis
- evidence_packet
- issue_ledger
optional_inputs:
- company_facts
- capital_structure_snapshot
- valuation_result
output_schema: AdversarialAuditResult
incompatible_with: []
recommended_model_class: STRONG
context_policy: independent evidence packet; minimize exposure to rhetorical conclusion
side_effects: none
authoritative_decision: false
description: 서로 충돌하는 증거의 정의·기간·출처·문맥을 독립적으로 검증한다. Python Python Python Python Python Python Python Python GateDecision은 Python이
  소유한다.
compose_with:
- system.analysis_grounding
requires_results: []
requires_capabilities: []
prompt_kind: LEAF
stage: AUDIT
allowed_run_modes:
- HUNT_ONLY
- HUNT_AND_EXECUTION_REVIEW
conditional_dependencies: {}
output_example_case: positive_AdversarialAuditResult
---

# Evidence Contradiction Audit v2.2

## Objective
Claim의 결론보다 서로 충돌하는 Evidence의 정의와 lineage를 전문적으로 reconciliation한다.

## Exclusive Attack Focus
- source hierarchy conflict와 primary/secondary 충돌
- period·단위·통화 mismatch
- GAAP/non-GAAP 및 reported/adjusted mismatch
- stale vs current source와 superseded evidence
- company statement vs SEC contradiction
- claim-evidence mapping의 reconciliation 상태

## Recommendation Namespace
`SUPPORTS_CONTINUATION|CHALLENGES_CONTINUATION|NEEDS_NEW_EVIDENCE|AUDIT_EVIDENCE_INCOMPLETE`. Python GateDecision 문자열을 사용하지 않는다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#AdversarialAuditResult`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_AdversarialAuditResult`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `AdversarialAuditResult`에 대해 실제 검증한다.

