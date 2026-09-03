# 01 CURRENT RUNTIME RECONSTRUCTION

Baseline branch: `audit/main-v8-adversarial-hardening-20260903`
Baseline HEAD: `394969038de1bb14e961e334e52a66098d4707cb`

## Canonical production path

`stock_agent.production` calls `bootstrap.install_production_stack()` and then exports the composed `runtime.ProductionStockAgent`. Package-root `stock_agent.ProductionStockAgent` remains an uncomposed/base symbol and therefore is not the canonical production entry point.

## Effective composition order

1. Alpha discovery policy / coverage
2. Discovery breadth provider only
3. Catalyst evidence acquisition
4. V8 primary policy + grade-quota firewall
5. Discovery recall firewall
6. HUNT V1.6 pipeline
7. HUNT V1.7 resilience
8. HUNT V1.8 / V1.8.1 / V1.8.2 integrity
9. Pre-successor terminal capture
10. V8 NEXT successor + certification v1.1 + runtime Step15/16/17/17.5/18/20
11. Evidence-origin V1.9
12. V8 MAIN scanner/source preparation
13. V8.4 exact-source fidelity
14. Scanner failure isolation
15. Pre-coach scanner executor
16. MAIN Discovery Coach
17. Discovery post/search-stop validator
18. Source gate
19. Recall conservation
20. Market/Discovery admission
21. Post-successor terminal restore
22. Pre-live integrity V2.0/V2.0.1
23. V8.4 semantic consistency
24. V8 system semantics V2.1 in-place patch
25. Shadow health/pointer/non-evaluable guards

## State flow

`ENTRY -> MARKET -> SECTOR -> DISCOVERY -> 02..14 SCANNERS -> SECONDARY/NEAR-MISS -> PRESCREEN -> RESEARCH -> FULL SEC -> AUDITS -> STEP15 -> STEP16 -> STEP17 -> STEP17.5 -> STEP18 -> STEP20 -> QUALIFICATION -> CONSERVATION -> RUN TERMINAL -> SHADOW -> PRE-A -> EXECUTION REVIEW`

## Authority map

| Stage | Primary authority | Failure meaning | Downstream |
|---|---|---|---|
| Market/Sector | Python contracts + model extraction | context/data failure | Discovery admission / run evaluability |
| Discovery/02..14 | HUNT_ONLY model scanners, Python receipts | DATA_BLOCK/UNKNOWN are not reject | Research routing |
| Prescreen/Research/SEC | Python gates + evidence providers | evidence/provider/engineering failure distinct from thesis reject | Audits |
| Step15-17.5 | extraction/audit only | incomplete/debt, no grade authority | Step18 |
| Step18 | Python finalizer/validator | sole Research Grade authority | Step20 |
| Step20 | Python research validator | PASS or RETURN; cannot rewrite grade | qualification/conservation |
| Conservation | currently `v8_system_semantics_v21` | should preserve evaluation and conclusion separately | terminal run state |
| Search stop | pre-live/post layers | currently mixes operational ceiling with source exhaustion | run terminal |
| Shadow/PRE-A | projections only | must never create/overwrite Research Grade | reporting only |

## Structural debt

The runtime still relies on a long chain of wrappers and in-place monkey patches. Semantics are therefore distributed across bootstrap order, `_run_strict()` wrappers, conservation helpers, search-stop helpers, and Shadow guards. The repair must create one canonical semantic vocabulary and one final run-evaluation proof without rewriting the entire engine.
