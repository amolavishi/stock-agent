---
prompt_id: capability.contract_backlog_quality
version: 2.2.0
schema_version: prompt-meta-2.2
layer: CAPABILITY
category: analysis
role: capability_worker
compatible_agents:
- stock_researcher
- forensic_analyst
- valuation_analyst
- adversarial_reviewer
task_tags:
- contract-backlog-quality
required_rule_packs:
- '00'
- '04'
- 09
required_inputs:
- effective_rule_pack
- contract_evidence
- company_facts
optional_inputs:
- customer_evidence
output_schema: ContractBacklogQualityAssessmentV2
incompatible_with: []
recommended_model_class: MID
context_policy: task-minimal context; only relevant evidence
side_effects: none
authoritative_decision: false
description: 계약·수주·backlog/RPO가 실제 주주경제 가치로 전환되는지를 검증한다.
compose_with:
- system.analysis_grounding
requires_results: []
requires_capabilities: []
prompt_kind: LEAF
stage: DEEP_RESEARCH
allowed_run_modes:
- HUNT_ONLY
- HUNT_AND_EXECUTION_REVIEW
conditional_dependencies: {}
output_example_case: positive_ContractBacklogQualityAssessmentV2
---

# Contract / Backlog Quality v2

## Objective
계약·수주·RPO·backlog가 실제 revenue/margin/FCF와 주당가치로 전환되는 정도를 검증한다.

## Analysis Tasks
1. MOU/LOI/IDIQ/award/definitive contract/PO를 binding status와 minimum guarantee로 구분한다.
2. 금액, 기간, 취소권, 고객, PO, backlog inclusion, revenue recognition을 확인한다.
3. expected margin, CapEx, working capital, milestone/acceptance risk를 연결한다.
4. bookings/backlog 증분이 acquisition·duration·renewal·FX 효과인지 분리한다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#ContractBacklogQualityAssessmentV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_ContractBacklogQualityAssessmentV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `ContractBacklogQualityAssessmentV2`에 대해 실제 검증한다.

