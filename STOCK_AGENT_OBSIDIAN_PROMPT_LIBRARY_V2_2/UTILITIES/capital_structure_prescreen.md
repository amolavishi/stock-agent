---
prompt_id: utility.capital_structure_prescreen
version: 2.2.0
schema_version: prompt-meta-2.2
layer: UTILITY
category: extraction
role: capital_structure_prescreen_extractor
compatible_agents:
- stock_scout
- stock_researcher
task_tags:
- capital-structure
- cheap-prescreen
- sec
- dilution
required_rule_packs:
- '00'
- '05'
- 09
required_inputs:
- effective_rule_pack
- security_identity
- cheap_sec_packet
- stage_gate_receipt
optional_inputs:
- company_facts
- cash_runway_snapshot
output_schema: CapitalStructurePrescreenResultV2
recommended_model_class: CHEAP
context_policy: latest identity + minimum SEC/capital packet only
side_effects: none
authoritative_decision: false
description: Python StageGate 허용 receipt 이후 Cheap Capital Prescreen tri-state extraction을 수행한다.
compose_with:
- system.analysis_grounding
requires_results: []
requires_capabilities: []
prompt_kind: LEAF
stage: PRESCREEN
allowed_run_modes:
- HUNT_ONLY
- HUNT_AND_EXECUTION_REVIEW
conditional_dependencies: {}
incompatible_with: []
output_example_case: positive_CapitalStructurePrescreenResultV2
---

# Cheap Capital Structure Prescreen Extraction v2.2

## Objective
비싼 Deep Research 전에 최신 SEC/기초재무 packet에서 **저비용 구조화 사실**을 추출한다. 이 Prompt는 `CapitalPrescreenGate`가 아니며 `EXCLUDE/BLOCK/PASS`를 결정하지 않는다.

## Minimum Checks
- active ATM
- large shelf + financing need 조합
- toxic convertible
- material warrant overhang
- imminent financing
- severe cash runway problem
- identity/SEC availability

## Architecture Boundary
1. 각 항목은 `TRUE|FALSE|UNKNOWN` 또는 structured terms로 반환한다.
2. Shelf 존재 자체를 toxic으로 판정하지 않는다.
3. 정보 부족을 `CLEAR`로 만들지 않는다.
4. `UNKNOWN`은 Full SEC Forensic 필요성을 높일 수 있으나 hard gate 결과가 아니다.
5. Python `CapitalPrescreenGate`가 rule pack과 typed facts를 사용해 downstream eligibility를 결정한다.
6. Full SEC Forensic은 Deep Research 뒤 최종후보에서 별도로 수행한다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#CapitalStructurePrescreenResultV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_CapitalStructurePrescreenResultV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `CapitalStructurePrescreenResultV2`에 대해 실제 검증한다.

