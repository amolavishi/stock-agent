# 05 TARGET STATE MACHINE

## Candidate state machine

```text
DISCOVERED
  -> WATCH / NEXT_STAGE                          (discovery routing only)
  -> RESEARCHING
      -> ENGINEERING_FAILURE                    (provider/model/schema/pipeline failure)
      -> EVIDENCE_DEBT                          (insufficient but unresolved evidence)
      -> VERIFIED_REJECT                        (evidence-backed thesis/hard-gate failure)
      -> STEP18_GRADED
          A/A- + Step20 PASS -> PASS
          B+   + Step20 PASS -> NEXT_STAGE(PRE-A eligible)
          B    + Step20 PASS -> WATCH/NEXT_STAGE
          EXCLUDE + Step20 PASS -> REJECT
          any grade + Step20 RETURN -> NOT_EVALUATED until return path resolved
```

No transition from ENGINEERING_FAILURE/EVIDENCE_DEBT/DATA_BLOCK to REJECT is legal without a new verified investment-failure receipt.

## Search state machine

```text
UNIVERSE_DECLARED
  -> PARTIALLY_PROBED
  -> MINIMUM_OPERATIONAL_PROBE_MET
  -> OPERATIONAL_LIMIT_REACHED (optional)
  -> PROVIDER_BUDGET_EXHAUSTED (optional, still debt)
  -> FULL_UNIVERSE_RECONCILED
  -> SOURCE_EXHAUSTED
```

`OPERATIONAL_LIMIT_REACHED` is not an alias for `SOURCE_EXHAUSTED`.

## Run-evaluation state machine

```text
collect validated receipts
  -> build SourceExhaustionProof
  -> build CandidateConservation ledger
  -> build RunEvaluationProof
  -> derive one terminal state
```

`NO_TRADE` is legal only from a PASSing `RunEvaluationProof` with no qualified A/A- candidate.

Any of the following forces NOT_EVALUABLE / SEARCH_DEBT instead:
- source integrity failure
- incomplete scanner execution/validation/coverage
- unresolved high-value secondary/near-miss debt
- candidate engineering failure
- unresolved candidate evaluation
- Step20 return debt
- market execution context failure
- false/absent source exhaustion or universe reconciliation proof

## Authority separation

- Discovery scanners: research routing only.
- Step15/16/17/17.5: evidence/capital/assumption audit only.
- Step18 Python finalizer/validator: only Research Grade writer.
- Step20: research completeness/routing validator only.
- PRE-A: B+ trajectory sidecar only.
- Execution: downstream action layer only; never back-propagates into grade.

## Final-owner constraint

The existing final pre-live sentinel class remains the outer production owner. New semantics should be implemented as canonical pure functions/receipts consumed by that final owner rather than by adding another wrapper outside it.
