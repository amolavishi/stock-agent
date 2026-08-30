# V2.1 → V2.2 Patch Report

## 결론

- 열린 P0: **0**
- 열린 P1: **0**
- Production Stock Agent 코드 구현: **없음**
- Architecture target: **v1.1 유지**
- Industry Overlay: **12개 산업 분석 본문 보존; 중복 output 계약만 runtime 호환을 위해 교체**

## 발견 및 수정

### P0 — SCHEMAS/output_schema_registry_v2.json

- Severity: `P0`
- 문제 위치: SCHEMAS/output_schema_registry_v2.json
- 기존 동작: 빈 properties와 additionalProperties=true 중심
- 왜 문제인지: enum·authority·conditional 모순을 차단하지 못함
- Architecture/Rule 위반: Architecture v1.1 strict validation
- 실패 시나리오: Discovery STARTER/BUY 또는 blocked+STARTER가 통과
- 수정 내용: 27개 typed schema, 공통 $defs, nested type/enum/conditional/additionalProperties=false로 교체
- 영향 파일: SCHEMAS/*, VALIDATION/*
- Regression Test: 32 positive PASS, 62 negative rejection PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P0 — SYSTEM/analysis_grounding_contract.md

- Severity: `P0`
- 문제 위치: SYSTEM/analysis_grounding_contract.md
- 기존 동작: Grounding과 leaf가 동시에 최종 JSON schema 소유
- 왜 문제인지: 한 call에 두 root schema가 존재
- Architecture/Rule 위반: One call = one output owner
- 실패 시나리오: 혼합 JSON 또는 repair loop
- 수정 내용: Grounding을 output_schema=null MIXIN으로 변경하고 receipt를 leaf에 투영
- 영향 파일: SYSTEM/*, metadata schema, runtime contract
- Regression Test: 40 leaf composition closure owner=1, grounding count=1
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P0 — WORKFLOW/stock_researcher.md → probability risk capability

- Severity: `P0`
- 문제 위치: WORKFLOW/stock_researcher.md → probability risk capability
- 기존 동작: Deep Research가 후행 risk_engine_results 필수 capability를 요구
- 왜 문제인지: semantic cycle 및 HUNT_ONLY 완주 불가
- Architecture/Rule 위반: Architecture v1.1 funnel/run mode
- 실패 시나리오: Research→Risk→QualifiedPool→Research 역전
- 수정 내용: Research-time directional capability 신설, 기존 capability Execution 전용화
- 영향 파일: WORKFLOW/stock_researcher.md, CAPABILITIES/directional_probability_hypothesis.md, probability_edge_risk_asymmetry.md
- Regression Test: semantic cycle 0, future dependency 0, HUNT execution dependency 0
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — ADVERSARIAL/*, WORKFLOW/final_synthesis_agent.md

- Severity: `P1`
- 문제 위치: ADVERSARIAL/*, WORKFLOW/final_synthesis_agent.md
- 기존 동작: LLM status에서 INSUFFICIENT_EVIDENCE 등 Python GateDecision 문자열 재사용
- 왜 문제인지: 로그·parser namespace 혼선
- Architecture/Rule 위반: LLM/Python authority separation
- 실패 시나리오: Audit recommendation이 GateResult로 오인
- 수정 내용: AuditRecommendation과 FinalRecommendationStatus enum을 분리
- 영향 파일: decision_contract_v2.json, output schema, 5 prompts
- Regression Test: namespace overlap 0
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — WORKFLOW/stock_researcher.md

- Severity: `P1`
- 문제 위치: WORKFLOW/stock_researcher.md
- 기존 동작: capital_prescreen_receipt 하나로 extraction과 Gate receipt 불명확
- 왜 문제인지: Gate PASS prerequisite를 입증 불가
- Architecture/Rule 위반: CapitalPrescreenGate ordering
- 실패 시나리오: LLM extraction만 있어도 Deep Research 실행
- 수정 내용: extraction receipt와 Python gate receipt를 분리하고 Gate receipt 최소 계약 강제
- 영향 파일: stock_researcher.md, runtime contract, schema
- Regression Test: required input producer/stage PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — UTILITIES/missing_evidence_request.md

- Severity: `P1`
- 문제 위치: UTILITIES/missing_evidence_request.md
- 기존 동작: items가 자유 배열
- 왜 문제인지: Python search workflow가 dedupe/routing 불가
- Architecture/Rule 위반: typed workflow boundary
- 실패 시나리오: '최신 SEC 찾아봐' 자유문 통과
- 수정 내용: EvidenceRequestItemV2 필드·enum·date window·priority를 strict 정의
- 영향 파일: missing_evidence_request.md, schema registry
- Regression Test: EvidenceRequestSet positive validation PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — 모든 Prompt frontmatter와 manifest

- Severity: `P1`
- 문제 위치: 모든 Prompt frontmatter와 manifest
- 기존 동작: allowed_run_modes가 manifest에서 자동 보충되고 source 불명확
- 왜 문제인지: 이중 metadata source
- Architecture/Rule 위반: frontmatter/manifest consistency 계약
- 실패 시나리오: frontmatter에는 mode가 없지만 manifest가 두 mode를 허용
- 수정 내용: frontmatter authoritative, manifest generated projection, metadata schema 추가
- 영향 파일: 41 Prompt, prompt_metadata_schema_v2_2.json, manifest
- Regression Test: semantic diff 0, content hash 41/41
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — workflow.adversarial_reviewer composition

- Severity: `P1`
- 문제 위치: workflow.adversarial_reviewer composition
- 기존 동작: workflow leaf가 standard_audit leaf를 compose하여 diamond와 다중 owner 가능
- 왜 문제인지: composer ambiguity
- Architecture/Rule 위반: One call = one output owner
- 실패 시나리오: 같은 call에 Grounding 중복 및 leaf schema owner 2개
- 수정 내용: standard audit를 선행 result dependency로 전환하고 compose는 mixin만 허용
- 영향 파일: adversarial_reviewer.md, runtime contract
- Regression Test: dedupe/owner/grounding tests PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — FinalSynthesisRecommendationV2

- Severity: `P1`
- 문제 위치: FinalSynthesisRecommendationV2
- 기존 동작: status/action/plan과 ADD prerequisite가 문장 규칙
- 왜 문제인지: blocked 상태에도 action 가능
- Architecture/Rule 위반: Investment Rules v2.0 ADD/Freshness
- 실패 시나리오: STALE_INPUT+STARTER 또는 ADD without Evidence
- 수정 내용: if/then 조건으로 blocked-null, ADD receipts/evidence, existing-position scope 강제
- 영향 파일: final_synthesis_agent.md, schema registry
- Regression Test: legacy/blocked/stale/ADD negative tests 전부 rejection
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P0 — 31개 Markdown inline JSON Output Contract

- Severity: `P0`
- 문제 위치: 31개 Markdown inline JSON Output Contract
- 기존 동작: 본문 JSON과 formal schema가 독립적으로 존재하여 30개 구조 drift
- 왜 문제인지: Prompt를 따른 정상 출력이 runtime schema에서 거부될 수 있음
- Architecture/Rule 위반: Formal JSON Schema single Source of Truth
- 실패 시나리오: Market/Portfolio/Industry output이 additionalProperties=false로 reject
- 수정 내용: 모든 leaf의 inline JSON 계약을 제거하고 canonical schema reference + validated fixture link로 단일화
- 영향 파일: 40개 leaf Prompt, validate_contracts.py
- Regression Test: Prompt-body lint 40/40 PASS, inline JSON contract 0
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P0 — PortfolioComparisonResultV2

- Severity: `P0`
- 문제 위치: PortfolioComparisonResultV2
- 기존 동작: alternatives가 generic analysis block
- 왜 문제인지: identity·scope·capital path·rank·EV/R:R·strengthening Evidence가 소실
- Architecture/Rule 위반: deterministic downstream capital comparison
- 실패 시나리오: 서로 다른 대안이 동일 narrative로 축약
- 수정 내용: PortfolioAlternative/PreferredRecommendation/OpportunityCost/CashBias를 typed 구조로 재작성
- 영향 파일: output_schema_registry_v2_2.json, portfolio_reviewer.md
- Regression Test: positive fixture 및 strict schema PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — StageGate 및 Market/Sector provenance

- Severity: `P1`
- 문제 위치: StageGate 및 Market/Sector provenance
- 기존 동작: stage_gate_receipt가 EXECUTION_RISK에 매핑되고 Prescreen/Deep prerequisite가 아님
- 왜 문제인지: 비싼 funnel 이전 eligibility 증명 불가
- Architecture/Rule 위반: Architecture v1.1 Market→Sector→Stock 및 StageGate 순서
- 실패 시나리오: Stage3 후보가 Prescreen/Deep Research로 진입
- 수정 내용: MarketContextGate/SectorGate/StageGate 전용 receipt와 Discovery-before-Prescreen 순서를 강제
- 영향 파일: runtime contract, sector/scout/prescreen/research metadata
- Regression Test: gate provenance/sequencing PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — FailurePath downstream projection

- Severity: `P1`
- 문제 위치: FailurePath downstream projection
- 기존 동작: Research 일부 category 요구가 capability/audit/final에서 자유형으로 약화
- 왜 문제인지: 동일 causal risk의 문구 변형이 독립 경로로 통과
- Architecture/Rule 위반: 최소 3개 독립 failure path end-to-end
- 실패 시나리오: 동일 scenario/causal path에 category label만 교체
- 수정 내용: 공통 $defs/FailurePathV2를 Research·Capability·Audit·Final에 적용하고 category 및 causal-pair uniqueness를 의미 검증
- 영향 파일: schema registry, validator, fixtures
- Regression Test: fewer-than-3/duplicate-category/cosmetic-variation/downstream-loss rejection PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — StarterPlanV2 / PlannedAddV2

- Severity: `P1`
- 문제 위치: StarterPlanV2 / PlannedAddV2
- 기존 동작: STARTER가 양의 최초 size만 있으면 통과
- 왜 문제인지: 사후 재량 ADD와 stop/대응 누락 가능
- Architecture/Rule 위반: Investment Rules v2.0 STARTER→ADD→FULL
- 실패 시나리오: Add trigger 없이 소량 진입 후 물타기
- 수정 내용: full starter/maximum/stop/holding/breakout/pullback 및 ex-ante planned add를 typed하고 post-add maximum을 의미 검증
- 영향 파일: Final schema, prompt, validator fixtures
- Regression Test: missing-plan/response/holding/time-stop/trigger 및 over-maximum rejection PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — PortfolioComparisonResultV2 cross-field semantics

- Severity: `P1`
- 문제 위치: PortfolioComparisonResultV2 cross-field semantics
- 기존 동작: typed field 사이의 모순과 preferred/rank 불일치 허용
- 왜 문제인지: FinalAllocationGate에 모순된 proposal 전달
- Architecture/Rule 위반: capital allocation semantic integrity
- 실패 시나리오: CASH+NEW_STARTER, 중복 rank, 없는 preferred row
- 수정 내용: discriminated union + snapshot/rank/asset-path/preferred semantic validator 적용
- 영향 파일: Portfolio schema, validator fixtures
- Regression Test: 11개 portfolio coherence negative rejection PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — ADD receipt lineage

- Severity: `P1`
- 문제 위치: ADD receipt lineage
- 기존 동작: receipt 존재만 확인하고 type/subject/trigger/evidence chain이 느슨함
- 왜 문제인지: Evidence Averaging Up 불변식을 증명하지 못함
- Architecture/Rule 위반: Investment Rules v2.0 ADD
- 실패 시나리오: 다른 종목 receipt 또는 disjoint evidence로 ADD
- 수정 내용: 4개 전용 V2 receipt와 target/trigger/evidence subset 의미 검증을 적용
- 영향 파일: Final schema, runtime contract, validator fixtures
- Regression Test: wrong-type/subject/trigger/disjoint evidence rejection 및 valid subset PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P2 — MarketExecutionGateReceipt

- Severity: `P2`
- 문제 위치: MarketExecutionGateReceipt
- 기존 동작: PASS_WITH_PARTIAL 및 incomplete passing 가능
- 왜 문제인지: Context와 Execution gate 의미가 혼합
- Architecture/Rule 위반: 자본투입 직전 fail-closed market verification
- 실패 시나리오: 핵심 시장 input 누락 상태에서 PASS
- 수정 내용: passing enum을 PASS/PASS_WITH_CONSTRAINTS로 제한하고 core_input_complete=true const 적용
- 영향 파일: MarketExecutionGateReceipt, runtime contract, fixtures
- Regression Test: partial pass 및 incomplete passing 3건 rejection PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — StarterPlanV2 arithmetic coherence

- Severity: `P1`
- 문제 위치: StarterPlanV2 arithmetic coherence
- 기존 동작: Starter/resulting/maximum의 존재는 강제하지만 상호 수량·비중 산술이 일부 열림
- 왜 문제인지: 경제적으로 불가능한 Action Card가 machine-valid
- Architecture/Rule 위반: Starter와 Planned ADD를 포함하는 Maximum Position 상한
- 실패 시나리오: starter가 maximum보다 크거나 starter+add가 resulting cap 초과
- 수정 내용: starter <= resulting <= maximum 및 starter+planned add <= resulting을 semantic validator로 강제
- 영향 파일: validate_contracts.py, STARTER fixtures
- Regression Test: 5개 독립 arithmetic mutation rejection PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — FULL/TRIM/EXIT PositionSnapshot identity

- Severity: `P1`
- 문제 위치: FULL/TRIM/EXIT PositionSnapshot identity
- 기존 동작: ADD 외 existing-position action의 receipt subject 미검증
- 왜 문제인지: 다른 종목 snapshot으로 action 가능
- Architecture/Rule 위반: existing-position identity lineage
- 실패 시나리오: A 종목 EXIT에 B 종목 receipt 첨부
- 수정 내용: ADD/FULL/TRIM/EXIT 공통 subject_id==target_security_id 및 position_exists=true 검증
- 영향 파일: validate_contracts.py, Final fixtures
- Regression Test: FULL/TRIM/EXIT mismatch 3건 rejection PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P1 — Structural Bear 분리 assertion

- Severity: `P1`
- 문제 위치: Structural Bear 분리 assertion
- 기존 동작: boolean false 허용
- 왜 문제인지: Structural Bear와 Execution Stop 분리 Hard Rule을 부정 가능
- Architecture/Rule 위반: Structural Bear != Execution Stop
- 실패 시나리오: assertion=false가 schema PASS
- 수정 내용: structural_bear_is_not_execution_stop를 const true로 고정
- 영향 파일: FailureScenarioInvalidationAssessmentV2
- Regression Test: false mutation rejection PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P2 — 1~8주 holding horizon runtime binding

- Severity: `P2`
- 문제 위치: 1~8주 holding horizon runtime binding
- 기존 동작: Prompt schema가 active rule horizon을 알 수 없음
- 왜 문제인지: 999일 plan도 구조 검증만 통과
- Architecture/Rule 위반: EffectiveRuleSet 기반 전략 horizon
- 실패 시나리오: override 없이 56일 초과
- 수정 내용: Python RiskEngine/FinalAllocationGate 소유 runtime policy와 default 56일/active override fixture를 추가
- 영향 파일: runtime contract, holding_horizon_policy_cases.json, validator
- Regression Test: 4/4 runtime policy cases PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함
### P2 — ADVERSARIAL 3종

- Severity: `P2`
- 문제 위치: ADVERSARIAL 3종
- 기존 동작: 본문 공격축이 거의 동일
- 왜 문제인지: 전문 역할의 정보가치 저하
- Architecture/Rule 위반: repair differentiation requirement
- 실패 시나리오: 세 call이 같은 finding 반복
- 수정 내용: Standard/Contradiction/Thesis causal-chain 공격축을 분리
- 영향 파일: ADVERSARIAL/standard_audit.md, evidence_contradiction_audit.md, strong_thesis_destruction.md
- Regression Test: prompt classification 및 schema compatibility PASS
- Residual Risk: 낮음; runtime 구현체가 계약을 준수해야 함

## 40개 기존 Prompt + 신규 Prompt 분류

| prompt_id | 분류 | 설명 |
|---|---|---|
| `adversarial.consensus_revalidation` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `adversarial.evidence_contradiction_audit` | REWRITE | P0/P1 runtime contract를 직접 수정 |
| `adversarial.standard_audit` | REWRITE | P0/P1 runtime contract를 직접 수정 |
| `adversarial.strong_thesis_destruction` | REWRITE | P0/P1 runtime contract를 직접 수정 |
| `capability.accounting_quality` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `capability.capital_structure_forensics` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `capability.catalyst_expectation_gap` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `capability.contract_backlog_quality` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `capability.directional_probability_hypothesis` | NEW | Research-time probability hypothesis; Risk Engine dependency 없음 |
| `capability.entry_readiness_execution_structure` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `capability.event_probability_ev` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `capability.failure_scenarios_invalidation` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `capability.fundamental_change_quality` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `capability.probability_edge_risk_asymmetry` | REWRITE | P0/P1 runtime contract를 직접 수정 |
| `capability.reverse_valuation` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `industry.ai_data_center_infrastructure` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.ai_data_services` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.biotech` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.crypto_linked_equities` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.defense_space` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.e_and_p` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.nuclear_critical_minerals` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.optical_networking_broadband` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.quantum` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.saas_ai_software` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.semiconductor_advanced_packaging` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `industry.shipping` | PATCH | 산업 분석·KPI·failure-path 본문 보존; 중복 inline Output Contract만 canonical schema 참조로 교체 |
| `system.analysis_grounding` | REWRITE | P0/P1 runtime contract를 직접 수정 |
| `utility.capital_structure_prescreen` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `utility.claim_evidence_mapping` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `utility.evidence_extraction` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `utility.freshness_delta_review` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `utility.missing_evidence_request` | REWRITE | P0/P1 runtime contract를 직접 수정 |
| `utility.sec_extraction` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `workflow.adversarial_reviewer` | REWRITE | P0/P1 runtime contract를 직접 수정 |
| `workflow.final_synthesis_agent` | REWRITE | P0/P1 runtime contract를 직접 수정 |
| `workflow.market_analyst` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `workflow.portfolio_reviewer` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `workflow.sector_analyst` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |
| `workflow.stock_researcher` | REWRITE | P0/P1 runtime contract를 직접 수정 |
| `workflow.stock_scout` | PATCH | 분석 본문 보존, metadata/schema/runtime 호환 수정 |

요약: 기존 40개 중 REWRITE 9, PATCH 31, MERGE 0, DELETE 0. 신규 1개는 NEW다. Industry 12종의 산업 분석/KPI/failure path는 보존했고, 감사에서 확인된 중복 inline JSON Output Contract만 canonical schema 참조로 교체했다.

## 검증 결과

- typed leaf schema: 27/27
- positive schema: 32/32 PASS
- negative schema: 62/62 rejection PASS
- holding-horizon runtime policy: 4/4 PASS
- Prompt-body/schema lint: 40/40 PASS, inline JSON Output Contract 0
- Gate provenance/sequencing: PASS
- Prompt ID: 41/41 unique
- dependency reference: 100% valid
- semantic stage cycle: 0
- future-stage required dependency: 0
- HUNT_ONLY execution-only dependency: 0
- manifest semantic diff: 0
- content hash: 41/41
- composition output owner: 모든 leaf 정확히 1
- P0/P1 residual: 0


