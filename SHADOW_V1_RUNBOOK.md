# Stock Agent SHADOW V1.0 Runbook

SHADOW V1.0은 운영자가 하루 한 번 직접 실행하는 read-only 의사결정 기록 시스템입니다. Broker 주문 기능은 포함하지 않습니다.

## 환경변수

`.env`에는 최소 다음 값을 설정합니다. 값은 로그·SQLite·보고서에 저장되지 않습니다.

```text
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6-luna
LUNA_DEFAULT_REASONING_EFFORT=medium
LUNA_DEEP_REASONING_EFFORT=high
LUNA_MAX_RETRIES=2
LUNA_RETRY_BACKOFF_SEC=1
```

Toss, SEC 및 Research provider 설정은 기존 read-only 설정을 그대로 사용합니다.

## Luna contract smoke

```powershell
python -m stock_agent --smoke-luna
```

## Daily Shadow Run

입력 JSON에는 live universe/research query 등 기존 strict runtime 입력만 둡니다. action, shares, capital 값은 Python authority를 대체할 수 없습니다.

```powershell
python -m stock_agent `
  --daily-shadow-run `
  --strict `
  --llm-provider luna `
  --market-provider live `
  --sec-provider sec `
  --research-provider issuer_ir `
  --portfolio-provider toss `
  --input shadow_input.json `
  --database shadow_v1.db `
  --shadow-output shadow_runs
```

중단된 Shadow orchestration은 마지막 완료된 authoritative run 경계에서 재개할 수 있습니다.

```powershell
python -m stock_agent ... --resume-shadow-run RUN-20260825-001
```

다른 authoritative run의 StageResult를 현재 run으로 복사하지 않습니다.

## Outcome append

관측된 일봉만 포함한 JSON 배열을 준비합니다. 배열에 없는 휴장일은 세션으로 계산하지 않습니다.

```powershell
python -m stock_agent `
  --database shadow_v1.db `
  --update-shadow-outcomes decision-... `
  --outcome-bars observed_bars.json `
  --outcome-as-of 2026-09-25
```

기존 Decision은 수정되지 않으며 Outcome snapshot만 append됩니다. 같은 `decision_id/horizon/as_of`에 다른 값을 다시 쓰면 conflict로 거부됩니다.

## 생성 파일

```text
shadow_runs/YYYY-MM-DD/RUN-YYYYMMDD-NNN/
  DAILY_REPORT.md
  RUN_LOG.json
  DECISIONS.jsonl
  INCIDENTS.jsonl
  EVIDENCE_MANIFEST.jsonl
```

Human report는 authoritative source가 아닙니다. SQLite의 Run, RawArtifact, Evidence, StageResult, Gate receipt 및 FinalAllocation이 최종 진실입니다.

## Hotfix와 Replay

- S0 correctness/security hotfix는 `SHADOW_V1.1`, `SHADOW_V1.2`처럼 새 버전을 사용합니다.
- 원본 Decision과 Run은 변경하지 않습니다.
- Counterfactual replay는 새 Shadow Run으로 생성하고 `original_shadow_run_id`로 원본을 참조합니다.
- 전략 threshold/weight/policy 변경은 30일 평가 전에는 수행하지 않습니다.
