# First Live HUNT P0/P1 Emergency Repair Report

Date: 2026-08-20  
Scope: `STOCK_AGENT_FIRST_LIVE_HUNT_EMERGENCY_REPAIR_GITHUB_COMMAND_2026-08-20.md`

## Verdict

The strict Python/SQLite runtime and recorded acceptance path are repaired and
independently re-executed. Live Toss/SEC/non-SEC credentials were not used in
this run, so this is not a production/live-readiness declaration.

## Issue → Reproduction → Root Cause → Files Changed → Fix → Regression Test → Residual Risk

### P0 — ad-hoc report was not authoritative

- Reproduction: the prior candidate Markdown had no matching run/receipt chain.
- Root Cause: no Python report boundary; CLI emitted only an unbound JSON summary.
- Files Changed: `stock_agent/reporting.py`, `stock_agent/cli.py`, `stock_agent/__init__.py`.
- Fix: `AuthoritativeHuntReportRenderer` requires a terminal `run_id`, reads only
  SQLite `runs/work_items/stage_results/evidence/raw_artifacts/discovery_funnel`,
  rejects missing receipts, and writes an atomic run-bound Markdown projection.
- Regression Test: `test_report_requires_run_id`,
  `test_report_is_bound_to_sqlite_run_and_receipts`.
- Residual Risk: live report generation remains NOT_RUN until live providers are
  executed through this same CLI path.

### P0 — non-SEC links could bypass persisted Evidence

- Reproduction: seeded fixture Evidence IDs existed without a corresponding raw
  artifact, making provenance unverifiable.
- Root Cause: `_seed_evidence` created an Evidence row from an ID/hash only.
- Files Changed: `stock_agent/runtime.py`, `stock_agent/reporting.py`.
- Fix: every seeded receipt now persists a `SEED_EVIDENCE` RawArtifact first;
  configured/recorded research continues through `RawArtifact → Evidence`, and
  the renderer rejects an active non-derived Evidence row without RawArtifact.
- Regression Test: report receipt-chain test and existing non-SEC provider tests.
- Residual Risk: no live non-SEC source was configured/executed; readiness is NO.

### P0 — live/report path could diverge from strict runtime

- Reproduction: the old candidate report path was outside the strict DAG.
- Root Cause: no report projection invoked from the runtime CLI.
- Files Changed: `stock_agent/cli.py`, `stock_agent/reporting.py`.
- Fix: `--report-output` renders only from the just-completed run ID; strict mode
  fails closed if the report cannot be rendered.
- Regression Test: fresh strict HUNT report and execution report generation.
- Residual Risk: live provider execution remains NOT_RUN.

### P1 — SEC cheap-facts keyword false positives

- Reproduction: generic/historical convertible/warrant language and terminated
  ATM language could become TRUE.
- Root Cause: broad regex/XBRL-positive logic did not require current economic
  terms or distinguish historical instruments.
- Files Changed: `stock_agent/adapters.py`.
- Fix: toxic convertible and material warrant require current/economic context;
  historical/repaid/terminated signals remain UNKNOWN. Coverage now records
  accession, form, primary document, rule ID/version, matched-window hash and
  tri-state. Imminent financing requires an announced/committed offering signal.
- Regression Test: `test_historical_convertible_keyword_does_not_become_toxic_true`;
  existing positive structured filing test.
- Residual Risk: live filing history and capacity follow-up are not executed.

### P1 — incomplete Full SEC document

- Reproduction: an index artifact could satisfy the document/accession check.
- Root Cause: `validate_sec_artifacts` accepted `SEC_FILINGS_INDEX` as a fallback.
- Files Changed: `stock_agent/gates.py`.
- Fix: index-only artifacts are rejected; document body must be non-empty and
  accession-backed.
- Regression Test: `test_index_only_sec_document_is_not_full_forensic_complete`.
- Residual Risk: required-section depth still depends on provider payload quality.

### P1 — MarketContext labels could grant PASS

- Reproduction: `complete=false` with regime/breadth/volatility labels passed via
  an `all(labels)` fallback.
- Root Cause: caller labels were conflated with deterministic completeness.
- Files Changed: `stock_agent/runtime.py`.
- Fix: strict runtime accepts a verified complete flag or derives completeness
  from raw series with the deterministic normalizer; labels alone fail closed.
- Regression Test: `test_labels_without_verified_market_completeness_fail_closed`.
- Residual Risk: live Toss breadth/asset coverage is NOT_RUN.

### P1 — no persisted discovery funnel

- Reproduction: prior output could not reconstruct universe-to-pool counts.
- Root Cause: no SQLite funnel table or runtime writes.
- Files Changed: `stock_agent/store.py`, `stock_agent/runtime.py`,
  `stock_agent/reporting.py`.
- Fix: `discovery_funnel` records raw universe, price/market-cap/ADV checks,
  sector/industry, stage, capital, research, SEC, audit, expectation-gap and
  qualified-pool counts per run.
- Regression Test: fresh HUNT report includes the funnel ledger.
- Residual Risk: recorded acceptance has a one-security universe; broad live
  universe verification is NOT_RUN.

### P1 — expectation gap and portfolio receipt provenance

- Reproduction: HUNT had no persisted reverse-valuation observation, and strict
  execution kept the portfolio snapshot receipt only in transient state.
- Root Cause: no Python HUNT expectation-gap artifact and no portfolio Evidence
  row bound to the execution context.
- Files Changed: `stock_agent/runtime.py`, `stock_agent/reporting.py`.
- Fix: evidence-linked Python `EXPECTATION_GAP` artifacts are persisted when a
  complete scenario exists; otherwise the funnel records UNKNOWN. Strict
  execution persists `E-PORTFOLIO_SNAPSHOT:<hash>` and includes a
  `PositionSnapshotReceiptV2` with artifact/evidence/timestamp lineage in the
  allocation context.
- Regression Test: fresh strict Execution Review ledger and report.
- Residual Risk: live economic scenario and live read-only portfolio are NOT_RUN.

## Readiness

| Capability | Result |
|---|---|
| STRICT_RUNTIME_CONTRACT_READY | YES (recorded acceptance) |
| AUTHORITATIVE_REPORTING_READY | YES (run-bound reports) |
| LIVE_DISCOVERY_PIPELINE_READY | NO — live provider NOT_RUN |
| LIVE_SEC_FORENSICS_READY | NO — live SEC NOT_RUN |
| LIVE_NON_SEC_EVIDENCE_READY | NO — live source NOT_RUN |
| LIVE_MARKET_CONTEXT_READY | NO — live Toss NOT_RUN |
| LIVE_HUNT_ONLY_READY | NO |
| LIVE_EXECUTION_REVIEW_READY | NO |
| PRODUCTION_READY | NO |

No broker/order/write API was called.

