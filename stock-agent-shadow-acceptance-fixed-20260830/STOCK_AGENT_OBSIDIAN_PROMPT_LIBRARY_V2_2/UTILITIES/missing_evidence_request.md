---
prompt_id: utility.missing_evidence_request
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
- missing-evidence-request
required_rule_packs:
- 09
required_inputs:
- effective_rule_pack
- issue_ledger
- current_evidence_inventory
optional_inputs: []
output_schema: EvidenceRequestSet
incompatible_with: []
recommended_model_class: CHEAP
context_policy: small targeted payload
side_effects: none
authoritative_decision: false
description: 미해결 issue를 해결하기 위한 가장 작은 directed EvidenceRequest를 생성한다.
compose_with:
- system.analysis_grounding
requires_results: []
requires_capabilities: []
prompt_kind: LEAF
stage: AUDIT
allowed_run_modes:
- HUNT_ONLY
- HUNT_AND_EXECUTION_REVIEW
conditional_dependencies: {}
output_example_case: positive_EvidenceRequestSet
---

# Missing Evidence Request v2.2

## Role
미해결 issue를 Python search workflow가 기계적으로 소비할 수 있는 최소 `EvidenceRequestItemV2` proposal로 변환한다.

## Item Contract
각 item은 다음을 포함한다.
- `request_key`
- optional `target_claim_id`, `target_issue_id`
- `subject_id`
- `evidence_type`
- `source_classes[]`
- `preferred_document_types[]`
- `query`
- optional `date_window`
- `freshness_requirement`
- `reason`
- `priority`
- optional `duplicate_check_key`

## Runtime Boundary
LLM은 proposal만 작성한다. Python이 validate, dedupe, budget, provider routing, WorkItem을 소유한다. Prompt는 web fetch나 network 권한을 직접 요구하지 않는다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#EvidenceRequestSet`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_EvidenceRequestSet`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `EvidenceRequestSet`에 대해 실제 검증한다.

