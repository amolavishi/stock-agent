# Stock Agent v1.1 전면 무결성 재작업 구현·검증 보고서

- 작성 시각: 2026-08-10 (Asia/Seoul)
- 기준 요구사항: `Stock Agent v1.1 전면 무결성 재작업 지시서 v2.1`
- 최종 코드 schema version: `20`
- 운영 DB migration: `12 → 19 → 20`
- 운영 모드: `PAPER only`
- Live E2E: **수행하지 않음** — 사용자 승인 전 유료/외부 호출 금지 조건 준수
- 최종 자동 테스트: **166/166 PASS**
- Secret scan: **0 findings**
- Doctor: **healthy=true**

## 1. 최종 판정

| 영역 | 판정 | 근거 |
|---|---|---|
| Emergency Certification Gate | DONE | 실행/분석/인증/side-effect 상태 독립 저장 및 export gate 적용 |
| INOD Golden Failure | DONE | 고정 입력 Offline Orchestrator E2E에서 정상 투자보고서 생성 차단 |
| Required Data Contract | DONE | 가격·시장·최신 중요 공시·자본구조·portfolio 요구조건을 deterministic blocker로 적용 |
| SEC Evidence Lifecycle | DONE | DISCOVERED→FETCHED→PARSED→EXHIBITS_RESOLVED→VALIDATED→READY_FOR_ANALYSIS |
| CompanyFacts / Financial Ontology | DONE | raw/normalized/derived 계층, instant/duration, Q2 YTD provenance, debt flow 배제 |
| Capital Structure Ontology | DONE | ATM/Shelf/Warrant/Convertible capacity·usage·outstanding 상태 분리 |
| Evidence Receipt / Request Lifecycle | DONE | 수집과 Agent 실제 열람을 분리하고 must-answer 양측 검토를 요구 |
| Claim–Evidence Validation | PARTIAL | ID/domain/grade/strength 검증 완료. 자연어 의미 관련성은 deterministic 규칙 범위 |
| Debate FSM / Consensus / Deadlock | DONE | material issue, must-answer, evidence review, stress test, no-progress 반영 |
| Context Compaction | DONE | 전체 대화 누적 금지, P0/P1 pin, R10 bounded context 테스트 통과 |
| PAPER Account / Sizing / Risk | DONE | 실제 account, 복합 risk metric, weighted-average cost basis, 분석 read-only |
| Conditional Order | PARTIAL | canonical BUY 재검증·상태머신·예약 반환·단일 체결 완료. 주기적 가격 감시 scheduler는 별도 운영 wiring 필요 |
| Financial Idempotency / Journal | DONE | operation key, immutable journal, reconciliation, crash boundaries, outbox 분리 |
| True Double-entry Accounting | DEFERRED | 요구사항에 따라 허위 명칭을 사용하지 않고 Immutable Financial Journal로 명시 |
| Queue CAS / Recovery | DONE | stable inbound identity와 worker 2개 동시 claim 단일 성공 검증 |
| Cancellation Boundary | DONE | CANCELLED_BEFORE_COMMIT / COMMITTED_BEFORE_CANCEL_REQUEST 구현 |
| Market Data Quality | DONE | transport/quote/candle/session/bar/volume/indicator 상태 분리 |
| Obsidian Certified Knowledge | DONE | uncertified Core write 차단, fact 메타, path traversal/.obsidian 보호 |
| Delta Research | DONE | CERTIFIED Run만 trusted baseline, 4개 cutoff 분리 |
| Report / Data Lineage | DONE | uncertified contract, 다중 confidence, numeric value/source/as_of/method 강제 |
| Cost Telemetry | DONE | 내부 canonical call identity, usage-known, token/cache/reasoning/cost/latency 기록 |
| OpenTelemetry Export | DEFERRED | 내부 schema와 분리된 선택적 export layer는 구현하지 않음 |
| Migration Safety | DONE | backup/shadow/count/checksum/FK/integrity/atomic activation 적용 |
| Live MINIMUM E2E | DEFERRED | 사용자 승인 필요 |

