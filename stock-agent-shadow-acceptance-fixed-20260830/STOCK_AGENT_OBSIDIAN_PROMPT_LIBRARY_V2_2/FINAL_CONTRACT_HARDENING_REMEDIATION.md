# v2.2 Final Contract Hardening Remediation

## 판정

`STOCK_AGENT_PROMPT_LIBRARY_V2_2_FINAL_REMEDIATION_REQUIREMENTS.md`의 P1 4건과 P2 1건을 모두 계약 수준에서 수정했다.

- P0 open: **0**
- P1 open: **0**
- P2 open: **0**
- Positive contract: **32/32 PASS**
- Negative contract: **62/62 rejection PASS**
- Holding-horizon runtime policy: **4/4 PASS**
- 전체 validator: **PASS / 0 failures**

## 요구사항별 조치

| 요구사항 | 구현 | 실제 검증 |
|---|---|---|
| STARTER full plan | `StarterPlanV2`와 `PlannedAddV2`; stop, holding/time-stop, breakout/pullback, post-add cap | 누락·trigger 부재·cap 초과·holding 역전 FAIL |
| Failure path 보존 | 공통 `$defs/FailurePathV2`를 Research, Failure Capability, Adversarial Audit, Final에서 참조 | 2개 경로·중복 category·동일 causal pair·downstream 소실 FAIL |
| Portfolio coherence | CASH/SECURITY discriminated union과 snapshot/rank/asset-path/preferred semantic rules | path/scope/evidence/position/preferred/rank/row/snapshot 모순 FAIL |
| ADD lineage | `PositionSnapshotReceiptV2`, `PriorAddTriggerReceiptV2`, `FreshnessDeltaReceiptV2`, `StrengtheningEvidenceReceiptV2` | type swap·subject·trigger·evidence mismatch FAIL, valid subset PASS |
| MarketExecution boundary | passing decision은 PASS/PASS_WITH_CONSTRAINTS, `core_input_complete=true` | PASS_WITH_PARTIAL 및 incomplete passing FAIL |
| STARTER arithmetic coherence | starter/resulting/maximum 및 planned-add 합계 교차검증 | 5개 arithmetic mutation FAIL |
| Existing-position identity | ADD/FULL/TRIM/EXIT position receipt subject 공통검증 | 세 action의 mismatched subject FAIL |
| Structural Bear 분리 | assertion을 `const true`로 고정 | false mutation FAIL |
| Holding horizon binding | default 56일 + active EffectiveRuleSet override runtime policy | 4/4 policy cases PASS |

## 보존된 불변식

- Architecture target v1.1 및 Investment Rules v2.0
- DiscoveryDecision/ExecutionAction enum
- Python FinalAllocationGate 단일 authoritative writer와 Fresh Money 0..1
- HUNT_ONLY execution dependency 0 및 future dependency 0
- Market Context/Execution 분리, StageGate Python ownership, Prescreen ordering
- Industry Overlay 12개 분석/KPI/failure-path 본문
- Production Stock Agent 코드 미구현

## 최종 선언

`PROMPT_LIBRARY_READY`


