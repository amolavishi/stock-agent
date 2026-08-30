# Regression and E2E Test Plan

## Baseline

Run `python -m unittest discover -s tests -q` and the bundled Prompt Library
validator before and after each phase. Existing 22 tests remain mandatory but
fixtures will be migrated to recorded provider inputs.

## Discovery acceptance

1. Full Market→Sector→Industry→Stock→Stage→Prescreen→Research→SEC→Audit→Pool
   with RecordedMarket/SEC/LLM providers.
2. Empty universe returns `NO_QUALIFIED_CANDIDATE`.
3. Market Context PARTIAL may continue discovery; missing usable context does not.
4. Stage 3, identity conflict, toxic capital, missing SEC, missing audit and
   unresolved CRITICAL fail closed.
5. HUNT_ONLY database contains no ExecutionAction, position size, FinalAction,
   or NO_TRADE row.

## Execution acceptance

6. Fresh market/portfolio/price/risk inputs reach MarketExecutionGate.
7. Missing/stale core execution data blocks capital action but does not rewrite
   discovery results.
8. Risk arithmetic uses stop/gap/event/portfolio budgets; caller shares are
   ignored; starter arithmetic and maximum position are rechecked.
9. Existing ADD requires matching position receipt, prior trigger, strengthened
   evidence, same security/lineage and fresh risk capacity.
10. Multiple existing TRIM/EXIT actions are allowed; fresh-money positive
    commitments are transactionally limited to 0..1.

## Adversarial/failure tests

- stale market, SEC, evidence, context, rule and dependency hash;
- late worker result after refresh (`STALE_ON_ARRIVAL`);
- duplicate WorkItem lease, duplicate retry/commit, expired lease reclaim;
- malformed JSON, schema violation, semantic violation, repair exhaustion;
- DeepSeek timeout/429/5xx/auth failure/circuit breaker/cost ceiling;
- Toss unsupported endpoint, stale quote, provider outage;
- SEC identity ambiguity, rate-limit, missing filing and wrong accession;
- Obsidian write failure/retry without DB rollback;
- SQLite crash/restart, WAL backup/integrity failure, migration rollback;
- incomplete ContextManifest, unauthorized RuleOverride, prompt composition
  with multiple output owners;
- unresolved CRITICAL, EvidenceConflict, thesis invalidation and generation cap;
- wrong ticker/position receipt, price-only ADD, Stage 3, legacy enum and
  Discovery-to-execution leakage.

Each case asserts both returned outcome and authoritative DB rows; passing
narrative output alone is not acceptance.
