# SQLite / Obsidian Contract

## SQLite authority

SQLite is the sole operational source of truth for Run, WorkItem,
EffectiveRuleSet, Evidence, Claims, snapshots, GateResults, RiskAssessment,
QualifiedCandidatePool, FinalAction, provider calls, cost, reports and outbox.
All writes use short `BEGIN IMMEDIATE` transactions with expected status/version
checks. SQLite uses local NTFS, WAL, FK, FULL synchronous mode and migration
lock; the DB is not placed on OneDrive/network shares.

## Required vNext repositories

Introduce repository interfaces rather than passing ORM/SQLite rows into agents:

```text
RunRepository, WorkItemRepository, RuleRepository,
SecurityRepository, MarketSnapshotRepository, SECRepository,
EvidenceRepository, ClaimRepository, GateRepository,
PortfolioRepository(read-only import), RiskRepository,
PromptExecutionRepository, ProviderCallRepository,
QualifiedPoolRepository, FinalActionRepository,
ProjectionOutboxRepository, ReportRepository
```

Every derived row carries dependency hash/epoch, rule-set hash, context hash,
schema/prompt version and created/superseded status. No delete is used to hide
bad evidence or stale results.

## Obsidian contract

Obsidian has two vaults or clearly separated roots:

1. Prompt Vault: immutable Prompt Library v2.2 files, registry, schemas and
   validation artifacts.
2. Knowledge Vault: `Companies/<security_id>/`, `Industries/`, `Markets/`,
   `Reports/`, `Runs/` durable human-readable projections.

Only validated SQLite state may be rendered. A projection includes source
row/version/hash and generation timestamp. It never writes back operational
state. Projection failure creates a retryable `ProjectionJob`/outbox entry and
does not rollback FinalAction or Run completion.

FIRST_TOUCH writes a baseline projection; DELTA writes a revision that links
the superseded baseline and evidence changes. A critical evidence invalidation
must create a new projection revision, not silently edit history.
