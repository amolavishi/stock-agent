# Discovery Common Contract — V8.4

## Authority
Applies to scanner modules `02~14` and any `EXT-*` scanner. Scanner-specific modules may add stricter rules but may not relax this contract.

- `RUN_MODE = HUNT_ONLY_RECALL_FIRST`
- Discovery never creates Research Grade or Execution Action.
- `DISCOVERY_PRIORITY_SCORE` is scanner-local only; cross-scanner numeric ranking is prohibited.
- Cross-scanner routing uses `SIGNAL_STRENGTH`, `RESEARCH_VALUE`, evidence tier, event proximity, and cheap-fatal-risk.

## Canonical universe and cheap screen
Use `CANONICAL_US_UNIVERSE_RULES.md`. Every canonical eligible ticker receives the universal cheap screen and scanner eligibility routing. `UNKNOWN != NEGATIVE`.

Cheap fatal fail is limited to confirmed identity/listing/liquidity failure, toxic capital structure, serious accounting integrity failure, non-operating security mismatch, or true extreme Stage3 economic overrun. Missing expensive research is `DISCOVERY_INSUFFICIENT`, not a fatal fail.

## Evidence and PIT
All factual evidence uses `SOURCE_REGISTRY`; every source_id must resolve. Evidence timestamp must be <= current evidence cutoff. Status is explicit (`VERIFIED/PARTIAL/UNKNOWN/CONFLICT/STALE`). Same economic event cannot be counted as multiple independent fuels.

## Common discovery output
Allowed states: `DEEP_DIVE_NOW`, `DEEP_DIVE_SECONDARY`, `WATCH_STAGE0`, `WATCH_RESET`, `EARLY_TRAJECTORY`, `EXCLUDE`.

Each candidate must contain:
- primary signal family + at most two secondary signal families
- verified economic delta or explicit unknown
- 1~8w verification/catalyst hypothesis
- price/stage status
- cheap capital-structure status
- weakness packet (at least 3 material categories when available; do not fabricate filler)
- source_ids and economic_event_ids
- next verification questions

## Search Ledger and receipt
Every scanner writes ticker-level entries conforming to `SCHEMAS/SEARCH_LEDGER_SCHEMA.json` and one receipt conforming to `SCHEMAS/SCANNER_EXECUTION_RECEIPT_SCHEMA.json`.

`SIGNAL_SCAN_COMPLETE` requires an explicit eligible-universe denominator and `coverage_ratio = scanned_count / eligible_universe_count`. A scanner may terminalize with SOURCE_EXHAUSTED/DATA_BLOCKED/EVENT_PENDING/INTEGRITY_BLOCKED only with a reason.

`LANE_TOUCHED != SCANNER_EXECUTED`; `BREADTH_SCAN != SIGNAL_SCAN`; `DEEP_DIVE_YIELD_0 != SEARCH_STOP`.

## Blind handoff
Discovery score/rank/priority, Early-Trajectory metadata, persuasive bullish conclusions, old targets/probabilities/grades, and PRE-A metadata are forbidden in Step17 certification packet. Only factual claims, sources, weaknesses, unresolved questions and neutral event IDs survive serialization.

## Search continuation
Candidate count/A-count is not a stop criterion. Continue while there is meaningful new signal, independent evidence, secondary queue, high research-value near miss, or uncovered signal family. Stop only after scope coverage, scanner terminalization, secondary/anti-confirmation/residual work, and marginal-yield evidence support it.
