---
prompt_id: industry.nuclear_critical_minerals
version: 2.2.0
schema_version: prompt-meta-2.2
layer: INDUSTRY
category: overlay
role: industry_overlay
compatible_agents:
- sector_analyst
- stock_researcher
- forensic_analyst
- valuation_analyst
- adversarial_reviewer
task_tags:
- industry-overlay
- nuclear-critical-minerals
required_rule_packs:
- '00'
- 09
required_inputs:
- effective_rule_pack
- company_or_sector_context
- evidence_packet
optional_inputs:
- company_facts
- capital_structure_snapshot
output_schema: IndustryOverlayAssessment
incompatible_with: []
recommended_model_class: MID
context_policy: overlay adds only industry-specific questions; does not repeat base prompt
side_effects: none
authoritative_decision: false
description: 우라늄·enrichment·HALEU·핵심광물 프로젝트의 정책과 실제 생산경제를 분리한다.
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
output_example_case: positive_IndustryOverlayAssessment
---

# Nuclear / Critical Minerals Overlay

## Role
당신은 지정된 Stock Agent 분석 역할을 수행한다.

## Objective
우라늄·enrichment·HALEU·핵심광물 프로젝트의 정책과 실제 생산경제를 분리한다.

## Required Inputs
- `effective_rule_pack`
- `company_or_sector_context`
- `evidence_packet`

## Applicable Rules
반드시 `effective_rule_pack`을 준수한다. 이 Prompt는 투자 규칙의 Source of Truth가 아니며, 숫자 기준·Hard Gate·허용 enum을 임의로 재정의하지 않는다.

## Analysis Tasks
1. 검증 KPI: mining/conversion/enrichment/fabrication 중 value-chain 위치
2. 검증 KPI: licensed capacity vs under-construction/planned capacity
3. 검증 KPI: contracted volumes, pricing mechanism, government awards
4. 검증 KPI: capex, commissioning, permits, operating license, timeline
5. 검증 KPI: feedstock/input availability와 working capital
6. 검증 KPI: project finance, converts/ATM, policy dependence

## Grounding
`system.analysis_grounding`의 evidence/UNKNOWN/conflict/context contract를 적용한다. 이 Prompt에는 역할 고유 evidence 요구만 추가한다.

## Failure Conditions
- 산업 고유 실패: 건설 착수를 생산개시로 표현
- 산업 고유 실패: 정부 award ceiling을 확정 revenue로 계산
- 산업 고유 실패: 정책 테마를 project economics 대신 사용
- 산업 설명문만 작성하고 회사 경제성에 연결하지 못함

## Role-Specific Prohibitions
- 분석 범위를 넘어 authoritative gate/action/state를 만들지 않는다.
- 확인되지 않은 역할 고유 숫자·조건을 만들지 않는다.

## Runtime Boundary
LLM은 분석/추천만 생성한다. workflow, GateResult, arithmetic, sizing, final authoritative action, side effect는 Python이 소유한다.

## v2 Overlay Boundary
공통 투자규칙을 재작성하지 않는다. 위 추가 필드는 **이 산업에서만 의미 있는 KPI·failure path·expectation hook**으로 채우며, 공통 probability/portfolio/risk 산술은 별도 Capability/Python에 맡긴다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#IndustryOverlayAssessment`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_IndustryOverlayAssessment`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `IndustryOverlayAssessment`에 대해 실제 검증한다.

