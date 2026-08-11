# Stock Agent PR #7 — Security Master Coverage Closure / Live Shadow 판정

실행 기준: `codex/discovery-mvp-v2`, PR #7 Draft/Open/Unmerged 유지

## 실행 모드

- 실제 SEC 공식 데이터 사용
- Security Master Bootstrap candidate 생성
- SEC submissions bulk cache 및 unresolved supported issuer의 최신 10-Q/10-K/20-F/40-F cover-page fetch 사용
- `DISCOVERY_ENABLED=true`, `DISCOVERY_SHADOW_MODE=true`로 provider 환경 확인
- Deep ANALYZE: 0회
- LLM calls: 0회
- PAPER/Order mutation: 0회
- 임계값·Fuel·identity 필터 완화: 없음

## 기준과 구현 결과

기존 실행 기준은 raw SEC 10,398건, supported exchange scope 7,659건, identity known 5,686건(74.2395%), accepted common stock 4,428건, sector known 3,176건(71.7254%)이었다.

이번 변경은 다음을 추가했다.

- SEC periodic filing의 동일 context `dei:Security12bTitle` + `dei:TradingSymbol` + `dei:SecurityExchangeName` tuple parser
- latest periodic filing 선택 기준: `filed_at`, `acceptanceDateTime`, accession tie-break
- unresolved supported issuer만 대상으로 하는 bounded filing fetch, raw HTML cache와 provenance
- 공식 거래소명 alias 정규화 및 exchange conflict fail-closed
- `Ordinary Shares`와 명시적 common stock 제목의 deterministic 분류
- SIC 공식 범위 기반 deterministic sector mapping 보강
- identity before/after cover, conflict, remaining unknown, SIC/sector 원인 metrics
- candidate-only / atomic publish / last-known-good 보존 정책 유지

## 실제 Security Master Bootstrap

| 항목 | 결과 |
|---|---:|
| Raw SEC baseline | 10,398 |
| Supported exchange scope | 7,659 |
| Identity known before cover | 6,571 (85.7945%) |
| SEC cover tuple parsed | 448 |
| Identity newly resolved by cover | 248 |
| Cover conflicts | 119 |
| Identity known after cover | 6,819 (89.0325%) |
| Identity remaining UNKNOWN | 840 |
| Accepted common stock | 5,477 |
| SIC known in accepted | 5,184 (94.6504%) |
| Sector known in accepted | 5,172 (94.4313%) |
| Sector UNKNOWN: missing SIC | 293 |
| Sector UNKNOWN: mapper gap | 12 |
| Published active snapshot | 아니오 |

원래 5,686건에서 6,571건으로 오른 차이는 공식 Nasdaq Trader row의 명시적 `Ordinary Shares` 분류 보강에서 발생했습니다. cover-page fetch는 성공한 문서만 cache에 저장했으며, secret 값은 로그·snapshot·provenance에 기록하지 않았습니다.

### Identity UNKNOWN 원인

cover fetch 전 1,088건의 진단 bucket은 다음과 같습니다.

- `NO_OFFICIAL_NASDAQ_ROW`: 836 (76.8382%)
- `ONLY_ETF_FIELD_KNOWN`: 207 (19.0257%)
- `CLASS_SHARE_AMBIGUITY`: 10 (0.9191%)
- `EXCHANGE_MISMATCH`: 27 (2.4816%)
- `FOREIGN_OR_DEPOSITARY_AMBIGUITY`: 8 (0.7353%)

cover 적용 후 남은 840건을 별도 점검한 결과:

- 640건: 허용한 최신 periodic form이 없어 cover-page security tuple 증거 없음
- 200건: cover tuple은 존재하지만 `Class A Subordinate Voting Shares`, `Class A Limited Voting Shares`, `Registered Shares`, `Capital Stock` 등 현재 명시적 common-stock mapping 밖의 title이라 UNKNOWN 유지
- SEC cover exchange와 baseline exchange가 충돌한 문서는 `UNKNOWN_CONFLICTED`로 유지

`EntityCommonStockSharesOutstanding`만 있는 문서는 identity를 해소하지 않았습니다. 회사명·ticker 모양·ETF=N·share count를 보통주 증거로 사용하지 않았습니다.

### Sector 원인

SIC가 없는 accepted record 293건과 SIC가 있지만 현재 mapping 범위 밖인 12건을 분리했습니다. SIC mapper는 회사명이나 ticker 추측 없이 공식 SIC range 기반으로 확장했고, 기존 `7372 -> Software/IT Services` 등 구체 mapping을 유지했습니다.

## Health

실행 명령: `python main.py discovery-health`

- Security Master: `BOOTSTRAP_REQUIRED` — active snapshot 없음, candidate coverage insufficient
- Identity readiness: `IDENTITY_COVERAGE_INSUFFICIENT` — 89.0325% < 95%
- Sector readiness: `SECTOR_READY` — 94.4313% >= 90%
- SEC_USER_AGENT: READY
- Toss credentials: READY
- Market quote sample: Security Master not ready로 미실행
- Daily bars / benchmark SPY·QQQ·IWM: Security Master not ready로 미실행
- Fundamental provider: constructed, sample blocked by Security Master
- Capital preflight provider: constructed, sample blocked by Security Master
- Overall: `BOOTSTRAP_REQUIRED`

## Live Shadow

Security Master가 READY가 아니므로 지시서의 fail-closed 조건에 따라 `discover-market --shadow --intensity MINIMUM`은 실행하지 않았습니다.

- Executed: 아니오
- Raw/accepted market funnel: 미실행
- Deep analyzed: 0
- Certified: 0
- Final: `NOT_EXECUTED; readiness blocker가 해소되지 않아 투자 후보 final을 만들지 않음`

다음 운영 단계는 남은 840건을 공식 security-type source 또는 공식 filing evidence로 추가 해소한 뒤 identity 95% 이상을 재검증하는 것입니다. threshold를 낮추거나 UNKNOWN을 common stock으로 바꾸는 방식은 허용되지 않습니다.

## 안전성 / Side effect

- PAPER positions: 변화 없음
- PAPER orders: 변화 없음
- cash ledger: 변화 없음
- pending orders / transactions: 변화 없음
- Bootstrap·Health는 Orchestrator/PAPER 초기화 경계를 사용하지 않았습니다.
- 생성된 raw filing cache와 candidate snapshot은 Git 추적 대상이 아닙니다.

## 검증

- 전체 unittest: 286 PASS
- Security Master bootstrap test: 23 PASS (기존 18 + 신규 5)
- compileall: PASS
- tracked UTF-8 scan: PASS
- secret scan: 실제 credential 값 미검출 (placeholder/example 선언만 존재)
- PR #7: Draft/Open/Unmerged 유지, 자동 merge하지 않음

## 최종 판정

`BOOTSTRAP_REQUIRED`

최대 blocker:

1. supported identity coverage 89.0325%로 95% readiness 기준 미달
2. 남은 640건은 허용 periodic filing cover evidence가 없음
3. 남은 200건은 explicit common-stock mapping 밖의 security title
4. active snapshot 미발행으로 Toss quote, bars, SPY/QQQ/IWM benchmark, CompanyFacts, Capital Preflight를 운영 시험하지 못함
5. 실제 Live Shadow funnel과 final recommendation은 아직 검증하지 못함
