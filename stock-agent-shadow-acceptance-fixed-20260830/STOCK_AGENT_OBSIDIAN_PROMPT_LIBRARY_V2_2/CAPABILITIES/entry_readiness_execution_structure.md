---
prompt_id: capability.entry_readiness_execution_structure
version: 2.2.0
schema_version: prompt-meta-2.2
layer: CAPABILITY
category: analysis
role: entry_readiness_analyst
compatible_agents:
- stock_researcher
- portfolio_reviewer
- final_synthesis_agent
task_tags:
- entry-readiness
- technical-structure
- execution
required_rule_packs:
- '00'
- '03'
- '06'
- '07'
- 09
required_inputs:
- effective_rule_pack
- run_mode
- fresh_price_snapshot
- stage_assessment
- stage_gate_receipt
- research_result
- risk_metrics
- market_execution_gate_receipt
optional_inputs:
- catalyst_calendar
- technical_levels
- breakout_assessment
- opportunity_cost_inputs
- fresh_evidence_delta
output_schema: EntryReadinessExecutionAssessmentV2
incompatible_with:
- workflow.stock_scout
recommended_model_class: MID
context_policy: fresh price/stage/risk packet only
side_effects: none
authoritative_decision: false
description: Execution Review 전용 entry readiness 분석. StageGate/MarketExecutionGate/Risk Engine 결과를 소비하며 자체 GateDecision은 만들지
  않는다.
compose_with:
- system.analysis_grounding
requires_results: []
requires_capabilities: []
allowed_run_modes:
- HUNT_AND_EXECUTION_REVIEW
prompt_kind: LEAF
stage: EXECUTION_RISK
conditional_dependencies: {}
output_example_case: positive_EntryReadinessExecutionAssessmentV2
---

# Entry Readiness & Execution Structure v2.2

## Objective
`HUNT_AND_EXECUTION_REVIEW`에서 좋은 thesis와 좋은 entry를 분리한다. Python StageGate/MarketExecutionGate/Risk Engine 결과를 소비하는 **비권위 readiness recommendation**이다.

## Preconditions
- `run_mode == HUNT_AND_EXECUTION_REVIEW`
- fresh price snapshot
- StageAssessment + StageGate receipt
- MarketExecutionGate receipt
- risk metrics

필수 입력이 없으면 `INCOMPLETE`이며 `WATCH/NO_TRADE`로 의미변환하지 않는다.

## Analysis Tasks
1. StageGate 결과를 재정의하지 않고 가격/RS/volume 구조만 해석한다.
2. `PRICE_ONLY_BREAKOUT`이면 FOMO 추격 위험을 표시한다.
3. `FUNDAMENTAL_BREAKOUT`이면 fair-value/EV 변화와 가격상승폭을 비교해 자동 탈락하지 않는다.
4. Starter Zone/Breakout/Pullback 구조는 제안할 수 있으나 shares/capital %는 Python Risk Engine 값만 인용한다.
5. ADD readiness는 기존 position + 사전 trigger + `fresh_evidence_delta.add_evidence_strengthened=true`가 있을 때만 추천한다.
6. Execution Stop, Thesis Stop, Structural Bear, Worst Plausible Gap, max holding/time stop을 분리한다.
7. 기다림의 Opportunity Cost를 평가한다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#EntryReadinessExecutionAssessmentV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_EntryReadinessExecutionAssessmentV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `EntryReadinessExecutionAssessmentV2`에 대해 실제 검증한다.

