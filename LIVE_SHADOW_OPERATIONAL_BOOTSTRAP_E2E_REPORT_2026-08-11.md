�r�^�f��ئ{O,y�'vî���# Stock Agent PR #7 — Live Shadow Operational Bootstrap 실행 결과

작성 시각: 2026-08-11 KST  
브랜치: `codex/discovery-mvp-v2`  
실행 기준 HEAD: `69b702af1c82de993813803ee6623583371c97f8`  
PR: #7 Draft / Open / Unmerged 유지

## 최종 판정

`BOOTSTRAP_REQUIRED`

실제 SEC/Nasdaq 소스로 Security Master bootstrap을 실행했지만, configured readiness gate를 통과하지 못했습니다. 따라서 active snapshot을 publish하지 않았고, Live Shadow Discovery도 실행하지 않았습니다.

## 변경 및 검증한 운영 경로

- SEC Company/Ticker Directory를 listing baseline으로 사용했습니다.
- Nasdaq Trader 공식 디렉터리의 security name, ETF, Test Issue 정보를 사용했습니다.
- SEC submissions bulk archive를 우선 사용했습니다.
  - URL: `https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip`
  - per-CIK 요청 폭증을 피하고, cache/checksum/source metadata를 보존합니다.
- bulk archive 실패 시 기존 per-CIK cache만 사용하는 bounded fallback을 두어 refresh storm을 막았습니다.
- Security Master candidate/failed-candidate/diagnostics/progress/lock 파일을 active snapshot과 분리했습니다.
- coverage 부족 시 active last-known-good snapshot을 보존하고 publish하지 않습니다.
- 빈 exchange는 임의 보정하지 않고 `MISSING_EXCHANGE` rejection으로 처리합니다.
- Nasdaq `Test Issue=Y`는 `TEST_ISSUE`로 보존·제외합니다.
- `discovery-schema-init`은 schema만 초기화하고 PAPER account/cash/order를 만들지 않습니다.
- Shadow discovery CLI는 Orchestrator의 PAPER 초기화를 호출하지 않습니다.

## Health / credentials

| 항목 | 결과 | 사유 |
|---|---:|---|
| Database schema | READY | schema-only init 완료 |
| Security Master active snapshot | BLOCKED | `SECURITY_MASTER_COVERAGE_INSUFFICIENT` |
| Security Master candidate | BLOCKED | 지원 범위 identity/sector threshold 미달 |
| Toss credentials | READY | 값은 출력·저장하지 않음 |
| SEC_USER_AGENT | READY | 값은 출력·저장하지 않음 |
| Market quote sample | BLOCKED | Security Master active snapshot 없음 |
| Daily bars sample | BLOCKED | Security Master active snapshot 없음 |
| SPY/QQQ/IWM benchmark | BLOCKED | Security Master gate 이전 단계 |
| CompanyFacts provider | CONSTRUCTED / SAMPLE BLOCKED | Security Master 미준비 |
| Capital preflight provider | CONSTRUCTED / SAMPLE BLOCKED | Security Master 미준비 |
| Overall | `BOOTSTRAP_REQUIRED` | fail-closed |

## 실제 Security Master bootstrap

| 지표 | 실제 결과 |
|---|---:|
| Raw SEC baseline | 10,398 |
| Supported exchange scope | 7,659 |
| Identity known global | 5,714 |
| Identity coverage global | 54.9529% |
| Identity known supported | 5,686 |
| Identity coverage supported scope | 74.2395% |
| Accepted common stock | 4,428 |
| Sector known among accepted | 3,176 |
| Sector coverage | 71.7254% |
| Duplicate | 0 |
| Identity conflict | 0 |

Readiness 기준은 변경하지 않았습니다.

- supported-scope identity coverage: `95%` 필요, 실제 `74.2395%`
- accepted common stocks: `>= 1` 충족
- sector coverage: `90%` 필요, 실제 `71.7254%`

## Rejection counts

| 사유 | 건수 |
|---|---:|
| `UNSUPPORTED_EXCHANGE` | 2,542 |
| `UNKNOWN_IDENTITY_IS_COMMON_STOCK` | 1,973 |
| `NOT_COMMON_STOCK` | 1,258 |
| `MISSING_EXCHANGE` | 197 |

추측으로 UNKNOWN을 common stock이나 비-warrant 등으로 바꾸지 않았습니다.

## Source 상태

- SEC listing cache: 사용됨
- Nasdaq Trader `nasdaqlisted.txt` / `otherlisted.txt`: 사용됨
- SEC submissions bulk archive: 다운로드 후 cache 재사용
- bulk fallback individual network calls: `0`
- bulk archive 재사용 download: `0` (이번 build에서는 cache hit)
- SEC submissions source as-of: `Tue, 11 Aug 2026 04:39:56 GMT`
- Nasdaq source as-of: `0811202607:00`
- active snapshot: 생성하지 않음
- candidate snapshot: 생성됨
- failed candidate: 생성됨
- diagnostics/progress: 생성됨

## Discovery funnel

실제 Discovery funnel은 Security Master active readiness gate에서 중단했습니다.

| 단계 | 결과 |
|---|---:|
| Raw universe | 10,398 |
| Accepted universe | 0 active snapshot 기준 |
| Market ready | 0 |
| Preliminary survivors | 실행하지 않음 |
| Fundamental hydrated | 0 |
| Final Fuel PASS | 0 |
| Capital preflight requested | 0 |
| Capital preflight success | 0 |
| P1/P2/WATCH/REJECT | 실행하지 않음 |
| Deep analyzed | 0 |
| Certified | 0 |
| Final | `NONE` / Discovery 미시작 |

## Side-effect 감사

schema-only initialization과 failed-candidate bootstrap 전후에 실제 DB를 확인했습니다.

- PAPER accounts: `0`
- portfolio positions: `0`
- PAPER orders: `0`
- PAPER cash ledger: `0`
- PAPER transactions: `0`
- PAPER reservations: `0`
- LLM calls: `0`
- Deep child handoff: `0`

## 테스트 및 정적 검증

- 전체 unittest: `281 PASS`
- Security Master bootstrap 회귀: `18 PASS`
- compileall: `PASS`
- tracked source UTF-8: `PASS`
- tracked secret scan: `PASS`
- CI 4-platform: 이 로컬 실행에서는 새 원격 commit을 아직 push하지 않아 재실행 대기

## 현재 최대 blocker

1. Nasdaq Trader의 현재 무료 symbol directory만으로는 supported scope의 security type identity가 `74.2395%`에 그칩니다. 공식 exchange security-master/issue-type 데이터 소스를 추가 연결해야 하며, 그 전까지 UNKNOWN은 유지해야 합니다.
2. SIC는 실제로 수집됐지만 기존 deterministic SIC→sector mapping으로 `71.7254%`만 canonical sector로 확정됩니다. mapping 확장은 근거 있는 공식 SIC taxonomy 범위에서 별도 검토해야 합니다.
3. active Security Master가 없으므로 Toss quote/bar 및 SPY/QQQ/IWM benchmark sample은 안전하게 실행하지 않았습니다.
4. 위 gate가 해결되기 전에는 CompanyFacts hydration, capital preflight, fuel/ranking, P1, explicit deep promotion을 실제 live run으로 주장할 수 없습니다.

## 다음 실행 조건

threshold를 낮추거나 UNKNOWN을 추측으로 채우지 않고, official exchange security-master/issue-type source를 연결한 뒤 다음 순서로 재실행해야 합니다.

`bootstrap → discovery-health → Toss quote/bar/benchmark health → CompanyFacts/Capital sample → discover-market --shadow --intensity MINIMUM`

현재 실제 운영 판정은 **`BOOTSTRAP_REQUIRED`** 입니다.
