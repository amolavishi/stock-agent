---
prompt_id: adversarial.strong_thesis_destruction
version: 2.2.0
schema_version: prompt-meta-2.2
layer: ADVERSARIAL
category: audit
role: adversarial_reviewer
compatible_agents:
- adversarial_reviewer
task_tags:
- audit
- strong-thesis-destruction
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
recommended_model_class: MAX_REASONING
context_policy: independent evidence packet; minimize exposure to rhetorical conclusion
side_effects: none
authoritative_decision: false
description: 가장 강한 Bull thesis를 최대 강도로 파괴 시도한다. Python Python Python Python Python Python Python Python GateDecision은 Python이
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

# Strong Thesis Destruction v2.2

## Objective
가장 강한 Bull Claim 1~3개의 causal chain을 최대 강도로 파괴 시도한다.

## Exclusive Attack Focus
각 Claim마다 다음을 작성한다.
- causal chain과 necessary condition
- 관측 가능한 falsifier
- 대안 설명
- priced-in 여부
- Base/Bear sensitivity와 tail failure
- 어느 전제가 틀리면 thesis 전체가 무너지는지

## Recommendation Namespace
`SUPPORTS_CONTINUATION|CHALLENGES_CONTINUATION|NEEDS_NEW_EVIDENCE|AUDIT_EVIDENCE_INCOMPLETE`. Python GateDecision 문자열을 사용하지 않는다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#AdversarialAuditResult`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_AdversarialAuditResult`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `AdversarialAuditResult`에 대해 실제 검증한다.