## 2. 이전에 깨졌던 핵심 invariant와 증명

### INOD 실패

수정 전 고정 보고서에는 다음 모순이 동시에 존재했다.

- Debate: `DEADLOCK`
- Decision: `WAIT`, Confidence `53`
- TradePlan 및 141-share sizing 노출
- Run: `SUCCESS`처럼 표현
- 최신 Q2/ATM/시장 데이터 무결성 문제 존재

수정 후 같은 고정 입력의 Offline E2E 결과:

- `ExecutionStatus=SUCCESS`
- `AnalysisStatus=BLOCKED`
- `CertificationStatus=BLOCKED_MARKET_DATA`
- `SideEffectStatus=NOT_AUTHORIZED`
- `Action=NO_CERTIFIED_ACTION`
- Decision Confidence `N/A`
- TradePlan 없음
- PositionSizing 없음
- PAPER position 0
- Obsidian Core 생성 없음

Evidence 배열 순서를 반대로 넣은 metamorphic replay에서도 인증 결과가 동일했다.

### 금융 invariant

- Opening Cash + cash journal = Current Cash: PASS
- Reserved Cash = active reservations 합계: PASS
- Position Quantity = BUY 수량 - SELL/TRIM 수량: PASS
- Cost basis: `WEIGHTED_AVERAGE`, 거래별 기록
- 동일 financial operation 재실행: effect count 정확히 1
- commit 직후 crash 후 retry: effect count 정확히 1
- Discord publish 전/후 retry: 금융 effect count 정확히 1
- 동일 ticker의 서로 다른 PAPER account: `(ticker, account_id)` 복합키로 격리

## 3. 주요 변경 파일

### 신규 파일

- `stock_agent/certification.py` — 독립 상태와 인증 gate
- `stock_agent/paper_execution.py` — 모든 PAPER action의 canonical validator
- `stock_agent/market_quality.py` — 다차원 시장 품질 판정
- `stock_agent/lineage.py` — material numeric claim provenance contract
- `stock_agent/migration.py` — backup/shadow/checksum/FK/atomic activation
- `stock_agent/secret_scan.py` — secret 후보 스캔
- `tests/test_v21_certification.py`
- `tests/test_v21_financial_integrity.py`
- `tests/test_v21_sec_integrity.py`
- `tests/test_v21_grounding.py`
- `tests/test_v21_debate_fsm.py`
- `tests/test_v21_paper_policy.py`
- `tests/test_v21_market_quality.py`
- `tests/test_v21_migration_security.py`
- `tests/test_v21_inod_offline_e2e.py`
- `tests/fixtures/inod_20260810_failure/` — hash가 고정된 실패 입력 snapshot

### 핵심 수정 파일

- `stock_agent/schemas.py`
- `stock_agent/database.py`
- `stock_agent/orchestrator.py`
- `stock_agent/reports.py`
- `stock_agent/command_parser.py`
- `stock_agent/dispatcher.py`
- `stock_agent/paper.py`
- `stock_agent/position_sizing.py`
- `stock_agent/guard.py`
- `stock_agent/toss.py`
- `stock_agent/delta.py`
- `stock_agent/knowledge.py`
- `stock_agent/debate.py`
- `stock_agent/analysis_context.py`
- `stock_agent/hermes_agents.py`
- `stock_agent/claim_validation.py`
- `stock_agent/sec.py`
- `stock_agent/edgar_documents.py`
- `stock_agent/capital_structure.py`

## 4. DB migration

운영 DB를 삭제하거나 초기화하지 않았다.

1. 서비스 중지
2. SQLite online backup
3. 원본 `PRAGMA integrity_check=ok`
4. shadow DB 생성
5. schema 12→19 migration
6. table row count 검증
7. critical-column checksum 검증
8. `foreign_key_check=0`
9. atomic activation
10. schema 19→20에 동일 절차 반복
11. post-migration doctor 및 금융 invariant 검증

최종 운영 DB:

