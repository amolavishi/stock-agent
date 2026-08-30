---
prompt_id: capability.fundamental_change_quality
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
- fundamental-change-quality
required_rule_packs:
- '00'
- '01'
- 09
required_inputs:
- effective_rule_pack
- company_facts
- earnings_evidence
optional_inputs:
- industry_overlay
output_schema: FundamentalChangeQualityAssessmentV2
incompatible_with: []
recommended_model_class: STRONG
context_policy: task-minimal context; only relevant evidence
side_effects: none
authoritative_decision: false
description: 기업 실적·수요·수익성 변화의 질과 지속성을 분해한다.
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
output_example_case: positive_FundamentalChangeQualityAssessmentV2
---

# Fundamental Change Quality v2

## Objective
최근 실적·수요·수익성 변화가 진짜 organic acceleration인지, acquisition/가격/회계 one-off인지 분해한다.

## Analysis Tasks
1. 3~6개 분기의 revenue growth, growth acceleration, guide revision, margin, EBITDA/FCF 방향을 비교한다.
2. Organic / M&A / price / volume / mix / FX / accounting one-off를 분리한다.
3. 고객수·usage·units·bookings·backlog/RPO·book-to-bill 등 선행 KPI가 reported revenue를 지지하는지 확인한다.
4. concentration, recurring quality, gross-to-FCF conversion, working-capital burden을 평가한다.
5. 새 정보가 가격상승과 동반되었으면 Fundamental Breakout인지 판단할 입력을 만든다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#FundamentalChangeQualityAssessmentV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_FundamentalChangeQualityAssessmentV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `FundamentalChangeQualityAssessmentV2`에 대해 실제 검증한다.

