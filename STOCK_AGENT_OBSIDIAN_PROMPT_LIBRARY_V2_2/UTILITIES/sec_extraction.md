---
prompt_id: utility.sec_extraction
version: 2.2.0
schema_version: prompt-meta-2.2
layer: UTILITY
category: utility
role: utility_worker
compatible_agents:
- market_analyst
- sector_analyst
- stock_researcher
- forensic_analyst
- adversarial_reviewer
task_tags:
- sec-extraction
required_rule_packs:
- 09
required_inputs:
- effective_rule_pack
- sec_document
- sec_targets
optional_inputs: []
output_schema: SECExtractionResultV2
incompatible_with: []
recommended_model_class: MID
context_policy: small targeted payload
side_effects: none
authoritative_decision: false
description: SEC filing에서 자본구조·재무·계약 관련 사실을 구조화한다.
compose_with:
- system.analysis_grounding
requires_results: []
requires_capabilities: []
prompt_kind: LEAF
stage: FULL_SEC
allowed_run_modes:
- HUNT_ONLY
- HUNT_AND_EXECUTION_REVIEW
conditional_dependencies: {}
output_example_case: positive_SECExtractionResultV2
---

# SEC Extraction v2

## Objective
SEC 원문에서 자본구조·주식수·자금조달·회계·계약 사실을 **해석 전 facts**로 구조화한다.

## Boundary
사실 추출 단계에서 toxic/non-toxic 최종 판정을 만들지 않는다. Full Forensic 또는 Python Gate가 해석한다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#SECExtractionResultV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_SECExtractionResultV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `SECExtractionResultV2`에 대해 실제 검증한다.