- schema: `20`
- integrity: `ok`
- FK violation: `0`
- runs: `22`
- positions: `0`
- 기존 INOD legacy Run: `BLOCKED_SYSTEM_INTEGRITY / NO_CERTIFIED_ACTION`

백업 예:

- `data/migration_work/stock_agent.20260810T053954Z.backup.sqlite`
- `data/migration_work/stock_agent.20260810T054203Z.backup.sqlite`

## 5. Prompt 및 Agent 변경

- SEC/웹/Discord/과거 지식은 모두 `UNTRUSTED DATA` delimiter 안에 삽입한다.
- Evidence 본문을 system/tool instruction으로 실행하지 않는다.
- Research는 Thesis Defender, Critic은 Skeptical Falsifier 역할을 유지한다.
- Round 결과의 accepted/rejected/modified/unresolved/new/withdrawn/evidence-request 필드를 구조화한다.
- score에는 `coverage`, `rubric_version`, `supporting_facts`, `evidence_ids`, `missing_inputs`를 저장한다.
- 이전 전체 대화를 다음 Round에 누적하지 않는다.

## 6. 비용 계측과 Context 최적화

각 호출에 다음 내부 canonical 필드를 저장한다.

- call_id / parent_call_id / run_id / role / round / phase
- provider / model / attempt / repair / retry 성격
- input/output/reasoning/cache tokens
- latency / estimated cost / usage_known
- success/failure / exception_type

Offline 수정·검증 과정에서는 유료 LLM 호출을 하지 않았다. 과거 INOD 실패 Run의 감사값은 21 calls, input 293,518, output 258,971, reasoning 181,602, cache 401,664, estimated cost 약 $0.114729였다.

Context는 Canonical AnalysisContext + Issue Ledger + 현재 Thesis + 필요한 Evidence + 직전 상대 응답 + 압축 change history만 사용한다. P0/P1 evidence는 순서와 budget 변화에도 빠지지 않는다.

## 7. Debate Engine

지원 상태:

- ROUND_ACTIVE
- WAITING_FOR_EVIDENCE
- EVIDENCE_REVIEW_REQUIRED
- BLOCKED_BY_MATERIAL_ISSUE
- PROVISIONAL_CONSENSUS
- STRESS_TEST_REQUIRED
- FINAL_CONSENSUS
- DEADLOCK / FAILED / CANCELLED

Consensus는 decision 문자열 일치가 아니라 material disagreement 여부로 판정한다. must-answer 미해결, critical material issue, 새 material evidence 양측 미검토 상태에서는 합의를 금지한다. 2개 연속 Round에서 material information gain이 없으면 evidence collection 또는 DEADLOCK으로 종료한다.

## 8. SEC / CompanyFacts / Capital Structure

- Raw filing과 XBRL raw fact, normalized fact, derived metric을 별도 table에 저장한다.
- 8-K Item 2.02/7.01/8.01은 Exhibit 99.x resolver를 통과해야 한다.
- metric resolver는 concept/unit/form/fy/fp/start/end/duration/filed/accn/frame를 보존한다.
- Q2 standalone 계산은 `6M_YTD - Q1`, source fact IDs와 derived flag를 기록한다.
- 현금흐름의 debt payment를 debt balance로 사용할 수 없다.
- ATM capacity/used/remaining과 warrant/convertible authorized/offerable/outstanding를 분리한다.
- external evidence와 SQLite state의 material conflict는 Certification을 차단한다.

## 9. PAPER / Risk / Side Effect

- `ANALYZE`는 기본 read-only이며 `paper_action_enabled=False`이다.
- 명시적 `PAPER_BUY/PAPER_SELL/PAPER_TRIM`만 financial path에 진입한다.
- HOLD는 실제 보유 position이 없으면 WAIT로 canonicalize하고 오류 이유를 남긴다.
- WAIT/EXCLUDE/uncertified 상태에서 신규 sizing을 export하지 않는다.
- Conditional trigger는 일반 BUY와 동일한 freshness/certification/cash/heat/sector/exposure validation을 다시 통과한다.
- Risk는 initial capital risk/current mark-to-stop/pending committed/gross/sector exposure로 분리한다.
- 실제 주문 또는 brokerage API는 존재하지 않는다.

