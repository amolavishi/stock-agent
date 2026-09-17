# MAIN_RUNTIME_ARCHITECTURE_AFTER

## Canonical composition root

Production code must call `stock_agent.bootstrap.install_production_stack()` or import `stock_agent.production`. The package root remains side-effect-light. No production entrypoint is allowed to assemble a private patch order.

```text
Production entrypoint
  -> bootstrap.install_production_stack()
      -> Alpha breadth/provider
      -> Catalyst acquisition
      -> V8 PRIMARY process semantics
      -> Grade-quota + blind-packet firewalls
      -> HUNT v16/v17
      -> Candidate/evidence integrity v18/v18.1/v18.2
      -> upstream terminal capture
      -> V8 NEXT successor + certification v1.1 + runtime
      -> Python evidence-origin authority
      -> V8 MAIN discovery schema/integrity preparation
      -> exact V8 source fidelity
      -> real V8 MAIN 02..14 scanner executor
      -> MAIN stock_scout aggregation
      -> persistent Secondary/Near-Miss post validator
      -> source gate / technical-debt conservation
      -> partial Market Context discovery admission
      -> terminal restore
      -> Shadow health / pointer / non-evaluable guards
```

The `DiscoveryRecallLiteProductionStockAgent` runtime is **not** installed. Its live provider/breadth adapter may be reused; Python keyword heuristics are not V8 qualitative scanner authority.

## State machine

```text
Step00A orchestration
  |
Step01 Market Context
  |-- core data absent/stale ------------------> NOT_EVALUABLE_PRE_DISCOVERY
  |-- non-core PARTIAL ------------------------> Discovery may continue
  v
Broad U.S. universe
  -> deterministic universe/liquidity normalization
  -> RAW / ELIGIBLE / CONTEXT-ONLY lineage
  v
02..14 V8 source scanners
  for each scanner:
    exact source bytes -> SHA check -> round chunks -> concrete model call
    -> structured schema -> strategy-specific dimensions
    -> model-call receipt -> round metrics -> authoritative scanner receipt
  v
Scanner union / Secondary / Near-Miss / rejection sentinel
  v
workflow.stock_scout
  - sole final DiscoveryCandidateSet owner
  - cannot create Research Grade
  v
Cheap fatal-only prescreen
  |-- verified hard fail -> INVESTMENT_REJECT
  |-- UNKNOWN -----------> evidence debt / full review
  v
Deep Research + Catalyst revalidation + Full SEC
  |-- candidate-scoped engineering error -> candidate failure ledger; other names continue
  v
Step15 Capital Structure / FD Share Bridge
  v
Step16 Blind Atomic Claim / Evidence-Origin Audit
  - no Discovery score/rank/scanner/quota/PRE-A/target/action fields
  v
Step17 Canonical Evidence Packet
  v
Step17.5 Critical Assumption Audit
  v
Step18 Python-finalized independent certification
  - only Research Grade writer
  - score starts at zero
  v
Step20 pure validator
  |-- incomplete -> RETURN_TO_STEP15/16/17/17.5/18
  `-- valid ------> certified result
  v
PRE-A sidecar only if authoritative Grade == B+
  - read-only trajectory
  - no grade/action/size/broker authority
  v
Execution review (if requested)
  - MarketExecutionGate strict
  - Python risk/allocation authority
  - Shadow broker writes = 0
```

## Search-stop authority

A broad HUNT may cleanly stop only when all are true:

1. all mandatory 02..14 scanners have validated execution receipts;
2. scanner signal coverage >= 150 unique strategy-eligible names;
3. rejection sentinel is complete with no systematic false-negative flag;
4. persistent HIGH research-value Secondary debt is zero;
5. unresolved HIGH near-miss debt is zero;
6. at least two consecutive complete 13-scanner system rounds have zero new signal, zero new Secondary and zero new independent evidence;
7. source/budget exhaustion is explicitly documented.

`deep_dive_yield == 0`, raw ticker count alone, or a weak market regime cannot independently authorize stop.

## Authority boundaries

| Layer | May discover/rank | May reject | May write Research Grade | May write execution action |
|---|---:|---:|---:|---:|
| 02~14 scanners | Yes | Cheap verified structural fail only | No | No |
| stock_scout aggregation | Yes | Discovery routing only | No | No |
| Python hard gates | No | Yes, by explicit rule/evidence | No | No |
| Step16/17/17.5 | No | Audit/return/research debt | No | No |
| Step18 | No | Certification caps/exclude | **Yes** | No |
| Step20 | No | Return route only | No | No |
| PRE-A | No | trajectory status only | No | No |
| Execution/Risk Python | No | Yes | No | **Yes** |
