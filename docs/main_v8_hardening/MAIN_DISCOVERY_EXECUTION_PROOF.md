# MAIN_DISCOVERY_EXECUTION_PROOF

## Definition

`SCANNER_EXECUTED = TRUE` is permitted only when the complete chain below exists for that scanner round:

```text
canonical source file
 -> exact manifest SHA-256 match
 -> source-backed runtime prompt
 -> concrete provider/model request containing candidate universe packet
 -> model response
 -> scanner-specific JSON schema validation
 -> strategy-dimension validation
 -> persisted WorkItem/ModelCall
 -> persisted round receipt
 -> persisted authoritative scanner receipt
 -> output consumed by MAIN stock_scout aggregation / post-discovery ledger
```

Lane metadata, scanner name, inferred theme, generic `workflow.stock_scout`, Python keyword routing, or a default payload cannot satisfy this definition.

## Mandatory scanners

| ID | Strategy identity | Required strategy dimensions |
|---|---|---|
| 02 | Non-AI/non-semi broad blind | economic_change, catalyst_1_8w, price_lag, stage |
| 03 | Recent/Busted IPO revaluation | ipo_dislocation, operating_delta, lockup_resale |
| 04 | Turnaround earnings | operating_inflection, one_off_vs_structural, cash_conversion |
| 05 | Policy/defense/nuclear/critical minerals | funded_policy, issuer_materiality, realization_1_8w |
| 06 | Space/defense/ISR/aerospace components | funded_backlog, contract_quality, revenue_conversion |
| 07 | Underfollowed profitability improvement | profitability_inflection, cash_conversion, underfollowed |
| 08 | Offering/block/secondary absorption | offering_terms, dilution_float, absorption |
| 09 | Insider buy/buyback turnaround | open_market_purchase, buyback_execution, sbc_offset |
| 10 | Refinancing/bankruptcy-risk removal | refinancing_terms, maturity_covenant, interest_cashflow |
| 11 | Post-earnings revision lag | earnings_surprise, estimate_revision, abnormal_price_reaction |
| 12 | Customer concentration break | customer_concentration, second_customer_economics, diversification_realization |
| 13 | Fintech/healthcare/non-semi software rotation | branch_kpi, sector_rotation, stock_relative_strength |
| 14 | AI bottleneck expansion exception | bottleneck_directness, demand_evidence, per_share_economics |

## Receipt contract

Each round receipt persists at least:

- scanner_id / scanner_name
- canonical source_file / source_sha256 / source_integrity_status
- prompt_runtime_hash
- model_provider / model_name / reasoning_effort / router_profile
- run_id / round_id
- universe_input_count / unique_ticker_count / evaluated_count
- signal_count / partial_signal_count / secondary_count / deep_count
- excluded_count / unknown_count
- output_schema_version / output_validated
- started_at / completed_at / model_call_at
- execution_status / failure_class / contract_failures
- source_exhaustion
- grade_authority=false

The aggregate authoritative scanner receipt additionally contains `round_receipts` and may report `SIGNAL_SCAN_COMPLETE` only when every round validated.

## Multi-round breadth

- Round size: 75 names.
- Each universe chunk is evaluated by all 13 scanners.
- System breadth is the maximum unique names in a shared chunk, never the sum of 13 repeated evaluations.
- Minimum clean broad signal coverage: 150 unique strategy-eligible names.
- Search is not allowed to stop at 150 merely because the count was reached.

Per round/system-round telemetry includes:

`new_unique_tickers`, `cumulative_unique_tickers`, `new_deep_dive_now`, `new_secondary`, `new_high_research_value`, `new_signal`, `new_partial_signal`, `new_independent_evidence`, `duplicate_count`, `duplicate_saturation`, `marginal_candidate_yield`, `marginal_evidence_yield`, and source-exhaustion fields.

## Persistent recall debt

`DEEP_DIVE_SECONDARY` is persisted in `discovery_secondary_queue` with missing evidence, verification path, expected resolution, recheck trigger and expiry. Expiry transitions to WATCH, not PRE-A. Structural/thesis hard fails are excluded from Near-Miss/Secondary. HIGH unresolved Secondary or Near-Miss blocks a clean search stop.

## Source availability status

The source-fidelity layer is intentionally fail-closed. The canonical 02~14 files are represented by `SOURCE_MANIFEST.json`, and source copies have been located in the user's File Library, including 07, 08 and 11. Exact-byte transfer into the repository has not been asserted because File Library search references do not provide a safe byte-for-byte GitHub write path. A fresh production run therefore must provide exact sources through the configured source root/archive until they are vendored with matching hashes.

No runtime is allowed to substitute paraphrased scanner text and claim execution.