## 10. Obsidian / Delta

- SQLite가 Source of Truth이다.
- Obsidian은 사람이 읽는 certified persistent knowledge projection이다.
- DEADLOCK/BLOCKED/SYSTEM_INTEGRITY_FAIL Run은 Core write가 차단된다.
- Core fact에는 source/verified_at/certification_status/mutable_class가 기록된다.
- 현재 가격·현재 ATM 잔여량·현금·주식수·guidance 같은 변동값은 Core 영구 사실로 저장하지 않는다.
- 이전 Run이 CERTIFIED가 아니면 diagnostic history일 뿐 trusted Delta baseline이 아니다.
- cutoff는 latest SEC accession/filed, market observed, CompanyFacts as-of로 분리한다.

## 11. 테스트 결과

- Baseline: 116 tests PASS
- 중간 Phase 6: 150 tests PASS
- 최종: **166 tests PASS**

검증 범위:

- Golden failure reproduction
- Certification/report contract
- Evidence domain/receipt/context property
- CompanyFacts/debt/capital ontology
- Debate consensus/deadlock/no-progress
- Queue concurrency
- Financial fault injection 및 after-commit retry
- Reservation/cash/position reconciliation
- Migration shadow/activation
- Obsidian traversal/write gate
- Secret scan false-positive regression
- Offline INOD E2E 및 evidence-order metamorphic replay

## 12. Doctor / 보안

Doctor 결과:

- SQLite integrity: `ok`
- journal mode: `wal`
- schema version: `20`
- report/Vault writable: true
- Hermes executable: true
- 필요한 credential 존재 여부: true (값은 출력하지 않음)
- PAPER only: true
- healthy: true

Secret scan은 `.env` 실제 값을 읽거나 출력하지 않으며 최종 `0 findings`였다. SEC HTML의 긴 점 구분 문자열 3건은 token 값 출력 없이 길이/해시로 오탐을 확인하고 회귀 테스트로 고정했다.

## 13. 남은 Known Issues / Technical Debt

1. **Live E2E 미수행**: 외부 Toss/SEC/Hermes/Discord 및 비용을 발생시키는 검증은 사용자 승인 후 MINIMUM 1회가 필요하다.
2. **SEC 의미 parser coverage**: 고정 fixture와 현재 규칙은 통과하지만 모든 issuer별 비표준 filing 문구를 완전 포괄한다고 주장하지 않는다.
3. **Semantic relevance**: Claim–Evidence domain과 strength 검증은 deterministic이나, 모든 자연어 문장의 의미 동치/비동치를 완전 판정하지는 않는다.
4. **Conditional scheduler**: 주문 평가 함수와 안전한 재검증은 구현됐으나 장기 실행 주기 감시 job wiring은 PARTIAL이다.
5. **OpenTelemetry export**: 내부 canonical telemetry는 완료했지만 OTel exporter는 외부 표준 결합을 피하기 위해 DEFERRED다.
6. **Double-entry**: 현재 구현은 Immutable Financial Journal + Reconciliation이며, 진정한 account-model double-entry는 아니다.
7. `Root Cause Matrix.md` 별도 파일은 작업 환경에서 발견되지 않아, 사용자 프롬프트에 유지된 1–23 항목을 감사 기준으로 사용했다.

## 14. 다음 우선순위

1. 사용자가 승인하면 MINIMUM Live E2E 1회
2. 결과의 Toss timestamp/session, SEC lifecycle, Hermes usage, Discord report delivery 확인
3. 문제가 없을 때만 NORMAL 1회
4. MAXIMUM은 비용 cap과 사용자 별도 승인 후 수행
5. 조건부 주문 periodic scheduler는 운영 주기·시장 시간 정책을 정한 뒤 연결

현재 결론: **Offline Certification/Data-Lineage/Financial-Integrity gate는 GO, Live E2E는 승인 대기(DEFERRED)**.
