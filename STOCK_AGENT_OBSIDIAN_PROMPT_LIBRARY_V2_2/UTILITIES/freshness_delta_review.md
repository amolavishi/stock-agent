---
prompt_id: utility.freshness_delta_review
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
- freshness-delta-review
required_rule_packs:
- 09
required_inputs:
- effective_rule_pack
- prior_analysis
- fresh_evidence
- must_review_evidence_ids
optional_inputs:
- prior_dependency_receipt
- new_evidence_snapshot_receipt
output_schema: FreshnessDeltaReviewResultV2
incompatible_with: []
recommended_model_class: MID
context_policy: small targeted payload
side_effects: none
authoritative_decision: false
description: 새 Evidence의 의미 delta와 ADD strengthening 여부를 분석. 실제 stale/invalidation/commit fence는 Python lineage/repository가
  결정.
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
output_example_case: positive_FreshnessDeltaReviewResultV2
---

# Freshness Delta Review v2.2

## Objective
새 Evidence가 기존 thesis·risk·capital structure·probability edge를 실제로 바꿨는지 **delta 중심**으로 재검증한다.

## Architecture Boundary
- 이 Prompt는 어떤 DB row도 `STALE/INVALIDATED`로 바꾸지 않는다.
- active dependency hash/epoch와 `STALE_ON_ARRIVAL` 판정은 Python commit-time freshness fence가 소유한다.
- LLM은 **잠재 영향 domain**과 Evidence strengthening 의미만 반환한다.

## Analysis Tasks
1. old claim/evidence와 new evidence를 claim 단위로 diff한다.
2. `UNCHANGED|STRENGTHENED|WEAKENED|INVALIDATED|NEW_CONFLICT`를 구분한다.
3. ADD 검토라면 새 Evidence가 기존 thesis dependency와 구별되는 **실질적 strengthening evidence**인지 명시한다. 가격변화 자체는 강화 Evidence가 아니다.
4. critical Evidence 변화가 Research/Audit/Risk/Recommendation 중 어디에 영향을 줄 수 있는지 advisory list로 표시한다.
5. evidence snapshot/hash receipt는 제공된 값을 그대로 반환하며 임의 생성하지 않는다.

## Formal Output Contract
Machine Source of Truth는 `SCHEMAS/output_schema_registry_v2_2.json#FreshnessDeltaReviewResultV2`다. 중복 inline JSON 계약은 두 번째 Source가 되므로 두지 않는다. Runtime은 이 formal schema를 call에 주입하고 strict validation한다.

## Machine-Validated Example
정규 positive example은 `VALIDATION/schema_positive_cases.json`의 `positive_FreshnessDeltaReviewResultV2`다. `VALIDATION/validate_contracts.py`가 이 fixture를 `FreshnessDeltaReviewResultV2`에 대해 실제 검증한다.

