# Data Flow and State Machine

## HUNT_ONLY

```text
CommandIntent
 -> frozen EffectiveRuleSet
 -> Market raw fetch/normalize -> MarketContextGate
 -> Sector/Industry snapshots -> SectorGate
 -> SecurityMaster + Market/Technical snapshots
 -> StageClassifier proposal -> Python StageGate
 -> Cheap SEC CapitalPrescreen -> Python CapitalPrescreenGate
 -> deterministic ranking
 -> Deep Research prompt/result
 -> Full SEC Forensic
 -> independent Audit/Debate
 -> live prerequisite query -> QualifiedCandidatePool
 -> NO_QUALIFIED_CANDIDATE or pool terminal
```

No ExecutionAction, sizing, Action Card, or NO_TRADE exists in this branch.

## HUNT_AND_EXECUTION_REVIEW

```text
QualifiedCandidatePool
 -> fresh MarketExecutionSnapshot -> MarketExecutionGate
 -> fresh read-only PortfolioSnapshot
 -> RiskAssessment (stops/gap/event/budget)
 -> PositionSizer
 -> EntryReadiness / PortfolioComparison / OpportunityCost
 -> non-authoritative Final Synthesis recommendation
 -> FinalAllocationGate live query + transaction
 -> FinalAction (0..1 fresh money; multiple existing reductions allowed)
```

## WorkItem state

```text
PENDING -> LEASED -> RUNNING -> SUCCEEDED
                         ├-> FAILED_RETRYABLE -> PENDING(available_after)
                         ├-> STALE_ON_ARRIVAL -> replacement PENDING
                         ├-> FAILED_FINAL
                         └-> CANCELLED
```

Lease claim uses `BEGIN IMMEDIATE`, prerequisite PASS, status/expiry CAS,
attempt increment and lease token. Result commit compares work id, attempt,
input/dependency/rule/context hashes and live subject fence in one transaction.

## Evidence lifecycle

```text
FETCHED raw -> NORMALIZED -> ACTIVE EvidenceSnapshot
                         ├-> SUPERSEDED (new version)
                         └-> INVALIDATED (bad/conflicting/stale)
```

Critical refresh traverses Claim→Research→Audit→Gate→Recommendation→FinalAction,
marks dependents stale/invalidated and creates bounded replacement WorkItems in
the same transaction. Unrelated evidence does not advance the subject/domain
fence.

## Run state transitions

Terminal outcomes are `NO_QUALIFIED_CANDIDATE`, `PARTIAL_COMPLETION`,
`COMPLETED`, `FAILED`, `CANCELLED`, or `MANUAL_REVIEW`. A stale result is never
promoted by retrying the old result; it is preserved as an audit artifact.
