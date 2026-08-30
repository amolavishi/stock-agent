# Prompt Library Architecture v2.2

## 1. 권위 토폴로지

```text
Investment Rules v2.0 → Python Rule Resolver → immutable EffectiveRuleSet
                                          +
frontmatter → generated Manifest → Prompt Composer → ContextManifest
                                          ↓
                                  LLM leaf Agent call
                                          ↓
                              analysis / recommendation
                                          ↓
          Python Stage/Domain Gates → Risk Engine → FinalAllocationGate
```

Prompt Library는 SQLite, WorkItem lease, reservation FSM, transaction, OS ACL, Python Gate, authoritative FinalAction을 구현하지 않는다.

## 2. One Call = One Output Schema Owner

`system.analysis_grounding`은 `MIXIN`이며 output schema가 `null`이다. 모든 나머지 호출 단위는 `LEAF`이고 schema owner다. Composer는 graph expand → prompt_id dedupe → deterministic topological order → mixin first → exactly one leaf owner → canonical composition hash 순서로 동작해야 한다.

## 3. Schema Source of Truth

Formal JSON Schema가 machine Source of Truth다. Registry는 Draft 2020-12를 사용하고 공통 `$defs`를 leaf schema에 주입한 뒤 strict extraction → validate → bounded repair → reject 순서로 처리한다. Authority-sensitive object는 `additionalProperties: false`다.

## 4. Run Mode

### HUNT_ONLY

Market Context → MarketContextGate → Sector → SectorGate → Stock Discovery → StageGate → Prescreen → CapitalPrescreenGate → Deep Research → Full SEC → Audit → `QualifiedCandidatePool`. PortfolioSnapshot, MarketExecution, Risk Engine, FinalAllocation, ExecutionAction, Action Card를 요구하지 않는다. 빈 pool은 `NO_QUALIFIED_CANDIDATE`다.

### HUNT_AND_EXECUTION_REVIEW

Qualified pool 이후 fresh MarketExecution/Portfolio/Risk prerequisite를 사용해 non-authoritative execution recommendation을 만들 수 있다. `NO_TRADE`는 이 mode의 ExecutionAction이며 HUNT_ONLY terminal과 다르다.

## 5. Semantic Stage DAG

```text
INPUT → DISCOVERY(MarketContextGate → SectorGate → Stock Discovery → StageGate)
                    → PRESCREEN(CapitalPrescreenGate) → DEEP_RESEARCH → FULL_SEC → AUDIT
                                                     ↓
                          [Execution mode only] EXECUTION_RISK → FINAL_SYNTHESIS
```

Deep Research는 `directional_probability_hypothesis`를 사용한다. `probability_edge_risk_asymmetry`는 Python Risk Engine 결과를 소비하는 Execution 전용 capability다.

## 6. Gate/Recommendation Namespace

- DiscoveryDecision: `DEEP_DIVE_NOW|DEEP_DIVE_SECONDARY|WATCH_STAGE0|WATCH_RESET|EXCLUDE`
- ExecutionRecommendation: `NO_TRADE|WATCH|STARTER|ADD|FULL|TRIM|EXIT`
- AuditRecommendation: `SUPPORTS_CONTINUATION|CHALLENGES_CONTINUATION|NEEDS_NEW_EVIDENCE|AUDIT_EVIDENCE_INCOMPLETE`
- Python GateDecision은 별도 namespace이며 LLM enum에 재사용하지 않는다.

## 7. Prescreen·Stage·Final Authority

- Prescreen LLM: tri-state extraction만 생성.
- Python CapitalPrescreenGate: PASS/PASS_WITH_CONSTRAINTS receipt 생성.
- Stage LLM: `proposed_stage`만 생성.
- Python StageGate: Discovery 직후 final eligibility를 소유하며 Prescreen/Deep Research 전에 검증됨.
- Final Synthesis: recommendation만 생성.
- Python FinalAllocationGate: authoritative FinalAction과 Fresh Money 0..1 소유.

## 8. Metadata/Manifest

모든 Prompt의 frontmatter가 authoritative metadata다. Manifest는 frontmatter와 content hash의 generated immutable projection이며 semantic diff 0이어야 한다.

## 9. Final Contract Hardening

- STARTER: full StarterPlanV2와 PlannedAddV2, holding/time-stop, breakout/pullback, post-add maximum을 검증한다.
- Failure: 공통 FailurePathV2를 전 구간에서 사용하고 category 및 scenario/causal pair 독립성을 의미 검증한다.
- Portfolio: discriminated union과 rank/preferred/snapshot 교차필드 검증을 함께 적용한다.
- ADD: 전용 receipt 4종 및 target/trigger/evidence lineage를 검증한다.
- MarketExecutionGate: `PASS_WITH_PARTIAL`을 금지하고 passing receipt는 `core_input_complete=true`만 허용한다.


