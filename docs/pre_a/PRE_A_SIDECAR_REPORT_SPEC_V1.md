# PRE-A Sidecar Report V1

## 목적

PRIMARY Stock Agent의 공식 Shadow/Research 판정을 바꾸지 않고, 이미 생성된 `DAILY_REPORT.md`를 **읽기 전용 입력**으로 사용해 별도의 `PRE_A_REPORT.md`를 생성한다.

핵심 원칙:

```text
PRIMARY authoritative report
        ↓ read only
PRE-A sidecar
        ↓
independent PRE_A_REPORT.md
```

PRE-A 결과는 Research Grade, Execution Action, Position Size, 기존 DB 또는 기존 Shadow artifact를 수정할 권한이 없다.

## 운영 위치

공식 Shadow artifact 디렉터리는 immutable로 유지한다. 따라서 PRE-A 보고서는 `shadow_runs/<run_id>/` 안에 추가하지 않고 별도 root에 저장한다.

권장:

```text
shadow_runs/<shadow_run_id>/DAILY_REPORT.md
        ↓
pre_a_reports/<shadow_run_id>/PRE_A_REPORT.md
```

## 실행

```cmd
python -m stock_agent.pre_a_sidecar --source-report shadow_runs\<shadow_run_id>\DAILY_REPORT.md --llm-provider luna
```

기본 출력:

```text
pre_a_reports/<shadow_run_id>/PRE_A_REPORT.md
```

명시적 출력도 가능하다.

```cmd
python -m stock_agent.pre_a_sidecar --source-report shadow_runs\<shadow_run_id>\DAILY_REPORT.md --output pre_a_reports\<shadow_run_id>\PRE_A_REPORT.md --llm-provider luna
```

## V1 입력 경계

V1은 의도적으로 **완성된 Daily Report만** 읽는다.

- web search 금지
- SEC 재검색 금지
- 뉴스 재검색 금지
- 기존 개인 기억/과거 분석 사용 금지
- source report에 없는 ticker 생성 금지
- source report에 없는 수치/계약/촉매 생성 금지

보고서가 PRE-A 판정에 충분한 근거를 제공하지 못하면 `INSUFFICIENT_SOURCE_REPORT` 또는 후보별 `NOT_EVALUATED`를 반환한다.

## 등급 방화벽

PRE-A는 등급이 아니다.

```text
Current Research Grade
≠ Promotion Readiness
≠ Execution Action
```

V1에서 `PRE_A` 또는 `PRE_A_HIGH`는 source report가 `B+`를 명시적으로 뒷받침하는 후보에만 허용한다.

PRE-A 보고서는 다음을 절대 할 수 없다.

- B+ → A- 자동승격
- PRE_A_HIGH → STARTER 자동승격
- 기존 Action 변경
- 기존 Position Size 변경
- PRIMARY report rewrite

## PRE-A 보고서 필드

후보별 최소:

- ticker
- source_grade
- promotion_readiness
- a_trajectory
- fundamental_direction
- expectation_gap
- price_lag
- catalyst_window
- missing_gates + severity
- promotion_triggers
- demotion_triggers
- expiry_or_recheck
- source_limitations

## 출력 해석

`PRE_A_HIGH`는 "거의 A"라는 뜻이 아니다.

정확한 의미:

> 현재 B+를 유지하면서 A-/A 재인증에 가까워지고 있는지 감시할 우선순위가 높다.

Promotion Trigger가 실제로 발생하더라도 이 Sidecar는 자동승격하지 않는다. 이후 별도의 Blind A-/A Recertification이 필요하다.

## PRIMARY Shadow 보호

이 기능은 공식 `--daily-shadow-run` 경로와 연결하지 않는다.

따라서:

- 공식 Run count 변화 없음
- broker writes = 0
- PRIMARY DB write = 0
- PRIMARY grade/action mutation = 0
- V8 OFF 상태와 무관

운영자는 PRIMARY 실행 완료 후 두 번째 독립 명령으로 PRE-A 보고서를 생성한다.
