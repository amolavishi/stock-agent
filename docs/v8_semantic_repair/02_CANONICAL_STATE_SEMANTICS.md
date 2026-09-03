# 02 CANONICAL STATE SEMANTICS

## Design principle

`One Fact -> One Meaning -> One Authoritative State -> Consistent Downstream Projection`.

Evaluation completeness and investment conclusion are orthogonal.

## Candidate states

- `PASS`: completed evaluation, A/A- certified and Step20 PASS.
- `NEXT_STAGE`: completed evaluation requiring non-execution follow-up, including B+ PRE-A eligibility.
- `WATCH`: completed non-executable observation state, including B where policy chooses watch semantics.
- `REJECT`: completed investment rejection supported by verified thesis/hard-gate evidence.
- `NOT_EVALUATED`: required evaluation/certification is missing or Step20 returned upstream.
- `ENGINEERING_FAILURE`: model/schema/pipeline/transport failure prevented valid evaluation.
- `EVIDENCE_DEBT`: evidence is insufficient but not proven false.
- `DATA_BLOCK`: scanner/provider/data failure prevents assessment of a supplied security.
- `SOURCE_EXHAUSTED`: evidence/source traversal was actually completed and no further source remains under the authoritative source contract. This is not an investment conclusion.

## Non-alias invariants

`ENGINEERING_FAILURE != REJECT`
`EVIDENCE_DEBT != REJECT`
`DATA_BLOCK != REJECT`
`NOT_EVALUATED != REJECT`
`SOURCE_EXHAUSTED != REJECT`
`WATCH != NOT_EVALUATED`
`B+ != NOT_EVALUATED`
`B != NOT_EVALUATED`
`EXCLUDE != NOT_EVALUATED`

## Grade semantics

- A/A-: completed Research Grade, execution-eligible only if all later execution gates pass.
- B+: completed Research Grade, non-executable, PRE-A source eligible.
- B: completed Research Grade, non-executable watch/next-stage state.
- EXCLUDE: completed Research Grade conclusion and investment rejection.
- Missing/invalid Step18: not a grade; NOT_EVALUATED/EVIDENCE_DEBT/ENGINEERING_FAILURE according to cause.

## Failure classes

- Provider/model/schema/transport/pipeline failure -> engineering/data state, never thesis rejection.
- Missing evidence/critical unknown -> EVIDENCE_DEBT or NOT_EVALUATED.
- Verified toxic structure / thesis contradiction / hard gate fail -> REJECT.
- Step20 RETURN -> NOT_EVALUATED until the return path is resolved; stored Step18 grade is historical, not currently qualifiable.

## Search states

- `MINIMUM_OPERATIONAL_PROBE_MET`: count threshold only.
- `OPERATIONAL_PROBE_LIMIT_REACHED`: operational budget/cap only.
- `PROVIDER_BUDGET_EXHAUSTED`: provider budget ended; unresolved search debt remains unless the universe is otherwise reconciled.
- `FULL_UNIVERSE_RECONCILED`: authoritative denominator, eligibility and traversal reconciled.
- `SOURCE_EXHAUSTED`: true source-end proof; never inferred from a numeric probe cap.
- `SEARCH_DEBT_REMAINS`: unresolved names, scanners, sources, sentinel findings, or high-value secondary debt remain.

## clean NO_TRADE

Allowed only when a canonical run-evaluation proof demonstrates complete evaluability, no unresolved engineering/data/search debt, valid source/universe closure, terminal conservation for all relevant candidates, and no qualified A/A- candidate.
