---
prompt_id: capability.catalyst_expectation_gap
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
- catalyst-expectation-gap
required_rule_packs:
- '00'
- '01'
- '04'
- 09
required_inputs:
- effective_rule_pack
- research_context
- catalyst_evidence
optional_inputs:
- market_expectation_data
- consensus_snapshot
- estimate_revision_snapshot
- implied_move
- short_interest
- historical_event_reactions
output_schema: CatalystExpectationGapAssessmentV2
incompatible_with: []
recommended_model_class: STRONG
context_policy: task-minimal context; only relevant evidence
side_effects: none
authoritative_decision: false
description: 왜 지금인가·왜 아직 안 올랐는가·무엇이 시장을 깨우는가를 하나의 재평가 메커니즘으로 검증한다.
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
output_example_case: positive_CatalystExpectationGapAssessmentV2
---

# Catalyst & Expectation Gap v2

## Objective
`실제 미래 변화 - 현재 시장 기대`를 측정하고 1~8주 내 기대 재조정 경로를 구조화한다.

## Analysis Tasks
1. 공식 consensus, revenue/EPS revision, target/rating revision, whisper, implied move, short interest, historical reaction, reverse valuation 중 사용 가능한 기대 측정치를 분리한다.
2. 애널리스트 의견은 진실의 source가 아니라 **시장 기대 측정도구**로만 사용한다.
3. catalyst마다 type, timing, probability rationale, economic size, per-share transmission, priced-in degree, binding status, confirmation metric, downside surprise path를 분석한다.
4. `why_now`, `why_not_priced`, `wakeup_event`를 독립 항목으로 답한다.
5. Fundamental Breakout과 Price-Only Breakout을 구분하고, 전자는 fair value/EV 재계산을 요구한다.
6. event 전이라는 이유만으로 자동 0주를 암시하지 않는다. 실제 실행 판단은 Risk/Portfolio/Python이 한다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#CatalystExpectationGapAssessmentV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_CatalystExpectationGapAssessmentV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `CatalystExpectationGapAssessmentV2`에 대해 실제 검증한다.

