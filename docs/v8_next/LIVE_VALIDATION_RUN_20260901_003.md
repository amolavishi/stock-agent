# V8 NEXT Live Validation — RUN-20260901-003

## Source run

- Shadow run: `RUN-20260901-003`
- HUNT run: `run-e25dc15b1f794922a3d2a74ff7cb2481`
- Branch: `audit/adversarial-integrity-v18-20260901`
- Tested SHA: `edb7638343eec8927505e3fc5e762c400c5e12fe`
- Shadow: `SHADOW_V1.3`
- Investment policy: `V8_NEXT_PRE_A_2026-09-01_R1`
- Luna health: `PASS` (`SHADOW_HEALTH_V1.9`)
- Market health: `PASS`
- Broker writes: `0`

## Live result

This run was **not an acceptance pass**.

The authoritative HUNT finished with `status=FAILED` and `outcome=NOT_EVALUABLE_DISCOVERY_COVERAGE`. The discovery funnel was zero at raw/eligible/discovered/deep/qualified and SEC/Research were not reached.

The Shadow projection incorrectly emitted `status=SUCCEEDED`, `investment_conclusion=NO_TRADE`, and `investment_conclusion_is_clean_no_trade=true`. PRE-A also skipped with `observed 0` because its report locator searched only one directory level although the canonical path is `<shadow-root>/<date>/<run-id>/DAILY_REPORT.md`.

## Root-cause classes exposed

1. **Shadow terminal-state vocabulary mismatch** — base Shadow recognized `BLOCKED_*` but not the newer `NOT_EVALUABLE_*` HUNT terminal states.
2. **V8 NEXT coverage root-cause overwrite** — the 150-name breadth guard could rewrite a prior provider/contract/pipeline failure as `NOT_EVALUABLE_DISCOVERY_COVERAGE`.
3. **Broad-live reporting identity drift** — the current `composite-live-market-alpha-v13` provider was not recognized by the older broad-provider name list.
4. **PRE-A artifact locator depth bug** — `_snapshot_reports()` used a one-level glob instead of the canonical date/run tree.

## Repairs after RUN-003

### Shadow non-evaluable guard

Added `stock_agent/shadow_non_evaluable_guard.py` (`SHADOW_V1.4_NON_EVALUABLE_GUARD`).

- `NOT_EVALUABLE_*` can never become clean `NO_TRADE`.
- `BLOCKED_*`, pipeline and pre-discovery failures remain non-evaluable rather than investment rejection.
- only an evaluable `NO_QUALIFIED_CANDIDATE` may become clean `NO_TRADE`.
- Shadow is degraded when the authoritative HUNT is non-evaluable.
- broad-live identification covers the current composite alpha provider.

### V8 NEXT terminal lineage

Added `stock_agent/v8_next_terminal_lineage.py` (`V8_NEXT_TERMINAL_LINEAGE_V1.0`).

- capture the upstream HUNT outcome and blocked reason before V8 NEXT coverage post-processing;
- preserve coverage telemetry without using the breadth guard as an error classifier;
- restore an upstream `BLOCKED_*` / `NOT_EVALUABLE_*` terminal reason after coverage accounting.

This allows the next live failure, if any, to expose the actual universe/provider/contract root cause instead of losing it behind a generic coverage failure.

### PRE-A locator and eligibility guard

Updated `stock_agent/daily_with_pre_a.py`.

- report discovery now scans `<root>/*/*/DAILY_REPORT.md`;
- PRE-A reads the sibling `RUN_LOG.json` before sidecar execution;
- non-evaluable/degraded PRIMARY runs produce an explicit fail-closed PRE-A skip rather than the false `observed 0` error.

## Regression coverage

Added/updated tests for RUN-003 non-evaluable semantics, pre-discovery failure semantics, evaluable clean NO_TRADE, two-level Shadow artifact discovery, PRE-A non-evaluable skip, upstream terminal preservation, report rendering, and production-test process isolation.

## CI after repairs

Production source reached the repaired stack by `80c9968108398ff3517ad34831434e7827d0ec3e`. At that source state these dedicated regressions passed: V1.6 pipeline, Alpha Discovery, V1.7 resilience, V8 PRIMARY, V1.8 integrity, Shadow Sentinel, and Catalyst Acquisition.

Subsequent changes were test-isolation / CI-trigger / documentation only.

At `4c281d3fc04799dd5d6b281b06f551eca33b0252`:

- full unittest suite: PASS on Ubuntu 3.11/3.12 and Windows 3.11/3.12;
- Prompt Library contract validation: PASS;
- tracked-tree mutation check: PASS;
- hostile integrity regression: PASS on Ubuntu 3.11/3.12 and Windows 3.11/3.12.

## Acceptance status

`RUN-20260901-003` remains a failed live-validation run and must not be treated as a clean investment `NO_TRADE`.

A further live run is required after pulling the latest audit branch. The next run must either:

1. progress into non-zero broad discovery and continue through the research funnel, or
2. preserve and report the exact upstream provider/contract/pipeline root cause as non-evaluable.

Main merge remains blocked until live broad discovery is demonstrated or the remaining live provider defect is identified and repaired.