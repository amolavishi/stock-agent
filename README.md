# Stock Agent Hybrid PAPER v0.6

Discord 자연어 명령을 받아 미국 주식의 시세·SEC 근거를 수집하고, Hermes/DeepSeek 기반 Research·Critic·Chairman 토론을 거쳐 PAPER 보고서를 만드는 독립 실행 프로젝트입니다. Codex가 꺼져 있어도 로컬 런타임이 실행 중이면 작동합니다.

## 운영 구조

1. 사용자가 `명령채널`에 자연어 또는 슬래시 명령을 입력합니다.
2. Chairman Python 런타임이 서버·채널·사용자 허용 목록, 봇 메시지, 중복 요청을 검사합니다.
3. Toss 시세와 SEC 원문/Company Facts를 수집합니다. 실데이터 실패 시 mock으로 대체하지 않습니다.
4. Hermes가 Research → Critic을 수행하며, 근거 보완이 필요하면 최대 한 번만 추가 조사합니다.
5. Python이 위험 규칙·포지션 크기를 계산하고 Hermes Chairman이 결론을 작성합니다.
6. Python Final Guard가 근거 ID, 위험 규칙, 거래 계획을 검증합니다.
7. 토론 내용은 `토론의장`, 최종 Markdown 보고서는 `보고서제출` 채널에 올라갑니다.

Discord는 표시·입력 계층이며 봇끼리 Discord 메시지를 읽어 통신하지 않습니다. Research와 Critic 봇은 출력 전용이고, 명령 수신은 Chairman 봇 하나만 담당합니다.

## 필요한 설정

`.env.example`을 `.env`로 복사한 후 아래 값을 채웁니다.

- `TOSS_APP_KEY`, `TOSS_APP_SECRET`
- `DEEPSEEK_API_KEY`
- Discord Research/Critic/Chairman 봇 토큰 3개
- 서버, 명령채널, 토론채널, 보고서채널, 소유자 사용자 ID
- `SEC_USER_AGENT=StockAgent/0.6 실제연락가능이메일`

SEC에는 API 키가 없습니다. User-Agent에는 앱 이름과 연락 가능한 이메일이 모두 필요합니다. 특정 이메일 주소나 도메인을 SEC가 거부할 수 있으며, 그때는 다른 연락처를 사용해야 합니다.

Discord Developer Portal에서 Chairman 봇의 **Message Content Intent**만 켭니다. Presence Intent와 Server Members Intent는 현재 기능에 필요하지 않습니다.

## 실행과 종료

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_stock_agent.ps1
```

종료:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_stock_agent.ps1
```

상태 로그는 `data/runtime/stdout.log`, 오류 로그는 `data/runtime/stderr.log`에 저장됩니다. 실행 프로세스 ID는 `data/runtime/stock-agent.pid`로 관리됩니다.

## 지원 명령 예시

- `IONQ 지금 가격 알려줘`
- `IONQ 한 달 관점으로 분석해줘`
- `IONQ와 SOUN 중 하나만 고른다면 비교해줘`
- `지난번 IONQ 이후 달라진 점을 재분석해줘`
- `현재 PAPER 포트폴리오 보여줘`
- `진행 상태 알려줘`
- `IONQ 분석 취소`
- `지난 IONQ 보고서 다시 올려줘`

모호한 요청은 바로 실행하지 않고 명령채널에서 확인 질문을 합니다. 분석은 한 번에 하나씩 처리하며 중복 메시지는 데이터베이스에서 차단합니다.

## 안전 원칙

- `MODE=PAPER`만 사용하며 실제 주문 기능은 없습니다.
- Toss/SEC 장애 시 실데이터 분석을 실패 처리하고 mock 결과를 발행하지 않습니다.
- Discord 토큰과 API 키는 `.env`에만 두며 ZIP 배포본에서 제외합니다.
- 봇 메시지는 명령으로 처리하지 않아 봇 간 무한 루프를 방지합니다.
- 허용된 서버·채널·사용자만 명령을 내릴 수 있습니다.
- 토론은 최대 2라운드입니다.

## 테스트

```powershell
python -m unittest discover -s tests -v
```

현재 자동 테스트는 파서, 권한 필터, 중복 방지, 데이터베이스 마이그레이션, 토론 상한, 역할별 채널 라우팅, 보고서 경로 보호와 기존 위험 규칙을 포함합니다.

## v0.5 MCP/Hermes 프로필

`mcp_server.py`, `stock_agent/tool_service.py`, `hermes/` 및 일부 v0.5 스크립트는 향후 Hermes 직접 주도형 v2 실험을 위해 보존했습니다. v0.6 운영 런타임은 이 Gateway를 동시에 실행하지 않으며 `main.py discord` 기반 Hybrid 구조만 사용합니다. 두 수신기를 동시에 켜면 명령이 중복 처리될 수 있습니다.
