# V8 Canonical PRIMARY Contract

Status: **AUTHORITATIVE RESEARCH-PROCESS CONTRACT**  
Runtime version: `V8_PRIMARY_CANONICAL_V1.0`

The daily PRIMARY runtime adopts the V8 process semantics without granting discovery code any grading, sizing, or broker authority. The immutable source reference is `SOURCE_MANIFEST.json`; `README_V8_PIPELINE.md` is copied from the V8 source bundle identified there.

## Canonical stage semantics

| V8 Step | PRIMARY meaning | Authority |
|---|---|---|
| 00A | Top-down + mandatory bottom-up orchestration; breadth management | discovery resource allocation only |
| 01 | Market/sector context | context only; weak regime cannot auto-kill an idiosyncratic candidate |
| 02–14 | HUNT_ONLY discovery lanes | `DISCOVERY_PRIORITY_SCORE`/weakness/UNKNOWN/verification questions only; **no Research Grade** |
| Cheap veto | price/cap/ADV/Stage plus explicit fatal capital facts | explicit fatal TRUE may reject; UNKNOWN becomes evidence debt |
| 15 | Full SEC/dilution/capital forensic | no Research Grade |
| 16 | weakness/UNKNOWN-first adversarial verification | blind to discovery score/rank/prior grade/target/size; no Research Grade |
| 17 | evidence packet + score firewall | certification input only |
| 18 | independent 0→105 A-grade certification | first and only Research Grade writer |
| 19 | portfolio/cash competition and risk-budget execution | Python-authoritative execution boundary |
| 20 | pure validator | cannot create new research, grade, target, probability, or action |

## Non-negotiable invariants

1. `Discovery Priority != Research Grade != PRE-A Readiness != Execution Action`.
2. Discovery optimizes recall; certification optimizes precision.
3. `UNKNOWN` is never silently converted to `FALSE` or `REJECT`.
4. Cheap prescreen is **fatal-veto only**. Missing SEC fields create `V8_EVIDENCE_DEBT` and request full forensic work.
5. Explicit hard capital/dilution dangers remain rejectable; V8 does not weaken hard risk gates.
6. Step 16/18 must be blind to discovery scores/ranks and earlier grades.
7. Step 18 resets score to zero and applies V8 hard gates/grade caps. Other stages cannot compensate around a cap.
8. Fewer than five A-/A results triggers `SEARCH_EXPANSION_REQUEST`; thresholds never relax and B+ is never promoted to fill quota.
9. Top-down and bottom-up run together. Market/sector weakness may change resource allocation and later sizing, not eliminate company-specific discovery.
10. PRE-A is a trajectory/evidence-debt tracker only. A PRE-A trigger requests blind Step-18 recertification; it never auto-promotes or writes an action.

## RUN-008 correctness requirement

A run with a large discovered pool may not report clean `NO_TRADE` merely because cheap SEC packets are incomplete. For each discovered candidate that reaches capital screening, one of these must be observable:

- explicit fatal veto/reject,
- `PASS`,
- `PASS_WITH_CONSTRAINTS` with evidence debt/full-forensic escalation,
- explicit provider/staleness failure.

`discovered > 0`, zero prescreen receipts, and zero provider/staleness failures is an **engineering incident**, not a market conclusion.

## Current implementation boundary

`V8_PRIMARY_CANONICAL_V1.0` immediately enforces the V8 front-end/grade-firewall semantics and fixes UNKNOWN→silent-drop behavior. The existing PRIMARY Python qualification gates remain the authoritative candidate gate while the separate evidence-backed 105-point Step-18 writer is implemented. Until that writer exists, PRIMARY is **not permitted to manufacture A/A-/B+/B labels in earlier stages**. This is intentionally fail-closed against grade inflation while no longer being fail-closed against discovery recall.
