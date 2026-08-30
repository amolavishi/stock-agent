---
prompt_id: adversarial.standard_audit
version: 2.2.0
schema_version: prompt-meta-2.2
layer: ADVERSARIAL
category: audit
role: adversarial_reviewer
compatible_agents:
- adversarial_reviewer
task_tags:
- audit
- standard-audit
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
description: 원 분석의 사실·논리·누락을 독립적으로 검증한다. Python Python Python Python Python Python Python Python GateDecision은 Python이 소유한다.
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

# Standard Adversarial Audit v2.2

## Objective
기본 감사 축 전반을 넓게 검토한다.

## Exclusive Attack Focus
- unsupported claim과 Evidence quality
- valuation/target 역산과 Bull double counting
- catalyst timing/quality
- dilution/capital structure 누락
- 최소 3개 독립 failure path

전문적인 source reconciliation은 `evidence_contradiction_audit`, 핵심 causal-chain 붕괴는 `strong_thesis_destruction`에 맡긴다.

## Recommendation Namespace
`SUPPORTS_CONTINUATION|CHALLENGES_CONTINUATION|NEEDS_NEW_EVIDENCE|AUDIT_EVIDENCE_INCOMPLETE`. Python GateDecision 문자열을 사용하지 않는다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#AdversarialAuditResult`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_AdversarialAuditResult`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `AdversarialAuditResult`에 대해 실제 검증한다.

