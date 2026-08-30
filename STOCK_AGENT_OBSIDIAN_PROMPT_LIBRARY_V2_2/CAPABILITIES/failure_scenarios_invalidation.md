---
prompt_id: capability.failure_scenarios_invalidation
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
- failure-scenarios-invalidation
required_rule_packs:
- '00'
- 09
required_inputs:
- effective_rule_pack
- research_result
- industry_overlay
optional_inputs:
- capital_structure_result
- valuation_result
output_schema: FailureScenarioInvalidationAssessmentV2
incompatible_with: []
recommended_model_class: MID
context_policy: task-minimal context; only relevant evidence
side_effects: none
authoritative_decision: false
description: 최소 3개의 독립 실패 경로와 사전에 검증 가능한 thesis 무효화 조건을 만든다.
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
output_example_case: positive_FailureScenarioInvalidationAssessmentV2
---

# Failure Scenarios & Invalidation v2

## Objective
최소 3개의 독립 실패경로와 관측 가능한 thesis invalidation을 만들고, Execution Stop/Thesis Stop/Structural Bear를 혼동하지 않게 한다.

## Analysis Tasks
1. 최소 Fundamental / Capital Structure / Price-Expectation 축을 분리하고 산업 특수 failure path를 추가한다.
2. 각 failure의 causal path, supporting/contradicting evidence, earliest observable trigger, probability direction, impact를 명시한다.
3. Thesis invalidation은 경제적 가설이 틀렸음을 인정하는 조건으로 정의한다.
4. Execution Stop은 실제 거래 종료 조건, Structural Bear는 stress/tail value로 별도 입력을 요구한다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#FailureScenarioInvalidationAssessmentV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_FailureScenarioInvalidationAssessmentV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `FailureScenarioInvalidationAssessmentV2`에 대해 실제 검증한다.

