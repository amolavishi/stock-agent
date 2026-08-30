# Current Architecture Reconstruction

작성 기준: 2026-08-18

현재 작업 디렉터리에는 `.git` metadata가 없어 branch/history/CI workflow를
검증할 수 없었다. 따라서 “repository 전체”는 제공된 현재 working tree와
첨부 package를 대상으로 역설계했으며, Git history/remote/CI는 MISSING으로
기록한다.

## 조사 범위

실제 repository의 `stock_agent/`, `tests/`, `README.md`,
`requirements.txt`, `env.example`, 그리고 redesign package 안의
Architecture v1.1, Investment Rules v2.0, Prompt Library v2.2 ZIP을
대조했다. Prompt Library의 READY 선언은 LLM contract 계층에만 적용하고
전체 시스템의 구현 완료로 해석하지 않았다.

## 현재 실행 구조

```text
python -m stock_agent
  -> cli.py (JSON input + library root + database)
  -> StockAgent.run()
  -> SQLiteStore.create_run()
  -> StockAgent._qualified_candidates()
       -> _work_stage(): enqueue -> lease -> heartbeat -> strict_call -> complete
       -> Python Stage/Capital gates
       -> Deep Research / SEC extraction / Audit prompt calls
  -> HUNT_ONLY: finish_run(QUALIFIED_CANDIDATE_POOL)
  -> HUNT_AND_EXECUTION_REVIEW: portfolio prompt -> RiskEngine -> synthesis prompt
       -> SQLiteStore.commit_final_allocation()
```

핵심 근거는 `stock_agent/runtime.py:25-202`와 `stock_agent/cli.py`이다.
현재 orchestration은 별도 Workflow/Recovery component가 아니라
`StockAgent` 한 클래스에 집중되어 있다.

## 실제 모듈/책임

| 위치 | 현재 책임 | 관찰된 상태 |
|---|---|---|
| `stock_agent/models.py:21-244` | RunMode, DiscoveryDecision, ExecutionAction, GateDecision, WorkStatus와 dataclass | PARTIAL: Architecture가 요구하는 Position, Snapshot, Candidate, Portfolio, Risk, ProviderCall 모델은 없음 |
| `stock_agent/store.py:18-380` | SQLite WAL, runs/work_items/evidence/claims/results/final actions, lease/heartbeat/retry/commit | PARTIAL: schema는 존재하지만 authoritative entity와 migration/recovery 계약이 부족 |
| `stock_agent/runtime.py:25-202` | discovery/execution orchestration, prompt 호출, gate/RiskEngine 연결 | PARTIAL/BROKEN: 실제 외부 데이터가 아니라 CLI raw/fixture를 stage output으로 투영하며, direct defaults와 단일 synchronous coordinator가 남아 있음 |
| `stock_agent/gates.py:20-163` | Stage, capital tri-state, context/sector/execution gate, RiskEngine, starter/add validators | PARTIAL: TechnicalFeatureCalculator, independent PositionSizer, full gate input contracts 없음 |
| `stock_agent/dependencies.py` | evidence dependency hash/fence wrapper | PARTIAL: subject/domain epoch 및 full dependency graph 없음 |
| `stock_agent/prompt_runtime.py:20-119` | manifest/frontmatter load, composition, ContextManifest, jsonschema strict call | IMPLEMENTED for prompt contract; runtime-owned state/DB/Provider는 아님 |
| `stock_agent/providers.py:12-98` | ModelProvider protocol, Fake/Recorded, DeepSeek boundary, Router, CostTracker facade | PARTIAL: DeepSeek `call()` is `NotImplementedError`; cost repository methods exist but no live provider/network policy |
| `stock_agent/cli.py` | CLI JSON input parsing and runtime invocation | PARTIAL: secrets/config/provider selection and raw provider boundaries 없음 |
| `tests/test_stock_agent.py` | 16 legacy contract/smoke tests | PARTIAL: fixture semantics를 검증하며 external adapter/E2E recovery는 없음 |
| `tests/test_production_runtime.py` | 6 production-path smoke/negative tests | PARTIAL: FakeProvider only, no real market/SEC/Obsidian adapter |

## 현재 state/data 흐름

SQLite는 `store.py:48-122`에서 WAL/FULL/FK로 초기화된다. 현재 테이블은
`runs`, `work_items`, `evidence`, `claims`, `claim_evidence`, `results`,
`stage_results`, `execution_contexts`, `debate_issues`, `rule_overrides`,
`cost_reservations`, `final_actions` 등이다. 그러나 Architecture의
`SecurityMaster`, `MarketSnapshot`, `TechnicalSnapshot`, `CapitalStructureSnapshot`,
`PortfolioSnapshot`, `RiskAssessment`, `QualifiedCandidatePool`,
`PromptExecution`, `ModelCall`, `ProviderCall`, `Notification`, `Checkpoint`,
`ErrorEvent`는 독립적인 typed repository entity로 존재하지 않는다.

`runtime.py:97-139`는 candidate raw fields를 사용해 FakeProvider의
canonical payload를 만들고, evidence도 `RECORDED_PROVIDER`로 seed한다.
따라서 현재 E2E는 실제 market/SEC ingestion이 아니라 recorded/fake
contract path이다. 이것은 의도적으로 MISSING adapter 경계를 남긴 상태이며
실제 외부 데이터 구현으로 오인하면 안 된다.

## 외부 기능 조사 결과

- Toss/MarketDataProvider: source, adapter, endpoint client, normalizer 없음 → **MISSING**.
- SEC/EDGAR: source, CIK resolver, submissions/CompanyFacts/filing fetcher 없음 → **MISSING**.
- Obsidian: projection, vault writer, knowledge loader 없음 → **MISSING**.
- DeepSeek: `providers.py:62-74`에 이름과 API-key constructor만 있고 실제 transport 없음 → **MISSING/PARTIAL**.
- CI/migration/backup/operational logging: 별도 workflow/migration/backup configuration 없음 → **MISSING**.

## 현재 실행의 권위

Python이 final allocation을 기록하는 경계는 존재한다. 그러나
`runtime.py:169-202`에서 raw input의 `requested_action`을 Python이 읽고,
market/price/equity가 없을 때 local defaults를 사용한다. 이는 Architecture의
실제 fresh-provider contract보다 약하다. Prompt output은
`PromptRuntime.strict_call()`로 schema 검증되지만, Prompt Library output이
SQLite authority가 되지는 않는다.

## Legacy/fixture 경로

`tests/test_stock_agent.py:21-32`의 candidate/execution fixture는
`research_status`, `audit_recommendation`, `shares`, `capital_pct` 등의
필드를 포함한다. production runtime은 일부를 raw provider payload로
변환하지만, 이 입력 형식 자체는 live adapter DTO가 아니다. vNext에서는
fixture를 `RecordedMarketProvider`, `RecordedSECProvider`, `RecordedLLMProvider`
입력으로 격리하고 authoritative conclusion 필드를 제거해야 한다.
