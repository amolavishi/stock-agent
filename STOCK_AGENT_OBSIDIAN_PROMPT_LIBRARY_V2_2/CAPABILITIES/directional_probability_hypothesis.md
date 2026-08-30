---
prompt_id: capability.directional_probability_hypothesis
version: 2.2.0
schema_version: prompt-meta-2.2
prompt_kind: LEAF
stage: DEEP_RESEARCH
layer: CAPABILITY
category: analysis
role: directional_probability_analyst
compatible_agents:
- stock_researcher
- adversarial_reviewer
task_tags:
- probability-hypothesis
- scenario-analysis
- research-time
required_rule_packs:
- '00'
- '01'
- '04'
- 09
required_inputs:
- effective_rule_pack
- scenario_evidence
- valuation_scenarios
optional_inputs:
- catalyst_distribution
- failure_scenarios
output_schema: DirectionalProbabilityHypothesisV2
output_example_case: positive_DirectionalProbabilityHypothesisV2
incompatible_with: []
recommended_model_class: STRONG
context_policy: Bear/Base/Bull research evidence only; no Risk Engine or portfolio context
side_effects: none
authoritative_decision: false
description: Risk Engine 없이 Research-time 방향성 확률 가설과 swing factor를 구조화한다.
compose_with:
- system.analysis_grounding
requires_results: []
requires_capabilities: []
conditional_dependencies: {}
allowed_run_modes:
- HUNT_ONLY
- HUNT_AND_EXECUTION_REVIEW
---

# Directional Probability Hypothesis v2.2

## Role
Deep Research 단계에서 Risk Engine 없이 Bear/Base/Bull 가정과 상승·균형·하락 방향성 가설을 만든다.

## Analysis Tasks
1. 각 시나리오의 핵심 가정, 촉매, 실패경로, valuation evidence를 연결한다.
2. `UPSIDE_HYPOTHESIS|BALANCED_HYPOTHESIS|DOWNSIDE_HYPOTHESIS|UNKNOWN` 중 하나를 제안한다.
3. 확률을 크게 바꿀 수 있는 관측 가능한 swing factor를 제시한다.
4. 가짜 소수점 확률, Execution R:R, position size, authoritative action을 만들지 않는다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#DirectionalProbabilityHypothesisV2`다. 중복 inline JSON 계약은 두지 않는다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_DirectionalProbabilityHypothesisV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `DirectionalProbabilityHypothesisV2`에 대해 실제 검증한다.

