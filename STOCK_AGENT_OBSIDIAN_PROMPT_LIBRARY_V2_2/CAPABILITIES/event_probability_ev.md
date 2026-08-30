---
prompt_id: capability.event_probability_ev
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
- event-probability-ev
required_rule_packs:
- '00'
- '04'
- '06'
- 09
required_inputs:
- effective_rule_pack
- event_terms
- event_evidence
- python_ev_calculation
optional_inputs:
- legal_regulatory_evidence
output_schema: EventProbabilityAssessmentV2
incompatible_with: []
recommended_model_class: STRONG
context_policy: task-minimal context; only relevant evidence
side_effects: none
authoritative_decision: false
description: M&A·규제·임상·정부계약 등 불연속 이벤트의 확률·payoff·break value를 구조화한다.
compose_with:
- system.analysis_grounding
requires_results: []
requires_capabilities: []
prompt_kind: LEAF
stage: EXECUTION_RISK
allowed_run_modes:
- HUNT_AND_EXECUTION_REVIEW
conditional_dependencies: {}
output_example_case: positive_EventProbabilityAssessmentV2
---

# Event Probability & EV v2

## Objective
M&A·규제·임상·정부계약·법원판결 등 불연속 이벤트의 mutually-exclusive states와 probability rationale를 만들고 Python EV 계산을 해석한다.

## Analysis Tasks
1. event state를 상호배타적으로 정의한다.
2. 각 state의 evidence, probability range/rationale, payoff/break-value input, timing을 분리한다.
3. regulatory, financing, closing condition, litigation, funding, technical success risk를 독립적으로 평가한다.
4. `python_ev_calculation`을 재계산하지 않고 결과와 민감도를 해석한다.
5. Worst Plausible Gap은 event-risk input으로 전달하며 technical stop이 gap을 통제한다고 주장하지 않는다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#EventProbabilityAssessmentV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_EventProbabilityAssessmentV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `EventProbabilityAssessmentV2`에 대해 실제 검증한다.

