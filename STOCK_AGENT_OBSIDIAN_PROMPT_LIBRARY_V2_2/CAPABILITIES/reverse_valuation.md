---
prompt_id: capability.reverse_valuation
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
- reverse-valuation
required_rule_packs:
- '00'
- '01'
- 09
required_inputs:
- effective_rule_pack
- valuation_inputs
- company_facts
- price_snapshot
optional_inputs:
- peer_valuation_packet
output_schema: ReverseValuationAssessmentV2
incompatible_with: []
recommended_model_class: STRONG
context_policy: task-minimal context; only relevant evidence
side_effects: none
authoritative_decision: false
description: 현재 가격에 내재된 미래 실적 가정을 역산하고 re-rating 요구조건을 검증한다.
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
output_example_case: positive_ReverseValuationAssessmentV2
---

# Reverse Valuation v2

## Objective
현재 가격이 암묵적으로 요구하는 실적·성장·마진·FCF·multiple을 역산하고 Base/Bull re-rating의 요구조건을 검증한다.

## Analysis Tasks
1. 산업에 맞는 valuation framework를 선택하고 선택 이유를 명시한다.
2. Python valuation inputs/calculation이 있으면 이를 사용하고 산술을 재발명하지 않는다.
3. 현재가격이 implied revenue/growth/margin/FCF/multiple 중 무엇을 이미 선반영하는지 설명한다.
4. Base requirement와 Bull requirement를 분리한다.
5. rerating이 실적개선보다 multiple expansion에 과도하게 의존하는지 공격한다.
6. Fundamental Breakout 후에는 price move와 fair-value change를 비교한다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#ReverseValuationAssessmentV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_ReverseValuationAssessmentV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `ReverseValuationAssessmentV2`에 대해 실제 검증한다.

