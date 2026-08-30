# Implementation Plan

Big-bang rewrite is explicitly prohibited. Each phase has a compatibility
boundary and must leave the existing 22-test contract suite runnable.

## Phase 0 — inventory and contracts

- Freeze package/reference hashes and create source manifest.
- Add typed config/secret redaction and command idempotency.
- Approve target schema/API contracts and migration harness.
- Exit: docs approved, no code path changed.

## Phase 1 — persistence/runtime foundation

- Add migration version table, integrity/backup checks, typed Run/WorkItem
  state machine, prerequisite query, pause/cancel, CAS, generation limits,
  recovery manager and watchdog.
- Keep `StockAgent` facade delegating to new services.
- Exit: duplicate lease, crash/restart, stale late commit tests pass.

## Phase 2 — providers and raw artifacts

- Add MarketDataProvider and SECProvider protocols plus recorded adapters.
- Verify Toss capabilities before implementing any endpoint.
- Add raw artifact/provenance/rate-limit/cache models; no gate consumes raw
  caller fixture conclusions.

## Phase 3 — normalization/evidence/knowledge

- Implement SecurityMaster, Market/SEC normalizers, Evidence/Claim graph,
  source priority/conflict, subject/domain freshness and invalidation.
- Add FIRST_TOUCH/DELTA KnowledgeLoader.

## Phase 4 — prompt/provider runtime

- Split compiler/router/provider execution from current `prompt_runtime.py`.
- Persist PromptExecution/ModelCall/CostReservation FSM; implement structured
  repair and provider error normalization.
- Implement DeepSeek only after transport/config verification; keep Recorded
  acceptance path.

## Phase 5 — deterministic discovery

- Implement TechnicalFeatureCalculator and versioned StageClassifier.
- Build market context → sector → industry → stock WorkItem DAG and Python
  Stage/Eligibility/Catalyst/Expectation gates.

## Phase 6 — capital/research/SEC

- Add cheap SEC prescreen before deep research.
- Implement Deep Research, mandatory Full SEC Forensic and independent Audit
  with persisted result/dependency contracts.

## Phase 7 — qualified pool

- Add DB-derived QualifiedCandidatePool and HUNT_ONLY terminal projection.
- Prove no execution leakage at API and DB levels.

## Phase 8 — execution/risk/portfolio

- Add fresh MarketExecution and read-only PortfolioSnapshot import.
- Split RiskEngine from PositionSizer; implement stop/gap/event budgets,
  OpportunityCost and CashBias checks.

## Phase 9 — final allocation

- Implement live FinalAllocationGate transaction, FinalAction constraints,
  ADD evidence lineage, existing reductions and fresh-money guard.

## Phase 10 — projections/reporting

- Add SQLite consistent report renderer, atomic artifacts/outbox and Obsidian
  knowledge projection with independent retry.

## Phase 11 — operational hardening

- Windows service/scheduler, provider circuit breakers, backups, metrics,
  CI, concurrency/load tests, adversarial acceptance and live adapter contract
  tests.

No phase may mark the next layer complete while a P0 authority or freshness gap
remains open.
