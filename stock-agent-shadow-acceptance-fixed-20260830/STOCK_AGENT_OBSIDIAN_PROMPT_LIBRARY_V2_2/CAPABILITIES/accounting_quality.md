---
prompt_id: capability.accounting_quality
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
- accounting-quality
required_rule_packs:
- '00'
- 09
required_inputs:
- effective_rule_pack
- company_facts
- financial_statements
- filing_notes
optional_inputs: []
output_schema: AccountingQualityAssessmentV2
incompatible_with: []
recommended_model_class: STRONG
context_policy: task-minimal context; only relevant evidence
side_effects: none
authoritative_decision: false
description: 보고된 성장·수익성이 회계 정의·기간·조정항목 때문에 왜곡되지 않았는지 검증한다.
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
output_example_case: positive_AccountingQualityAssessmentV2
---

# Accounting Quality v2

## Objective
성장·수익성 개선이 회계 정의·기간·조정·인수회계 때문에 과장됐는지 검증한다.

## Analysis Tasks
1. quarterly/YTD, GAAP/non-GAAP, continuing/discontinued, acquisition accounting을 분리한다.
2. revenue recognition, deferred revenue, capitalization, SBC, restructuring, stock-based acquisition consideration, one-off를 추적한다.
3. working capital과 cash flow가 손익 개선을 지지하는지 확인한다.
4. KPI 정의변경·재분류·비교기간 변경으로 추세가 인위적으로 좋아졌는지 확인한다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#AccountingQualityAssessmentV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_AccountingQualityAssessmentV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `AccountingQualityAssessmentV2`에 대해 실제 검증한다.

