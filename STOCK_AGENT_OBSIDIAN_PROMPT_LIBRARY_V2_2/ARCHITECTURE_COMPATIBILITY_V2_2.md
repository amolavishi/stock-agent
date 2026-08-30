# Architecture Compatibility v2.2

대상은 **Stock Agent Architecture v1.1**이며 v1.2/v1.2.1 기능을 선반영하지 않았다.

| Architecture v1.1 경계 | v2.2 구현 위치 | 결과 |
|---|---|---|
| Python owns control/gates/state | Grounding + runtime contract | PASS |
| strict structured output | typed registry + Prompt-body canonical lint + executable tests | PASS |
| HUNT_ONLY terminal separation | runtime mode contract + dependency test | PASS |
| StageGate final eligibility | Discovery 직후 typed StageGate receipt; Prescreen/Deep prerequisite | PASS |
| CapitalPrescreenGate authority | extraction/gate receipt split | PASS |
| freshness fence | blocking status/action conditional | PASS |
| FinalAction single writer | Python FinalAllocationGate owner preserved | PASS |
| Fresh Money 0..1 | runtime authority invariant | PASS |

Investment Rules v2.0의 Discovery/Execution enum, STARTER→ADD→FULL, Opportunity Cost, Cash Bias 금지, 세 Risk Metric 분리 의미를 유지했다.


