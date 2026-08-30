# Architecture Decisions

## AD-001 — SQLite WAL remains MVP authority

Decision: retain SQLite WAL on local NTFS with bounded workers. Architecture
v1.1 explicitly targets a single Windows host; PostgreSQL is deferred until
multi-host or sustained lock contention is demonstrated. Repository interfaces
prevent vendor leakage.

## AD-002 — Prompt Library remains a reasoning contract

Decision: do not move workflow, gates, risk, sizing, or final action into
prompts. The final ZIP's `PROMPT_LIBRARY_ARCHITECTURE_V2_2.md` confirms these
are outside library scope.

## AD-003 — External adapters are explicit MISSING boundaries

Decision: absence of Toss/SEC/Obsidian/DeepSeek source code is not permission to
delete those capabilities or invent endpoints. Implement typed providers with
recorded adapters first, then verify live capability.

## AD-004 — Obsidian is projection, not database

Decision: SQLite verified state renders into Prompt Vault/Knowledge Vault;
projection failure is independently retryable and never rolls back authority.

## AD-005 — Preserve current facade during migration

Decision: keep `StockAgent` and current tests as compatibility shell while
extracting services. This limits migration risk and prevents a Big Bang rewrite.

## AD-006 — Freshness is domain-scoped

Decision: replace the current simple/global evidence hash behavior with
subject/domain fences and graph invalidation. Unrelated evidence must not stale
every result; critical refresh must not leave active dependent conclusions.

## AD-007 — Read-only portfolio import

Decision: portfolio snapshots are imported receipts. The Stock Agent may propose
actions but cannot mutate broker/portfolio authority in this architecture.

## Decision gate

Current code is not production-complete, but the repository is sufficiently
understood for staged implementation. The initial architecture package
contains no unresolved design conflict in authority boundaries; the blocking
items are implementation P0/P1 gaps listed in the gap analysis and should be
closed phase-by-phase.

## Final architecture-stage verdict

`ARCHITECTURE_READY_FOR_IMPLEMENTATION`
