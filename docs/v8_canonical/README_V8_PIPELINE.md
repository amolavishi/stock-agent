# STOCK SCANNING PROMPTS V8 — A-Grade Discovery / Certification Pipeline

## 목적

기존 시스템의 핵심 문제는 두 가지였다.

1. Discovery 단계에서 최종 검증규칙을 너무 일찍 적용하여 **진짜 A급 후보 recall이 낮아짐**.
2. 반대로 조건을 조금 완화하면 좋은 펀더멘털의 B/B+가 **A-/A로 등급 인플레이션**되는 문제.

V8은 이를 **Grade Firewall**로 분리한다.

> **앞단: 넓게 찾고 점수+약점 수집 → 중단: 점수 숨기고 약점 공격 → 후단: 0점에서 A급 재인증 → 마지막: 포트/리스크로 실행**

---

## 실행 순서

1. `00A` — 오케스트레이션, Top-Down + Bottom-Up 병행, 전체 breadth 관리
2. `01` — Market/Sector Context
3. `02~14` — HUNT_ONLY Discovery
   - `DISCOVERY_PRIORITY_SCORE`
   - 강점
   - **약점 3~7개**
   - UNKNOWN
   - `BLIND_VERIFICATION_PACKET`
   - **Research Grade 금지**
4. `15` — Full SEC Forensic
5. `16` — **약점/UNKNOWN 전용 적대적 심층검증**
   - Discovery Score 블라인드
   - Fundamental/Expectation Gap/Catalyst/Valuation/Stage 독립검증
   - A급 등급 금지
6. `17` — Evidence Packet / Score Firewall
7. `18` — **A-/A급 최종 인증**
   - Discovery Score 사용 금지
   - 0점부터 105점 재채점
   - Hard Gate + Grade Cap
   - 여기서 처음 A/A-/B+/B 생성
8. `19` — 포트폴리오/Cash 경쟁, risk budget, 실제 action
9. `20` — Pure Validator

---

## 가장 중요한 변경

### 1. Discovery Score ≠ Research Grade

앞단 점수는 조사 우선순위일 뿐 A/A-와 아무 관계가 없다.

### 2. Blind Verification

Step 16/18에는 Discovery Score·순위를 보여주지 않는다.
초기 고득점에 대한 anchoring을 차단한다.

### 3. Weakness-First

모든 후보는 강점만큼 약점을 구조적으로 남긴다.
Expectation Gap/Catalyst/Valuation/SEC/Stage의 미검증 항목을 후단 검증 큐로 넘긴다.

### 4. Grade Cap

점수가 90점이어도:

- expectation gap 미입증
- 1~8주 catalyst 약함
- valuation 재현 불가
- PW-EV 없음
- SEC 미완료

이면 최대 B+.

### 5. VCYT형 오탐 방지

- 좋은 headline 실적 뒤 큰 하락의 원인을 반드시 분석
- conference는 자동 strong catalyst가 아님
- consensus와 회사 guide가 같으면 expectation gap을 재검증
- 신규제품/보험급여는 실제 매출 timing이 없으면 단기 fuel로 과대평가 금지
- Base target의 multiple expansion 의존도를 공격

### 6. A급 부족 시 기준 완화 대신 탐색 확대

기본 목표 A-/A 5개.
5개 미만이면 `SEARCH_EXPANSION_REQUEST`로 00A에 되돌아간다.
B+ 승격은 금지.

### 7. Top-Down + Bottom-Up 병행

좋은 섹터만 찾는 구조를 폐기하지 않되, **company-specific anomaly 스캔을 항상 병행**한다.
약한 섹터에서도 특수상황 A급을 놓치지 않는다.

### 8. 기존포지션 downgrade ≠ 자동매도

등급이 낮아져도 현재 PW-EV, risk budget, 실제 대체후보가 더 중요하다.
몇 주 팔아 만든 소액 현금이 더 좋은 곳에 배치되지 못하면 현금 확보 자체를 이유로 기계적 TRIM하지 않는다.

---

## 최종 목표

- Discovery는 **False Negative**를 줄인다.
- Certification은 **False Positive / Grade Inflation**을 줄인다.
- Execution은 **리서치등급과 포지션 크기를 분리**한다.

이 세 목적을 한 프롬프트에 몰아넣지 않는 것이 V8의 핵심이다.
