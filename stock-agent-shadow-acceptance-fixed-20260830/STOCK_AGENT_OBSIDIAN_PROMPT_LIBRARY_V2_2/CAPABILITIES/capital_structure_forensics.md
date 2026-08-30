---
prompt_id: capability.capital_structure_forensics
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
- capital-structure-forensics
required_rule_packs:
- '00'
- '05'
- 09
required_inputs:
- effective_rule_pack
- capital_structure_snapshot
- sec_evidence
optional_inputs:
- company_facts
- capital_prescreen_result
- filing_cutoff_receipt
output_schema: CapitalStructureForensicAssessmentV2
incompatible_with: []
recommended_model_class: STRONG
context_policy: task-minimal context; only relevant evidence
side_effects: none
authoritative_decision: false
description: Python-normalized CapitalStructureSnapshot과 SEC evidence를 해석하는 Full SEC Forensic. CapitalStructureGate 권위는 Python에
  유지.
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
output_example_case: positive_CapitalStructureForensicAssessmentV2
---

# Capital Structure Forensics v2.2

## Objective
Deep Research 뒤 최종후보에 대해 Python-normalized `CapitalStructureSnapshot`과 최신 SEC evidence를 사용해 희석·오버행·자금조달 필요성의 경제적 의미를 해석한다. `CapitalStructureGate`는 Python이 소유한다.

## Analysis Tasks
1. filing cutoff/accession receipt가 있으면 최신 SEC index와 일치하는지 확인한다.
2. ATM, shelf, warrants, convertibles, preferred, PIPE, earn-out, S-8, Form 144, insider activity를 evidence와 연결한다.
3. remaining capacity, exercise/conversion terms, potential shares, maturity/timing을 구분한다.
4. cash runway, cash burn, debt maturity, covenant와 결합해 financing need/horizon을 해석한다.
5. shelf 존재만으로 toxic으로 만들지 않는다. 반복 ATM·toxic convertible·imminent financing 등은 숨기지 않는다.
6. Basic/Diluted shares와 potential dilution의 per-share economics/surge elasticity 영향을 설명한다.
7. `UNKNOWN` prescreen 항목이 Full Forensic에서 실제 해소됐는지 receipt를 남긴다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#CapitalStructureForensicAssessmentV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_CapitalStructureForensicAssessmentV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `CapitalStructureForensicAssessmentV2`에 대해 실제 검증한다.

